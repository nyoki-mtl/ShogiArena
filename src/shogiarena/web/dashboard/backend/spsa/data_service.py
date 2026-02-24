"""SPSA data service for querying and aggregating SPSA data."""

from __future__ import annotations

import logging
import math
import statistics
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from typing import Any, cast

from sqlalchemy import func, or_, select
from sqlalchemy.orm import aliased

from shogiarena.db import ShogiRepository
from shogiarena.db.models import Game, Player
from shogiarena.utils.types.coerce import coerce_int

from .event_service import SpsaEventService
from .store import SpsaStore
from .types import (
    ConvergenceAnalysis,
    ConvergenceMetrics,
    ConvergencePrediction,
    CorrelationAnalysis,
    GameListEntry,
    MobilitySeriesPayload,
    ParameterTimelineEntry,
    ProgressSnapshot,
    UpdateDetailResponse,
    UpdateEntry,
)
from .utils import coerce_float, extract_variant_from_game_id, resolve_variant_id

logger = logging.getLogger(__name__)


class SpsaDataService:
    """Service for querying SPSA data from both the database and event logs."""

    def __init__(
        self,
        store: SpsaStore,
        ensure_shogidb: Callable[[], None],
        shogidb_supplier: Callable[[], ShogiRepository | None],
    ) -> None:
        self._store = store
        self._ensure_shogidb = ensure_shogidb
        self._get_shogidb = shogidb_supplier
        self._events = SpsaEventService(
            store=store,
            ensure_shogidb=ensure_shogidb,
            shogidb_supplier=shogidb_supplier,
        )

    # ------------------------------------------------------------------
    # Event-backed helpers (delegated to SpsaEventService)
    # ------------------------------------------------------------------
    def build_update_detail(self, idx: int) -> UpdateDetailResponse:
        return self._events.build_update_detail(idx)

    def get_game_event_snapshot(self, game_id: str) -> dict[str, Any] | None:
        return self._events.get_game_event_snapshot(game_id)

    def collect_game_id_entries(self) -> list[tuple[str, int]]:
        return self._events.collect_game_id_entries()

    def load_index_updates(self) -> list[UpdateEntry]:
        return self._events.load_index_updates()

    def collect_updates_from_events(self) -> list[UpdateEntry]:
        return self._events.collect_updates_from_events()

    def compute_progress_snapshot(self, updates: Sequence[Mapping[str, Any]] | None = None) -> ProgressSnapshot:
        """Return completed update count and configured total updates."""

        if updates is None:
            updates = self.load_index_updates()

        completed = len(updates)
        total_updates = None
        try:
            meta = self._store.load_meta_data()
        except OSError:
            meta = None
        if meta is not None:
            total_updates = meta.effective_num_updates
        percent = None
        if total_updates and total_updates > 0:
            percent = completed / total_updates

        return ProgressSnapshot(
            completed=completed,
            total=total_updates,
            percent=percent,
        )

    # ------------------------------------------------------------------
    # Game listings (database first, fall back to events)
    # ------------------------------------------------------------------
    def list_games(
        self,
        offset: int,
        limit: int,
        search_query: str,
    ) -> tuple[list[GameListEntry], int]:
        self._ensure_shogidb()
        shogidb = self._get_shogidb()

        if shogidb is not None:
            session = shogidb.session
            Black = aliased(Player)
            White = aliased(Player)

            count_stmt = (
                select(func.count())
                .select_from(Game)
                .join(Black, Game.black_player)
                .join(White, Game.white_player)
                .where(Game.game_type == "spsa")
            )
            if search_query:
                like = f"%{search_query}%"
                count_stmt = count_stmt.where(
                    or_(
                        Game.game_name.like(like),
                        Black.player_name.like(like),
                        White.player_name.like(like),
                    )
                )

            total = session.execute(count_stmt).scalar_one()
            if total:
                data_stmt = (
                    select(
                        Game.game_name,
                        Black.player_name.label("black_player"),
                        White.player_name.label("white_player"),
                        Game.result_code,
                        Game.num_moves,
                        Game.end_date,
                        Game.init_position_sfen,
                        Game.time_control_black,
                        Game.time_control_white,
                    )
                    .join(Black, Game.black_player)
                    .join(White, Game.white_player)
                    .where(Game.game_type == "spsa")
                )
                if search_query:
                    like = f"%{search_query}%"
                    data_stmt = data_stmt.where(
                        or_(
                            Game.game_name.like(like),
                            Black.player_name.like(like),
                            White.player_name.like(like),
                        )
                    )
                data_stmt = (
                    data_stmt.order_by(Game.end_date.desc().nullslast(), Game.id.desc()).limit(limit).offset(offset)
                )

                rows = session.execute(data_stmt).all()
                games: list[GameListEntry] = []
                for (
                    game_name,
                    black_player,
                    white_player,
                    result_code,
                    num_moves,
                    end_date,
                    init_sfen,
                    time_control_black,
                    time_control_white,
                ) in rows:
                    event_meta = self.get_game_event_snapshot(game_name) or {}
                    phase_value = event_meta.get("phase")
                    update_idx_value = event_meta.get("update_idx")
                    resolved_variant = resolve_variant_id(update_idx_value)
                    if update_idx_value is None:
                        resolved_variant = extract_variant_from_game_id(game_name) or resolved_variant
                    games.append(
                        {
                            "game_id": game_name,
                            "black_player": black_player,
                            "white_player": white_player,
                            "result_code": int(result_code) if result_code is not None else None,
                            "total_plies": int(num_moves) if num_moves is not None else None,
                            "end_time": end_date.isoformat() if end_date else None,
                            "initial_sfen": init_sfen,
                            "time_control_black": time_control_black,
                            "time_control_white": time_control_white,
                            "variant_id": resolved_variant,
                            "phase": phase_value if isinstance(phase_value, str) else None,
                        }
                    )

                return games, int(total)

        # Database unavailable or empty -> fall back to events
        game_entries = self.collect_game_id_entries()
        if not game_entries:
            return [], 0

        seen_ids: set[str] = set()
        ordered_ids: list[tuple[str, int]] = []
        for gid, ts in game_entries:
            if gid in seen_ids:
                continue
            seen_ids.add(gid)
            ordered_ids.append((gid, ts))

        query_lower = search_query.lower() if search_query else ""
        records: list[GameListEntry] = []
        for gid, ts in ordered_ids:
            entry = self.get_game_event_snapshot(gid)
            if entry is None:
                if query_lower and query_lower not in gid.lower():
                    continue
                records.append({"game_id": gid, "timestamp": ts, "variant_id": extract_variant_from_game_id(gid)})
                continue

            if query_lower:
                candidates = [
                    gid.lower(),
                    (entry.get("black_player") or "").lower(),
                    (entry.get("white_player") or "").lower(),
                ]
                if not any(query_lower in value for value in candidates if value):
                    continue

            end_time = entry.get("end_time")
            if isinstance(end_time, int | float):
                end_time = datetime.fromtimestamp(float(end_time) / 1000.0, tz=timezone.utc).isoformat()

            update_idx_value = entry.get("update_idx")
            resolved_variant = resolve_variant_id(update_idx_value)
            if update_idx_value is None:
                resolved_variant = extract_variant_from_game_id(gid) or resolved_variant

            record = {
                "game_id": gid,
                "black_player": entry.get("black_player"),
                "white_player": entry.get("white_player"),
                "result_code": entry.get("result_code")
                if entry.get("result_code") is not None
                else entry.get("winner"),
                "total_plies": entry.get("num_moves"),
                "end_time": end_time,
                "initial_sfen": entry.get("initial_sfen"),
                "time_control_black": entry.get("time_control_black"),
                "time_control_white": entry.get("time_control_white"),
                "variant_id": resolved_variant,
                "phase": entry.get("phase"),
                "timestamp": ts,
            }
            records.append(cast(GameListEntry, record))

        records.sort(key=lambda item: item.get("timestamp") or 0, reverse=True)
        total = len(records)
        paginated = records[offset : offset + limit]
        for item in paginated:
            item.pop("timestamp", None)

        return paginated, total

    # ------------------------------------------------------------------
    # Analysis helpers
    # ------------------------------------------------------------------
    def compute_correlation_analysis(self, updates: list[UpdateEntry]) -> CorrelationAnalysis:
        if len(updates) < 2:
            return CorrelationAnalysis(
                correlations={},
                parameter_evolution={},
                gradient_evolution={},
                step_evolution=[],
                parameter_names=[],
                num_updates=len(updates),
                parameter_timeline={},
                message="Insufficient data for correlation analysis",
            )

        # Normalize updates: sort by update_idx and ensure initial baseline
        normalized_updates: list[dict[str, Any]] = []
        update_map: dict[int, dict[str, Any]] = {}
        initial_params: dict[str, float] = {}

        def normalize_non_negative_idx(value: Any) -> int | None:
            if isinstance(value, bool):
                return int(value)
            if isinstance(value, int | float) and math.isfinite(value):
                return max(0, int(value))
            try:
                parsed = float(value) if value is not None else None
            except (TypeError, ValueError):
                return None
            if parsed is None or not math.isfinite(parsed):
                return None
            return max(0, int(parsed))

        for update in updates:
            idx_raw = update.get("update_idx")
            if isinstance(idx_raw, int):
                idx = idx_raw
            elif isinstance(idx_raw, float) and math.isfinite(idx_raw):
                idx = int(idx_raw)
            else:
                continue

            params = update.get("params", {})
            if isinstance(params, dict):
                # Extract initial params from update_idx = -1 or 0
                if idx == -1 or idx == 0:
                    for name, value in params.items():
                        if (coerced := coerce_float(value)) is not None:
                            initial_params[name] = coerced

            # Store the latest entry for each update_idx
            if idx not in update_map:
                update_map[idx] = {}
            existing = update_map[idx]
            # Merge updates, preferring later entries
            update_map[idx] = {**existing, **update}

        # Build sorted list of updates
        sorted_indices = sorted(update_map.keys())
        for idx in sorted_indices:
            normalized_updates.append(update_map[idx])

        # If no initial_params found (no update_idx=-1 or 0), use first update
        # as a provisional baseline. This will be overridden by meta.json's
        # initial_params (if available) so that correlation analysis stays in
        # sync with the parameter summary API.
        if not initial_params and normalized_updates:
            first_update = normalized_updates[0]
            params = first_update.get("params", {})
            if isinstance(params, dict):
                for name, value in params.items():
                    if isinstance(value, int | float) and math.isfinite(value):
                        initial_params[name] = float(value)

        # Align initial_params with SPSA meta so that the baseline used for the
        # Parameter Detail chart (#0) matches the initial parameter snapshot
        # exposed via the params API. The meta-provided values take precedence
        # over any heuristics derived from the update stream.
        try:
            meta_data = self._store.load_meta_data()
        except OSError:
            meta_data = None
        if meta_data is not None:
            # meta.json is the source of truth; overwrite any
            # conflicting value collected from updates.
            initial_params.update(meta_data.initial_params)

        # Build parameter timeline with baseline tracking
        param_names: list[str] = sorted({name for update in normalized_updates for name in update.get("params", {})})
        if initial_params:
            param_names.extend(k for k in initial_params.keys() if k not in param_names)
            param_names = sorted(param_names)

        # Track baseline values per parameter
        # First pass: build baseline snapshots for all updates. We keep two
        # flavours of snapshots:
        # - baseline_snapshots: baseline *before* applying the update at idx
        #   (used for computing deltas for that idx),
        # - baseline_committed_snapshots: baseline *after* applying the update
        #   at idx (used as the revert target for LTC rejections).
        baseline_snapshots: dict[int, dict[str, float]] = {}
        baseline_committed_snapshots: dict[int, dict[str, float]] = {}
        current_baseline: dict[str, float] = initial_params.copy()
        # Track LTC rejection ranges as (baseline_idx, rejected_idx)
        ltc_rejection_ranges: list[tuple[int, int]] = []
        # Track indices where LTC was accepted (these should never be greyed out)
        ltc_accepted_indices: set[int] = set()

        # Initialize baseline snapshot for the initial state
        if sorted_indices and current_baseline:
            first_idx = sorted_indices[0]
            baseline_snapshots[first_idx] = current_baseline.copy()
            baseline_committed_snapshots[first_idx] = current_baseline.copy()

        # First pass: build baseline snapshots and record LTC rejection ranges.
        for update in normalized_updates:
            idx_raw = update.get("update_idx")
            if isinstance(idx_raw, int):
                idx = idx_raw
            elif isinstance(idx_raw, float) and math.isfinite(idx_raw):
                idx = int(idx_raw)
            else:
                continue

            # Store baseline snapshot at start of this update
            baseline_snapshots[idx] = current_baseline.copy()

            # Check if this update was LTC rejected or accepted
            ltc_rejected = bool(update.get("ltc_rejected", False))
            ltc_reverted_to_idx = normalize_non_negative_idx(update.get("ltc_reverted_to"))

            # Track LTC accepted points (these should never be greyed out)
            ltc_info = update.get("ltc_regression")
            if isinstance(ltc_info, Mapping) and ltc_info.get("accepted") is True:
                ltc_accepted_indices.add(idx)

            # If LTC rejected, restore baseline from the reverted index.
            # The reverted index represents the committed baseline that this
            # regression compared against.
            if ltc_rejected and ltc_reverted_to_idx is not None:
                baseline_idx = ltc_reverted_to_idx
                ltc_rejection_ranges.append((baseline_idx, idx))
                if baseline_idx == 0:
                    current_baseline = initial_params.copy()
                else:
                    reverted_baseline = baseline_committed_snapshots.get(baseline_idx) or baseline_snapshots.get(
                        baseline_idx
                    )
                    if reverted_baseline is not None:
                        current_baseline = reverted_baseline.copy()
            else:
                # Update baseline for accepted updates (not pending, not LTC rejected)
                params = update.get("params", {})
                if isinstance(params, dict) and not update.get("pending", False):
                    for name, value in params.items():
                        if (coerced := coerce_float(value)) is not None:
                            current_baseline[name] = coerced

            # Store committed baseline snapshot *after* applying the update's
            # effect so that subsequent updates and LTC reverts can reference
            # the correct committed state.
            baseline_committed_snapshots[idx] = current_baseline.copy()

        # Compute indices invalidated by LTC rejections. For each rejection at
        # R with baseline B, the half-open interval [B, R) is considered invalidated,
        # except for indices where LTC was accepted (those remain valid baselines).
        # Additionally, reject decision points that serve as baselines for subsequent
        # rejections are also invalidated (they were rolled back by the later reject).
        invalidated_indices: set[int] = set()

        # Identify reject decision points that have subsequent rejections
        # (i.e., they appear as baseline_idx in another rejection range)
        superseded_reject_points: set[int] = set()
        all_reject_points = {r for _, r in ltc_rejection_ranges}
        for baseline_idx, _ in ltc_rejection_ranges:
            if baseline_idx in all_reject_points:
                superseded_reject_points.add(baseline_idx)

        for baseline_idx, rejected_idx in ltc_rejection_ranges:
            if not isinstance(baseline_idx, int) or not isinstance(rejected_idx, int):
                continue
            if rejected_idx <= baseline_idx:
                continue
            for idx in sorted_indices:
                # Include baseline_idx in the invalidated range (half-open interval)
                # but exclude LTC-accepted points which are always valid baselines
                if baseline_idx <= idx < rejected_idx and idx not in ltc_accepted_indices:
                    invalidated_indices.add(idx)

        # Superseded reject decision points are also invalidated
        invalidated_indices.update(superseded_reject_points - ltc_accepted_indices)

        # Second pass: build timeline entries using baseline snapshots
        parameter_timeline: dict[str, list[ParameterTimelineEntry]] = {name: [] for name in param_names}

        for update in normalized_updates:
            idx_raw = update.get("update_idx")
            if isinstance(idx_raw, int):
                idx = idx_raw
            elif isinstance(idx_raw, float) and math.isfinite(idx_raw):
                idx = int(idx_raw)
            else:
                continue

            params = update.get("params", {})
            if not isinstance(params, dict):
                continue

            # Get baseline for this update
            baseline = baseline_snapshots.get(idx, current_baseline.copy())

            # Check if this update was LTC rejected (for point-level rollback),
            # and whether it falls inside an LTC-invalidated interval.
            raw_ltc_rejected = bool(update.get("ltc_rejected", False))
            ltc_reverted_to_idx = normalize_non_negative_idx(update.get("ltc_reverted_to"))
            # "ltc_invalidated" marks variants whose segments should be greyed
            # out because they lie strictly between the baseline and the
            # rejected variant.
            ltc_invalidated = idx in invalidated_indices
            ltc_decision: str | None = None
            ltc_info = update.get("ltc_regression")
            if isinstance(ltc_info, Mapping):
                accepted = ltc_info.get("accepted")
                if accepted is True:
                    ltc_decision = "accepted"
                elif accepted is False:
                    ltc_decision = "rejected"
            elif raw_ltc_rejected:
                ltc_decision = "rejected"

            # Build timeline entries for each parameter
            for name in param_names:
                raw_param_value = params.get(name)
                param_value = coerce_float(raw_param_value)
                if param_value is None:
                    continue

                baseline_value = baseline.get(name)
                if baseline_value is None:
                    baseline_value = param_value

                # For the rejected update itself, the "actual" value should
                # reflect the reverted baseline (delta=0), not the temporary
                # trial value that was rejected. Use the explicit
                # ltc_reverted_to index when available so that the rollback
                # target matches the effective LTC baseline:
                # - ltc_reverted_to == 0 → initial params (#0),
                # - otherwise           → committed baseline snapshot at that idx.
                if raw_ltc_rejected and ltc_reverted_to_idx is not None:
                    baseline_idx = ltc_reverted_to_idx
                    if baseline_idx == 0:
                        revert_source = initial_params
                    else:
                        revert_source = (
                            baseline_committed_snapshots.get(baseline_idx) or baseline_snapshots.get(baseline_idx) or {}
                        )
                    reverted_value = coerce_float(revert_source.get(name))
                    if reverted_value is not None:
                        baseline_value = reverted_value
                    effective_actual = float(baseline_value)
                else:
                    effective_actual = float(param_value)

                parameter_timeline[name].append(
                    ParameterTimelineEntry(
                        update_idx=idx,
                        actual=effective_actual,
                        baseline=float(baseline_value),
                        pending=bool(update.get("pending", False)),
                        ltc_invalidated=ltc_invalidated,
                        ltc_decision=ltc_decision,
                    )
                )

        def resolve_baseline_for_parameter(name: str) -> float | None:
            if (initial_value := coerce_float(initial_params.get(name))) is not None:
                return initial_value

            for idx in sorted_indices:
                snapshot = baseline_snapshots.get(idx)
                if not snapshot:
                    continue
                if (snapshot_value := coerce_float(snapshot.get(name))) is not None:
                    return snapshot_value

            for update in normalized_updates:
                params = update.get("params", {})
                if not isinstance(params, dict):
                    continue
                if (raw_value := coerce_float(params.get(name))) is not None:
                    return raw_value
            return None

        def normalize_update_idx(value: Any) -> int:
            return coerce_int(value) or 0

        for name in param_names:
            entries = parameter_timeline.get(name)
            if entries is None:
                continue
            entries.sort(key=lambda item: normalize_update_idx(item.get("update_idx")))
            has_baseline_entry = any(normalize_update_idx(item.get("update_idx")) <= 0 for item in entries)
            if not has_baseline_entry:
                baseline_value = resolve_baseline_for_parameter(name)
                if baseline_value is not None:
                    entries.insert(
                        0,
                        ParameterTimelineEntry(
                            update_idx=0,
                            actual=baseline_value,
                            baseline=baseline_value,
                            pending=False,
                            ltc_invalidated=False,
                            ltc_decision=None,
                        ),
                    )
            parameter_timeline[name] = entries

        # Build evolution arrays (for backward compatibility)
        param_evolution: dict[str, list[float]] = {name: [] for name in param_names}
        gradient_evolution: dict[str, list[float]] = {name: [] for name in param_names}
        step_evolution: list[float] = []

        for update in normalized_updates:
            params = update.get("params", {})
            grads = update.get("gradients", {})
            step_evolution.append(update.get("step", 0))
            for name in param_names:
                param_evolution[name].append(params.get(name, 0) if isinstance(params, dict) else 0)
                gradient_evolution[name].append(grads.get(name, 0) if isinstance(grads, dict) else 0)

        # Compute correlations
        correlations: dict[str, float] = {}
        for index, param1 in enumerate(param_names):
            for param2 in param_names[index + 1 :]:
                values1 = param_evolution[param1]
                values2 = param_evolution[param2]
                if len(values1) >= 2 and len(values2) >= 2:
                    count = len(values1)
                    sum1 = sum(values1)
                    sum2 = sum(values2)
                    sum1_sq = sum(value * value for value in values1)
                    sum2_sq = sum(value * value for value in values2)
                    sum12 = sum(x * y for x, y in zip(values1, values2, strict=False))
                    numerator = count * sum12 - sum1 * sum2
                    term1 = count * sum1_sq - sum1 * sum1
                    term2 = count * sum2_sq - sum2 * sum2
                    if term1 <= 0 or term2 <= 0:
                        continue
                    denominator = math.sqrt(term1 * term2)
                    if denominator != 0:
                        correlations[f"{param1}_{param2}"] = numerator / denominator

        return CorrelationAnalysis(
            correlations=correlations,
            parameter_evolution=param_evolution,
            gradient_evolution=gradient_evolution,
            step_evolution=step_evolution,
            parameter_names=param_names,
            num_updates=len(normalized_updates),
            parameter_timeline=parameter_timeline,
        )

    def compute_convergence_analysis(self, updates: list[UpdateEntry]) -> ConvergenceAnalysis:
        total_updates_observed = len(updates)

        def _extract_delta_vector(entry: Mapping[str, Any]) -> dict[str, float] | None:
            raw_vector = entry.get("deltas")
            if not isinstance(raw_vector, dict):
                return None
            vector: dict[str, float] = {}
            for key, value in raw_vector.items():
                coerced = coerce_float(value)
                if coerced is None or not math.isfinite(coerced):
                    continue
                vector[str(key)] = float(coerced)
            return vector or None

        def _extract_numeric_values(entry: Mapping[str, Any]) -> tuple[float, float, dict[str, float] | None] | None:
            delta_value = coerce_float(entry.get("delta_norm"))
            step_value = coerce_float(entry.get("step"))
            delta_vector = _extract_delta_vector(entry)
            if delta_value is None or step_value is None:
                return None
            if not math.isfinite(delta_value) or not math.isfinite(step_value):
                return None
            return float(delta_value), float(step_value), delta_vector

        completed_updates: list[tuple[Mapping[str, Any], float, float, dict[str, float] | None]] = []
        pending_updates = 0
        for update in updates:
            extracted = _extract_numeric_values(update)
            if extracted is None:
                pending_updates += 1
                continue
            completed_updates.append((update, extracted[0], extracted[1], extracted[2]))

        num_updates_available = len(completed_updates)

        def _compute_trend(series: Sequence[float]) -> tuple[float, float, float]:
            if not series:
                return 0.0, 0.0, 0.0
            mean_val = statistics.mean(series)
            std_val = statistics.stdev(series) if len(series) > 1 else 0.0
            if len(series) > 1:
                x_vals = list(range(len(series)))
                x_mean = statistics.mean(x_vals)
                y_mean = mean_val
                numerator = sum((x - x_mean) * (y - y_mean) for x, y in zip(x_vals, series, strict=False))
                denominator = sum((x - x_mean) ** 2 for x in x_vals)
                slope = numerator / denominator if denominator != 0 else 0.0
            else:
                slope = 0.0
            return mean_val, std_val, slope

        def _estimate_probability_confidence(slope: float, has_observation: bool) -> tuple[float, float]:
            confidence = max(0.0, min(100.0, (1 - abs(slope)) * 100.0))
            if slope < 0:
                probability = max(0.0, min(1.0, (1 - abs(slope)) * 0.8))
            else:
                probability = 0.1 if has_observation else 0.0
            return probability, confidence

        update_indices_all: list[int] = []
        probability_series_all: list[float] = []
        confidence_series_all: list[float] = []
        delta_series_all: list[float] = []
        mean_vector_norm_history: list[float | None] = []
        step_series_all: list[float] = []
        mobility_gain_ak: list[float] = []
        mobility_indices: list[int] = []
        recent_window = 20

        def _compute_mean_vector_norm(vectors: Sequence[dict[str, float]]) -> float:
            if not vectors:
                return 0.0
            component_sums: dict[str, float] = {}
            component_counts: dict[str, int] = {}
            for vector in vectors:
                for key, value in vector.items():
                    component_sums[key] = component_sums.get(key, 0.0) + value
                    component_counts[key] = component_counts.get(key, 0) + 1
            if not component_sums:
                return 0.0
            mean_components = [component_sums[key] / component_counts[key] for key in component_sums]
            return math.sqrt(sum(value * value for value in mean_components))

        vector_window: list[dict[str, float] | None] = []

        for entry, delta_value, step_value, delta_vector in completed_updates:
            raw_idx = entry.get("update_idx")
            if isinstance(raw_idx, int):
                idx_value = raw_idx
            elif isinstance(raw_idx, float) and math.isfinite(raw_idx):
                idx_value = int(raw_idx)
            else:
                idx_value = len(update_indices_all)
            update_indices_all.append(idx_value)
            delta_series_all.append(delta_value)
            step_series_all.append(step_value)
            gain_ak = coerce_float(entry.get("a_k") or entry.get("gain_ak") or entry.get("gainAk"))
            if gain_ak is not None and math.isfinite(gain_ak):
                mobility_gain_ak.append(float(gain_ak))
                mobility_indices.append(idx_value)

            window = delta_series_all[-recent_window:]
            mean_val, std_val, slope = _compute_trend(window)
            probability, confidence = _estimate_probability_confidence(slope, bool(window))
            probability_series_all.append(probability)
            confidence_series_all.append(confidence)

            vector_window.append(delta_vector)
            if len(vector_window) > recent_window:
                vector_window.pop(0)
            non_null_vectors = [vector for vector in vector_window if vector]
            if non_null_vectors:
                mean_vector_norm_history.append(_compute_mean_vector_norm(non_null_vectors))
            else:
                mean_vector_norm_history.append(None)

        recent_updates = completed_updates[-100:] if num_updates_available >= 100 else completed_updates
        recent_len = len(recent_updates)
        recent_indices = update_indices_all[-recent_len:] if recent_len else []
        probability_history = probability_series_all[-recent_len:] if recent_len else []
        confidence_history = confidence_series_all[-recent_len:] if recent_len else []

        delta_norms: list[float] = [delta for _, delta, _, _ in recent_updates]
        step_sizes: list[float] = [step for _, _, step, _ in recent_updates]
        if recent_len:
            vector_norms_recent = [value for value in mean_vector_norm_history[-recent_len:] if value is not None]
        else:
            vector_norms_recent = []

        min_delta_samples = 1
        available_delta_samples = len(delta_norms)
        history_payload = {
            "convergence_probability_history": probability_history,
            "convergence_confidence_history": confidence_history,
            "convergence_history_indices": recent_indices,
        }

        min_delta_samples = max(1, min_delta_samples)
        if available_delta_samples < min_delta_samples:
            return ConvergenceAnalysis(
                convergence_metrics=ConvergenceMetrics(),
                prediction=ConvergencePrediction(),
                required_delta_norms=min_delta_samples,
                available_delta_norms=available_delta_samples,
                available_updates=num_updates_available,
                pending_updates=pending_updates,
                total_updates_observed=total_updates_observed,
                **history_payload,
            )

        convergence_metrics = ConvergenceMetrics()
        prediction = ConvergencePrediction()

        recent_mean_vector_norm: float | None = None
        for value in reversed(mean_vector_norm_history):
            if value is not None:
                recent_mean_vector_norm = value
                break

        if len(delta_norms) >= recent_window:
            window = delta_norms[-recent_window:]
            recent_avg, recent_std, trend_slope = _compute_trend(window)
            probability, confidence = _estimate_probability_confidence(trend_slope, bool(window))
            overall_avg = statistics.mean(delta_norms)
            convergence_metrics = ConvergenceMetrics(
                recent_avg_delta_norm=recent_avg,
                overall_avg_delta_norm=overall_avg,
                recent_std_delta_norm=recent_std,
                trend_slope=trend_slope,
                is_converging=trend_slope < 0 and recent_std < overall_avg * 0.1,
                convergence_confidence=confidence,
            )
            if recent_mean_vector_norm is not None:
                convergence_metrics["recent_mean_vector_delta_norm"] = recent_mean_vector_norm
                if vector_norms_recent:
                    convergence_metrics["recent_avg_mean_vector_delta_norm"] = statistics.mean(vector_norms_recent)
            if trend_slope < 0:
                projected_remaining = max(0.0, recent_avg / max(abs(trend_slope), 1e-6))
                prediction = ConvergencePrediction(
                    remaining_updates_estimate=projected_remaining,
                    convergence_probability=probability,
                )
        else:
            convergence_metrics = ConvergenceMetrics(
                recent_avg_delta_norm=statistics.mean(delta_norms) if delta_norms else 0.0,
                recent_std_delta_norm=statistics.stdev(delta_norms) if len(delta_norms) > 1 else 0.0,
                trend_slope=0.0,
                is_converging=False,
                convergence_confidence=0.0,
            )
            if recent_mean_vector_norm is not None:
                convergence_metrics["recent_mean_vector_delta_norm"] = recent_mean_vector_norm

        prediction.setdefault("convergence_probability", probability_history[-1] if probability_history else 0.0)

        return ConvergenceAnalysis(
            convergence_metrics=convergence_metrics,
            prediction=prediction,
            delta_norm_history=delta_series_all,
            delta_mean_vector_norm_history=mean_vector_norm_history,
            delta_mean_vector_window=recent_window,
            recent_delta_norms=delta_norms[-recent_window:],
            recent_step_sizes=step_sizes[-recent_window:],
            required_delta_norms=min_delta_samples,
            available_delta_norms=available_delta_samples,
            available_updates=num_updates_available,
            pending_updates=pending_updates,
            total_updates_observed=total_updates_observed,
            num_updates_analyzed=num_updates_available,
            mobility_series=MobilitySeriesPayload(
                gain_ak=mobility_gain_ak,
                variant_indices=mobility_indices,
            ),
            **history_payload,
        )
