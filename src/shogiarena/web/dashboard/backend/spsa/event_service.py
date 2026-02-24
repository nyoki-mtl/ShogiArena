"""SPSA event aggregation helpers shared by dashboard services."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from shogiarena.arena.orchestrators.spsa.event_types import SpsaEvent
from shogiarena.db import ShogiRepository
from shogiarena.db.models import Game
from shogiarena.records.storage.db_store import DBRecordStore

from .store import SpsaStore
from .types import (
    GameBriefEntry,
    LtcRegressionDetail,
    UpdateDetailResponse,
    UpdateEntry,
    WdlCounts,
)
from .utils import (
    coerce_float,
    coerce_int,
    extract_variant_from_game_id,
    format_variant_label,
    resolve_variant_id,
    timestamp_to_iso,
)

logger = logging.getLogger(__name__)


class SpsaEventService:
    """Aggregate SPSA events into structured payloads consumed by the API."""

    def __init__(
        self,
        *,
        store: SpsaStore,
        ensure_shogidb: Callable[[], None],
        shogidb_supplier: Callable[[], ShogiRepository | None],
    ) -> None:
        self._store = store
        self._ensure_shogidb = ensure_shogidb
        self._get_shogidb = shogidb_supplier

    @staticmethod
    def _normalize_non_negative_idx(value: object) -> int | None:
        """Normalize update indices to non-negative integers (baseline anchored at #0)."""
        normalized = coerce_int(value)
        if normalized is None:
            return None
        return max(0, normalized)

    def build_update_detail(self, idx: int) -> UpdateDetailResponse:
        """Build detailed information about a specific SPSA update."""

        events = self._store.load_event_entries()
        if not events:
            raise ValueError("no events")

        params: dict[str, float] | None = None
        variant_id: str | None = None
        wins = losses = draws = 0
        game_ids: list[str] = []
        games_meta: dict[str, dict[str, Any]] = {}  # I/O boundary: イベントから集約する中間状態
        games_order: list[str] = []
        ltc_game_ids: list[str] = []
        ltc_games_meta: dict[str, dict[str, Any]] = {}  # I/O boundary: LTC イベントから集約する中間状態
        ltc_games_order: list[str] = []
        ltc_event_count = 0
        ltc_game_event_count = 0
        grads: dict[str, float] | None = None
        deltas: dict[str, float] | None = None
        s_plus: float | None = None
        s_minus: float | None = None
        step: float | None = None
        perturbations: dict[str, Any] | None = None
        gain_c_k: float | None = None
        gain_a_k: float | None = None
        is_pending = True
        phase_wdl: dict[str, WdlCounts] = {
            "plus": WdlCounts(wins=0, losses=0, draws=0),
            "minus": WdlCounts(wins=0, losses=0, draws=0),
        }
        ltc_regression: LtcRegressionDetail | None = None

        def ensure_ltc_detail() -> LtcRegressionDetail:
            nonlocal ltc_regression
            if not isinstance(ltc_regression, dict):
                ltc_regression = LtcRegressionDetail(
                    status="pending",
                    tuned_wins=0,
                    baseline_wins=0,
                    draws=0,
                    total_games=0,
                    total_pairs=None,
                    winrate=None,
                    elo=None,
                    pairs_played=None,
                    accepted=None,
                    baseline_update_idx=None,
                    baseline_variant_token=None,
                    tuned_variant_token=None,
                    started_at=None,
                    completed_at=None,
                    fail_reasons=[],
                    sprt=None,
                    sprt_decision=None,
                )
            return ltc_regression

        def is_ltc_event(event: SpsaEvent) -> bool:
            return event.get("ltc", False) or event.get("family", "").strip().lower() == "ltc"

        def register_game_record(game_id: str) -> dict[str, Any]:
            entry = games_meta.get(game_id)
            if entry is None:
                entry = {"game_id": game_id}
                games_meta[game_id] = entry
                games_order.append(game_id)
            return entry

        def register_ltc_game_record(game_id: str) -> dict[str, Any]:
            entry = ltc_games_meta.get(game_id)
            if entry is None:
                entry = {"game_id": game_id}
                ltc_games_meta[game_id] = entry
                ltc_games_order.append(game_id)
            return entry

        for event in events:
            if event.get("event") == "update_pending" and event.get("update_idx", -1) == idx:
                params_val = event.get("params")
                if params_val is not None:
                    params = dict(params_val)
                    variant_id = format_variant_label(idx)
                perturb_val = event.get("perturbations")
                if perturb_val is not None:
                    perturbations = perturb_val
                if (val := event.get("c_k")) is not None:
                    gain_c_k = val
                if (val := event.get("a_k")) is not None:
                    gain_a_k = val
            elif event.get("event") == "update" and event.get("update_idx", -1) == idx:
                params_val = event.get("params")
                if params_val is not None:
                    params = dict(params_val)
                    variant_id = format_variant_label(idx)

                grads_val = event.get("gradients")
                if grads_val is not None:
                    grads = dict(grads_val)

                deltas_val = event.get("deltas")
                if deltas_val is not None:
                    deltas = dict(deltas_val)

                if (val := event.get("s_plus")) is not None:
                    s_plus = val
                if (val := event.get("s_minus")) is not None:
                    s_minus = val
                if (val := event.get("step")) is not None:
                    step = val
                perturb_val = event.get("perturbations")
                if perturb_val is not None:
                    perturbations = perturb_val
                if (val := event.get("c_k")) is not None:
                    gain_c_k = val
                if (val := event.get("a_k")) is not None:
                    gain_a_k = val
                is_pending = False
            elif event.get("event") == "update_perturbation" and event.get("update_idx", -1) == idx:
                perturb_val = event.get("perturbations")
                if perturb_val is not None:
                    perturbations = perturb_val
                if (val := event.get("c_k")) is not None:
                    gain_c_k = val
                if (val := event.get("a_k")) is not None:
                    gain_a_k = val

            elif event.get("event") == "ltc_regression_start" and event.get("update_idx", -1) == idx:
                ltc_event_count += 1
                ltc_entry = ensure_ltc_detail()
                ltc_entry["status"] = "running"
                if (val := event.get("total_pairs")) is not None:
                    ltc_entry["total_pairs"] = val
                ts = event.get("ts")
                if ts is not None:
                    ltc_entry["started_at"] = ts
                ltc_entry["tuned_wins"] = 0
                ltc_entry["baseline_wins"] = 0
                ltc_entry["draws"] = 0
                ltc_entry["total_games"] = 0
                ltc_entry["pairs_played"] = 0
                ltc_entry["sprt"] = None
                ltc_entry["sprt_decision"] = None

            elif event.get("event") == "ltc_regression_result" and event.get("update_idx", -1) == idx:
                ltc_event_count += 1
                ltc_entry = ensure_ltc_detail()
                status_val = event.get("status")
                if status_val is not None:
                    ltc_entry["status"] = status_val.strip()
                if (winrate_val := event.get("winrate")) is not None:
                    ltc_entry["winrate"] = winrate_val
                if (elo_val := event.get("elo")) is not None:
                    ltc_entry["elo"] = elo_val
                tuned_wins_val = event.get("tuned_wins")
                baseline_wins_val = event.get("baseline_wins")
                draws_val = event.get("draws")
                total_games_val = event.get("total_games")
                ltc_entry["tuned_wins"] = tuned_wins_val if tuned_wins_val is not None else 0
                ltc_entry["baseline_wins"] = baseline_wins_val if baseline_wins_val is not None else 0
                ltc_entry["draws"] = draws_val if draws_val is not None else 0
                if total_games_val is not None:
                    ltc_entry["total_games"] = total_games_val
                else:
                    ltc_entry["total_games"] = (
                        ltc_entry.get("tuned_wins", 0) + ltc_entry.get("baseline_wins", 0) + ltc_entry.get("draws", 0)
                    )
                if (val := event.get("pairs_played")) is not None:
                    ltc_entry["pairs_played"] = val
                if (accepted_val := event.get("accepted")) is not None:
                    ltc_entry["accepted"] = accepted_val
                baseline_idx_val = event.get("baseline_update_idx")
                normalized_baseline_idx = self._normalize_non_negative_idx(baseline_idx_val)
                if normalized_baseline_idx is not None:
                    ltc_entry["baseline_update_idx"] = normalized_baseline_idx
                if (val := event.get("baseline_variant_token")) is not None:
                    ltc_entry["baseline_variant_token"] = val
                if (val := event.get("tuned_variant_token")) is not None:
                    ltc_entry["tuned_variant_token"] = val
                fail_reasons_val = event.get("fail_reasons")
                if fail_reasons_val is not None:
                    ltc_entry["fail_reasons"] = [str(reason) for reason in fail_reasons_val if reason is not None]
                sprt_val = event.get("sprt")
                if isinstance(sprt_val, dict):
                    ltc_entry["sprt"] = sprt_val
                sprt_decision_val = event.get("sprt_decision")
                if sprt_decision_val is not None:
                    ltc_entry["sprt_decision"] = sprt_decision_val
                else:
                    sprt_stored = ltc_entry.get("sprt")
                    if isinstance(sprt_stored, dict):
                        decision = sprt_stored.get("decision")
                        if isinstance(decision, str):
                            ltc_entry["sprt_decision"] = decision
                completed_ts = event.get("ts")
                if completed_ts is not None:
                    ltc_entry["completed_at"] = completed_ts

            elif event.get("event") == "game_result" and event.get("update_idx", -1) == idx:
                if is_ltc_event(event):
                    ltc_event_count += 1
                    ltc_game_event_count += 1
                    ltc_entry = ensure_ltc_detail()
                    if ltc_entry.get("started_at") is None:
                        started_ts = event.get("ts")
                        if started_ts is not None:
                            ltc_entry["started_at"] = started_ts
                    winner = event.get("winner")
                    if winner == 1:
                        ltc_entry["tuned_wins"] = int(ltc_entry.get("tuned_wins", 0)) + 1
                    elif winner == 0:
                        ltc_entry["baseline_wins"] = int(ltc_entry.get("baseline_wins", 0)) + 1
                    else:
                        ltc_entry["draws"] = int(ltc_entry.get("draws", 0)) + 1
                    ltc_entry["total_games"] = int(ltc_entry.get("total_games", 0)) + 1
                    ltc_gid = (event.get("game_id") or "").strip()
                    if ltc_gid:
                        ltc_game_ids.append(ltc_gid)
                        record = register_ltc_game_record(ltc_gid)
                        if (val := event.get("black_player")) is not None:
                            record["black_player"] = val
                        if (val := event.get("white_player")) is not None:
                            record["white_player"] = val
                        record["result_code"] = event.get("result_code")
                        record["num_moves"] = event.get("num_moves")
                        record["status"] = "completed"
                        if (val := event.get("variant_token")) is not None:
                            record["variant_id"] = val
                        if (val := event.get("tuned_variant_token")) is not None:
                            record["variant_token"] = val
                        if (val := event.get("baseline_variant_token")) is not None:
                            record["baseline_variant_token"] = val
                        if (val := event.get("phase")) is not None:
                            record["phase"] = val
                        if (val := event.get("start_time")) is not None:
                            record["start_time"] = val
                        if (val := event.get("end_time")) is not None:
                            record["end_time"] = val
                        if (val := event.get("assigned_instance")) is not None:
                            record["assigned_instance"] = val
                        if (val := event.get("round")) is not None:
                            record["round"] = val
                    continue
                gid = (event.get("game_id") or "").strip()
                if gid:
                    game_ids.append(gid)
                    record = register_game_record(gid)
                    if (val := event.get("black_player")) is not None:
                        record["black_player"] = val
                    if (val := event.get("white_player")) is not None:
                        record["white_player"] = val
                    record["result_code"] = event.get("result_code")
                    record["num_moves"] = event.get("num_moves")
                    record["status"] = "completed"
                    if (val := event.get("end_time")) is not None:
                        record["end_time"] = val
                winner = event.get("winner")
                if winner == 1:
                    wins += 1
                elif winner == 0:
                    losses += 1
                else:
                    draws += 1

                phase_raw = event.get("phase")
                phase_key = phase_raw.strip().lower() if phase_raw else None
                if not phase_key:
                    phase_key = "unknown"
                bucket = phase_wdl.setdefault(phase_key, WdlCounts(wins=0, losses=0, draws=0))
                if winner == 1:
                    bucket["wins"] = bucket.get("wins", 0) + 1
                elif winner == 0:
                    bucket["losses"] = bucket.get("losses", 0) + 1
                else:
                    bucket["draws"] = bucket.get("draws", 0) + 1

            elif event.get("event") == "game_scheduled" and event.get("update_idx", -1) == idx:
                if is_ltc_event(event):
                    ltc_event_count += 1
                    ltc_game_event_count += 1
                    ltc_gid = (event.get("game_id") or "").strip()
                    if not ltc_gid:
                        continue
                    record = register_ltc_game_record(ltc_gid)
                    if (val := event.get("black_player")) is not None:
                        record["black_player"] = val
                    if (val := event.get("white_player")) is not None:
                        record["white_player"] = val
                    if (val := event.get("variant_token")) is not None:
                        record["variant_id"] = val
                    if (val := event.get("tuned_variant_token")) is not None:
                        record["variant_token"] = val
                    if (val := event.get("baseline_variant_token")) is not None:
                        record["baseline_variant_token"] = val
                    if (val := event.get("phase")) is not None:
                        record["phase"] = val
                    if (val := event.get("assigned_instance")) is not None:
                        record["assigned_instance"] = val
                    status_value = (event.get("status") or "pending").strip().lower()
                    record["status"] = status_value or "pending"
                    if (val := event.get("start_time")) is not None:
                        record["start_time"] = val
                    record.setdefault("result_code", None)
                    record.setdefault("num_moves", None)
                    continue
                gid = (event.get("game_id") or "").strip()
                if not gid:
                    continue
                record = register_game_record(gid)
                if (val := event.get("black_player")) is not None:
                    record["black_player"] = val
                if (val := event.get("white_player")) is not None:
                    record["white_player"] = val
                if (val := event.get("variant_token")) is not None:
                    record["variant_id"] = val
                if (val := event.get("phase")) is not None:
                    record["phase"] = val
                if (val := event.get("assigned_instance")) is not None:
                    record["assigned_instance"] = val
                status_value = (event.get("status") or "pending").strip().lower()
                record["status"] = status_value or "pending"
                if (val := event.get("start_time")) is not None:
                    record["start_time"] = val
                record.setdefault("result_code", None)
                record.setdefault("num_moves", None)

        base_name, tuned_name = self._store.read_spsa_engine_names()

        if variant_id is None:
            variant_id = format_variant_label(idx)

        def normalize_variant_token(value: str | None) -> str | None:
            if not value:
                return None
            token = value.strip()
            if len(token) >= 2 and token[0].lower() == "v" and token[1:].isdigit():
                return token.lower()
            return None

        variant_token = normalize_variant_token(variant_id) or resolve_variant_id(idx, allow_base=True)
        ltc_variant_token = None
        if isinstance(ltc_regression, dict):
            tuned_token = ltc_regression.get("tuned_variant_token")
            if isinstance(tuned_token, str) and tuned_token.strip():
                ltc_variant_token = tuned_token.strip()
        ltc_patterns: list[str] = []
        primary_pattern = f"{ltc_variant_token or variant_token}-ltc-%"
        if primary_pattern:
            ltc_patterns.append(primary_pattern)

        self._ensure_shogidb()
        shogidb = self._get_shogidb()

        def hydrate_ltc_records_from_shogidb(db: ShogiRepository, game_ids: list[str]) -> None:
            record_store = DBRecordStore(db)
            for gid in game_ids[:50]:
                game_record = record_store.load(game_name=gid)
                if game_record is None:
                    continue
                record = register_ltc_game_record(gid)
                black_player = game_record.metadata.black_player
                if isinstance(black_player, str):
                    record.setdefault("black_player", black_player)
                white_player = game_record.metadata.white_player
                if isinstance(white_player, str):
                    record.setdefault("white_player", white_player)
                result = game_record.result
                result_code = result.value if result is not None else None
                if result_code is not None:
                    record.setdefault("result_code", result_code)
                record.setdefault("num_moves", len(game_record.moves))
                meta_snapshot = self.get_game_event_snapshot(gid)
                phase_value = meta_snapshot.get("phase") if isinstance(meta_snapshot, dict) else None
                update_idx_value = meta_snapshot.get("update_idx") if isinstance(meta_snapshot, dict) else None
                resolved_variant = resolve_variant_id(update_idx_value)
                if update_idx_value is None:
                    resolved_variant = extract_variant_from_game_id(gid) or resolved_variant
                if resolved_variant:
                    record.setdefault("variant_id", resolved_variant)
                if isinstance(phase_value, str):
                    record.setdefault("phase", phase_value)
                record.setdefault("status", "completed")
                start_time = game_record.metadata.start_date
                if isinstance(start_time, str) and start_time.strip():
                    record.setdefault("start_time", start_time)
                end_time = game_record.metadata.end_date
                if isinstance(end_time, str) and end_time.strip():
                    record.setdefault("end_time", end_time)

        if shogidb is not None and game_ids:
            record_store = DBRecordStore(shogidb)
            for gid in game_ids[:50]:
                game_record = record_store.load(game_name=gid)
                if game_record is None:
                    continue
                record = register_game_record(gid)
                record["black_player"] = game_record.metadata.black_player
                record["white_player"] = game_record.metadata.white_player
                result = game_record.result
                record["result_code"] = result.value if result is not None else None
                record["num_moves"] = len(game_record.moves)
                meta_snapshot = self.get_game_event_snapshot(gid)
                phase_value = meta_snapshot.get("phase") if isinstance(meta_snapshot, dict) else None
                update_idx_value = meta_snapshot.get("update_idx") if isinstance(meta_snapshot, dict) else None
                resolved_variant = resolve_variant_id(update_idx_value)
                if update_idx_value is None:
                    resolved_variant = extract_variant_from_game_id(gid) or resolved_variant
                record["variant_id"] = resolved_variant or record.get("variant_id") or variant_token
                if isinstance(phase_value, str):
                    record["phase"] = phase_value
                record["status"] = record.get("status") or "completed"
                start_time = game_record.metadata.start_date
                if isinstance(start_time, str) and start_time.strip():
                    record["start_time"] = start_time
                end_time = game_record.metadata.end_date
                if isinstance(end_time, str) and end_time.strip():
                    record["end_time"] = end_time

        if shogidb is not None and not ltc_game_ids:
            try:
                session = shogidb.session
                try:
                    session.rollback()
                except SQLAlchemyError:
                    pass
                for pattern in ltc_patterns:
                    query = select(Game.game_name).where(Game.game_type == "spsa", Game.game_name.like(pattern))
                    ltc_game_ids = [row[0] for row in session.execute(query).fetchall()]
                    if ltc_game_ids:
                        break
            except SQLAlchemyError as exc:
                logger.warning("Failed to load LTC games from DB (update=%s): %s", idx, exc)
                ltc_game_ids = []
        if shogidb is not None and ltc_game_ids:
            hydrate_ltc_records_from_shogidb(shogidb, ltc_game_ids)

        games_brief: list[GameBriefEntry] = []
        for gid in games_order:
            record_entry = games_meta.get(gid)
            if record_entry is None:
                continue
            games_brief.append(
                GameBriefEntry(
                    game_id=gid,
                    black_player=record_entry.get("black_player"),
                    white_player=record_entry.get("white_player"),
                    result_code=record_entry.get("result_code"),
                    num_moves=record_entry.get("num_moves"),
                    variant_id=record_entry.get("variant_id") or variant_token,
                    phase=record_entry.get("phase"),
                    status=record_entry.get("status"),
                    assigned_instance=record_entry.get("assigned_instance"),
                    round=record_entry.get("round"),
                    start_time=record_entry.get("start_time"),
                    end_time=record_entry.get("end_time"),
                )
            )

        ltc_games_brief: list[GameBriefEntry] = []
        for gid in ltc_games_order:
            record_entry = ltc_games_meta.get(gid)
            if record_entry is None:
                continue
            ltc_games_brief.append(
                GameBriefEntry(
                    game_id=gid,
                    black_player=record_entry.get("black_player"),
                    white_player=record_entry.get("white_player"),
                    result_code=record_entry.get("result_code"),
                    num_moves=record_entry.get("num_moves"),
                    variant_id=record_entry.get("variant_id"),
                    phase=record_entry.get("phase"),
                    status=record_entry.get("status"),
                    assigned_instance=record_entry.get("assigned_instance"),
                    round=record_entry.get("round"),
                    start_time=record_entry.get("start_time"),
                    end_time=record_entry.get("end_time"),
                )
            )

        wdl = WdlCounts(wins=wins, losses=losses, draws=draws)
        response_data: UpdateDetailResponse = {
            "update_idx": idx,
            "engines": {"baseline": base_name, "tuned": tuned_name},
            "wdl": wdl,
            "variant_id": variant_id,
            "params": params,
            "gradients": grads,
            "deltas": deltas,
            "s_plus": s_plus,
            "s_minus": s_minus,
            "step": step,
            "perturbations": perturbations,
            "c_k": gain_c_k,
            "a_k": gain_a_k,
            "is_pending": is_pending,
            "games": games_brief,
            "games_count": len(games_brief),
            "ltc_games": ltc_games_brief,
            "ltc_games_count": len(ltc_games_brief),
            "phase_wdl": phase_wdl,
            "ltc_regression": ltc_regression,
            "has_ltc_regression": bool(ltc_regression),
        }
        payload_data = {
            "update_idx": response_data["update_idx"],
            "engines": response_data["engines"],
            "wdl": response_data["wdl"],
            "variant_id": response_data["variant_id"],
            "params": response_data["params"],
            "gradients": response_data["gradients"],
            "deltas": response_data["deltas"],
            "s_plus": response_data["s_plus"],
            "s_minus": response_data["s_minus"],
            "step": response_data["step"],
            "perturbations": response_data["perturbations"],
            "c_k": response_data["c_k"],
            "a_k": response_data["a_k"],
            "is_pending": response_data["is_pending"],
            "games_count": response_data["games_count"],
            "ltc_games": response_data["ltc_games"],
            "ltc_games_count": response_data["ltc_games_count"],
            "phase_wdl": response_data["phase_wdl"],
            "ltc_regression": response_data["ltc_regression"],
            "has_ltc_regression": response_data["has_ltc_regression"],
        }
        response_data["payload"] = payload_data
        return response_data

    def get_game_event_snapshot(self, game_id: str) -> dict[str, Any] | None:
        """Return the most recent event payload for the given game."""

        events = self._store.load_event_entries()
        if not events:
            return None
        normalized = str(game_id).strip()
        if not normalized:
            return None
        for event in reversed(events):
            if event.get("event") != "game_result":
                continue
            gid = (event.get("game_id") or "").strip()
            if gid != normalized:
                continue
            payload = dict(event)
            payload.setdefault("black_player", payload.get("black_engine"))
            payload.setdefault("white_player", payload.get("white_engine"))
            payload.setdefault("num_moves", payload.get("moves_count"))
            if "end_time" not in payload:
                ts_val = payload.get("ts")
                end_iso: str | None = None
                if isinstance(ts_val, int):
                    end_iso = datetime.fromtimestamp(float(ts_val) / 1000.0, tz=timezone.utc).isoformat()
                payload["end_time"] = end_iso or ts_val
            return payload
        return None

    def collect_game_id_entries(self) -> list[tuple[str, int]]:
        """Collect game IDs with their latest timestamps from the event log."""

        events = self._store.load_event_entries()
        if not events:
            return []
        latest: dict[str, int] = {}
        for event in events:
            if event.get("event") != "game_result":
                continue
            gid = (event.get("game_id") or "").strip()
            if not gid:
                continue
            ts = event.get("ts") or 0
            prev = latest.get(gid)
            if prev is None or ts > prev:
                latest[gid] = ts
        entries = sorted(latest.items(), key=lambda item: item[1], reverse=True)
        return entries

    def load_index_updates(self) -> list[UpdateEntry]:
        """Merge index.json updates with event log enrichment."""

        entries = [entry for entry in self._store.load_index_updates() if isinstance(entry, dict)]
        if not entries:
            return []

        event_updates = self.collect_updates_from_events()
        event_enriched = {entry.get("update_idx"): entry for entry in event_updates if isinstance(entry, dict)}
        existing_idx: dict[int, UpdateEntry] = {}

        for entry in entries:
            idx = entry.get("update_idx")
            if isinstance(idx, int):
                existing_idx[idx] = entry
            entry.setdefault("has_ltc_regression", False)
            entry.setdefault("ltc_rejected", False)
            entry["variant_id"] = format_variant_label(idx, allow_base=True)
            entry.setdefault("pending", False)
            perturbations = entry.get("perturbations")
            if not isinstance(perturbations, dict):
                entry["perturbations"] = {"plus": {}, "minus": {}}
            enriched = event_enriched.get(idx)
            if enriched:
                for key in ("wins", "losses", "draws"):
                    if key not in entry or entry.get(key) in (None, 0):
                        entry[key] = enriched.get(key)
                if "timestamp" not in entry or entry.get("timestamp") is None:
                    entry["timestamp"] = enriched.get("timestamp")
                if not entry.get("started_at"):
                    entry["started_at"] = enriched.get("started_at")
                if not entry.get("ended_at"):
                    entry["ended_at"] = enriched.get("ended_at")
                if "phase_wdl" not in entry or not entry.get("phase_wdl"):
                    entry["phase_wdl"] = enriched.get("phase_wdl")
                if not entry.get("perturbations"):
                    entry["perturbations"] = enriched.get("perturbations", {"plus": {}, "minus": {}})
                if entry.get("c_k") is None and enriched.get("c_k") is not None:
                    entry["c_k"] = enriched.get("c_k")
                if enriched.get("pending") is True:
                    entry["pending"] = True
                ltc_info = enriched.get("ltc_regression")
                if isinstance(ltc_info, dict) and ltc_info:
                    entry["ltc_regression"] = ltc_info
                if enriched.get("has_ltc_regression"):
                    entry["has_ltc_regression"] = True
                if "ltc_rejected" in enriched:
                    entry["ltc_rejected"] = bool(enriched.get("ltc_rejected"))
                reverted_raw = enriched.get("ltc_reverted_to") if "ltc_reverted_to" in enriched else None
                reverted_idx = self._normalize_non_negative_idx(reverted_raw)
                if reverted_idx is not None:
                    entry["ltc_reverted_to"] = reverted_idx

        for enriched in event_updates:
            idx = enriched.get("update_idx")
            if isinstance(idx, int) and idx not in existing_idx:
                enriched_copy = dict(enriched)
                enriched_copy.setdefault("pending", True)
                if not isinstance(enriched_copy.get("perturbations"), dict):
                    enriched_copy["perturbations"] = {"plus": {}, "minus": {}}
                if "ltc_rejected" in enriched_copy:
                    enriched_copy["ltc_rejected"] = bool(enriched_copy.get("ltc_rejected"))
                else:
                    enriched_copy.setdefault("ltc_rejected", False)
                reverted_idx = self._normalize_non_negative_idx(enriched_copy.get("ltc_reverted_to"))
                if reverted_idx is not None:
                    enriched_copy["ltc_reverted_to"] = reverted_idx
                else:
                    enriched_copy.pop("ltc_reverted_to", None)
                enriched_copy["variant_id"] = format_variant_label(idx, allow_base=True)
                enriched_copy.pop("start_time", None)
                enriched_copy.pop("end_time", None)
                if "has_ltc_regression" not in enriched_copy:
                    enriched_copy["has_ltc_regression"] = bool(enriched_copy.get("ltc_regression"))
                entries.append(enriched_copy)

        return cast(list[UpdateEntry], entries)

    def collect_updates_from_events(self) -> list[UpdateEntry]:
        """Aggregate update entries from events.jsonl."""

        events = self._store.load_event_entries()
        if not events:
            return []

        now_ts = int(time.time() * 1000)
        updates: dict[int, dict[str, Any]] = {}

        def _is_ltc_event(event: SpsaEvent) -> bool:
            return event.get("ltc", False) or event.get("family", "").strip().lower() == "ltc"

        def _ensure_ltc_entry(entry: dict[str, Any]) -> LtcRegressionDetail:
            ltc_info = entry.get("ltc_regression")
            if not isinstance(ltc_info, dict):
                ltc_info = LtcRegressionDetail(
                    status="pending",
                    tuned_wins=0,
                    baseline_wins=0,
                    draws=0,
                    total_games=0,
                    total_pairs=None,
                    winrate=None,
                    elo=None,
                    accepted=None,
                    baseline_update_idx=None,
                    baseline_variant_token=None,
                    tuned_variant_token=None,
                    started_at=None,
                    completed_at=None,
                )
                entry["ltc_regression"] = ltc_info
            entry["has_ltc_regression"] = True
            return ltc_info

        def get_entry(idx: int) -> dict[str, Any]:
            entry = updates.get(idx)
            if entry is None:
                entry = {
                    "update_idx": idx,
                    "timestamp": now_ts,
                    "start_time": None,
                    "end_time": None,
                    "s_plus": None,
                    "s_minus": None,
                    "step": None,
                    "delta_norm": None,
                    "params": {},
                    "gradients": {},
                    "deltas": {},
                    "perturbations": {"plus": {}, "minus": {}},
                    "pending": True,
                    "c_k": None,
                    "a_k": None,
                    "wins": 0,
                    "losses": 0,
                    "draws": 0,
                    "phase_wdl": {
                        "plus": WdlCounts(wins=0, losses=0, draws=0),
                        "minus": WdlCounts(wins=0, losses=0, draws=0),
                    },
                    "has_ltc_regression": False,
                }
                updates[idx] = entry
            return entry

        def register_game(entry: dict[str, Any], game_id: str) -> dict[str, Any]:
            games_meta = entry.setdefault("games_meta", {})
            if not isinstance(games_meta, dict):
                games_meta = {}
                entry["games_meta"] = games_meta
            record = games_meta.get(game_id)
            if record is None:
                record = {"game_id": game_id}
                games_meta[game_id] = record
                games_order = entry.setdefault("games_order", [])
                if not isinstance(games_order, list):
                    games_order = []
                    entry["games_order"] = games_order
                games_order.append(game_id)
            return record

        for event in events:
            idx = event.get("update_idx")  # Already int | None from parser
            if idx is None:
                continue

            entry = get_entry(idx)
            event_type = event.get("event")
            event_ts = event.get("ts")
            if event_ts is None:
                fallback_ts = entry.get("timestamp")
                if not isinstance(fallback_ts, int):
                    fallback_ts = now_ts
                event_ts = fallback_ts
            if event_type == "update":
                entry["timestamp"] = event_ts
                if entry.get("start_time") is None:
                    entry["start_time"] = event_ts
                prev_end = entry.get("end_time")
                entry["end_time"] = max(prev_end, event_ts) if isinstance(prev_end, int) else event_ts
                params_val = event.get("params")
                if params_val is not None:
                    entry["params"] = dict(params_val)
                grads_val = event.get("gradients")
                if grads_val is not None:
                    entry["gradients"] = dict(grads_val)
                deltas_val = event.get("deltas")
                if deltas_val is not None:
                    entry["deltas"] = dict(deltas_val)
                perturb_val = event.get("perturbations")
                if perturb_val is not None:
                    entry["perturbations"] = perturb_val
                if (value := event.get("c_k")) is not None:
                    entry["c_k"] = value
                if (value := event.get("a_k")) is not None:
                    entry["a_k"] = value
                if (value := event.get("s_plus")) is not None:
                    entry["s_plus"] = value
                if (value := event.get("s_minus")) is not None:
                    entry["s_minus"] = value
                if (value := event.get("step")) is not None:
                    entry["step"] = value
                if (value := event.get("delta_norm")) is not None:
                    entry["delta_norm"] = value
                if "ltc_rejected" in event:
                    entry["ltc_rejected"] = bool(event.get("ltc_rejected"))
                reverted_idx = self._normalize_non_negative_idx(event.get("ltc_reverted_to"))
                if reverted_idx is not None:
                    entry["ltc_reverted_to"] = reverted_idx
                entry["pending"] = False
            elif event_type == "update_pending":
                entry["timestamp"] = event_ts
                current_start = entry.get("start_time")
                entry["start_time"] = min(current_start, event_ts) if isinstance(current_start, int) else event_ts
                params_val = event.get("params")
                if params_val is not None:
                    entry["params"] = dict(params_val)
                perturb_val = event.get("perturbations")
                if perturb_val is not None:
                    entry["perturbations"] = perturb_val
                if (value := event.get("c_k")) is not None:
                    entry["c_k"] = value
                if (value := event.get("a_k")) is not None:
                    entry["a_k"] = value
                entry["pending"] = True
            elif event_type == "update_perturbation":
                perturb_val = event.get("perturbations")
                if perturb_val is not None:
                    entry["perturbations"] = perturb_val
                if (value := event.get("c_k")) is not None:
                    entry["c_k"] = value
                if (value := event.get("a_k")) is not None:
                    entry["a_k"] = value
                entry.setdefault("pending", True)
            elif event_type == "ltc_regression_start":
                ltc_entry = _ensure_ltc_entry(entry)
                ltc_entry["status"] = "running"
                ltc_entry["total_pairs"] = event.get("total_pairs")
                ltc_entry["tuned_wins"] = 0
                ltc_entry["baseline_wins"] = 0
                ltc_entry["draws"] = 0
                ltc_entry["total_games"] = 0
                if event_ts is not None:
                    ltc_entry["started_at"] = event_ts
            elif event_type == "ltc_regression_result":
                ltc_entry = _ensure_ltc_entry(entry)
                status_val = event.get("status")
                if status_val is not None:
                    ltc_entry["status"] = status_val.strip()
                if (value := event.get("winrate")) is not None:
                    ltc_entry["winrate"] = value
                if (value := event.get("elo")) is not None:
                    ltc_entry["elo"] = value
                tuned_wins_val = event.get("tuned_wins")
                baseline_wins_val = event.get("baseline_wins")
                draws_val = event.get("draws")
                total_games_val = event.get("total_games")
                ltc_entry["tuned_wins"] = tuned_wins_val if tuned_wins_val is not None else 0
                ltc_entry["baseline_wins"] = baseline_wins_val if baseline_wins_val is not None else 0
                ltc_entry["draws"] = draws_val if draws_val is not None else 0
                if total_games_val is not None:
                    ltc_entry["total_games"] = total_games_val
                else:
                    ltc_entry["total_games"] = (
                        ltc_entry.get("tuned_wins", 0) + ltc_entry.get("baseline_wins", 0) + ltc_entry.get("draws", 0)
                    )
                if (accepted_val := event.get("accepted")) is not None:
                    ltc_entry["accepted"] = accepted_val
                baseline_idx_val = event.get("baseline_update_idx")
                normalized_baseline_idx = self._normalize_non_negative_idx(baseline_idx_val)
                if normalized_baseline_idx is not None:
                    ltc_entry["baseline_update_idx"] = normalized_baseline_idx
                if (val := event.get("baseline_variant_token")) is not None:
                    ltc_entry["baseline_variant_token"] = val
                if (val := event.get("tuned_variant_token")) is not None:
                    ltc_entry["tuned_variant_token"] = val
                if event_ts is not None:
                    ltc_entry["completed_at"] = event_ts
                if ltc_entry.get("accepted") is True:
                    self._persist_best_params_snapshot(entry, ltc_entry)
            elif event_type == "game_result":
                if _is_ltc_event(event):
                    ltc_entry = _ensure_ltc_entry(entry)
                    if event_ts is not None and ltc_entry.get("started_at") is None:
                        ltc_entry["started_at"] = event_ts
                    winner = event.get("winner")
                    if winner == 1:
                        ltc_entry["tuned_wins"] = int(ltc_entry.get("tuned_wins", 0)) + 1
                    elif winner == 0:
                        ltc_entry["baseline_wins"] = int(ltc_entry.get("baseline_wins", 0)) + 1
                    else:
                        ltc_entry["draws"] = int(ltc_entry.get("draws", 0)) + 1
                    ltc_entry["total_games"] = int(ltc_entry.get("total_games", 0)) + 1
                    continue

                winner = event.get("winner")
                if winner == 1:
                    entry["wins"] = entry.get("wins", 0) + 1
                elif winner == 0:
                    entry["losses"] = entry.get("losses", 0) + 1
                else:
                    entry["draws"] = entry.get("draws", 0) + 1

                phase_raw = event.get("phase")
                phase_key = phase_raw.strip().lower() if phase_raw else None
                if not phase_key:
                    phase_key = "unknown"
                phase_map = entry.setdefault(
                    "phase_wdl",
                    {
                        "plus": WdlCounts(wins=0, losses=0, draws=0),
                        "minus": WdlCounts(wins=0, losses=0, draws=0),
                    },
                )
                bucket = phase_map.setdefault(phase_key, WdlCounts(wins=0, losses=0, draws=0))
                if winner == 1:
                    bucket["wins"] = bucket.get("wins", 0) + 1
                elif winner == 0:
                    bucket["losses"] = bucket.get("losses", 0) + 1
                else:
                    bucket["draws"] = bucket.get("draws", 0) + 1

                gid = (event.get("game_id") or "").strip()
                if gid:
                    record = register_game(entry, gid)
                    if (val := event.get("black_player")) is not None:
                        record["black_player"] = val
                    if (val := event.get("white_player")) is not None:
                        record["white_player"] = val
                    record["result_code"] = event.get("result_code")
                    record["num_moves"] = event.get("num_moves")
                    record["status"] = "completed"
                    if (val := event.get("end_time")) is not None:
                        record["end_time"] = val

            elif event_type == "game_scheduled":
                if _is_ltc_event(event):
                    continue
                gid = (event.get("game_id") or "").strip()
                if not gid:
                    continue
                record = register_game(entry, gid)
                if (val := event.get("black_player")) is not None:
                    record["black_player"] = val
                if (val := event.get("white_player")) is not None:
                    record["white_player"] = val
                if (val := event.get("variant_token")) is not None:
                    record["variant_id"] = val
                if (val := event.get("phase")) is not None:
                    record["phase"] = val
                if (val := event.get("assigned_instance")) is not None:
                    record["assigned_instance"] = val
                status_value = (event.get("status") or "pending").strip().lower()
                record["status"] = status_value or "pending"
                if (val := event.get("start_time")) is not None:
                    record["start_time"] = val

        enriched: list[dict[str, Any]] = []
        for idx, entry in updates.items():
            entry["variant_id"] = format_variant_label(idx)
            start_ts = entry.get("start_time")
            end_ts = entry.get("end_time")
            entry["started_at"] = timestamp_to_iso(start_ts)
            entry["ended_at"] = timestamp_to_iso(end_ts)
            entry.pop("start_time", None)
            entry.pop("end_time", None)
            games_meta = entry.pop("games_meta", None)
            games_order = entry.pop("games_order", None)
            if isinstance(games_meta, dict) and isinstance(games_order, list):
                entry["games"] = [games_meta.get(gid) for gid in games_order if gid in games_meta]
            else:
                entry["games"] = []
            ltc_info = entry.get("ltc_regression") if isinstance(entry.get("ltc_regression"), Mapping) else None
            entry["btd_elo"] = ltc_info.get("elo") if ltc_info else None
            entry["has_ltc_regression"] = bool(entry.get("ltc_regression"))
            enriched.append(entry)

        enriched.sort(key=lambda item: item.get("update_idx", 0), reverse=True)
        return cast(list[UpdateEntry], enriched)

    def _persist_best_params_snapshot(self, entry: Mapping[str, Any], ltc_entry: Mapping[str, Any]) -> None:
        params = entry.get("params")
        if not isinstance(params, Mapping) or not params:
            return
        update_idx = entry.get("update_idx")
        if not isinstance(update_idx, int):
            return

        token_candidate = ltc_entry.get("tuned_variant_token") or entry.get("variant_id")
        variant_token: str | None = None
        if isinstance(token_candidate, str) and token_candidate.strip():
            variant_token = token_candidate.strip()
        else:
            variant_token = format_variant_label(update_idx)
        if not variant_token:
            return

        sanitized_params: dict[str, float] = {}
        for key, value in params.items():
            numeric = coerce_float(value)
            if numeric is None:
                continue
            sanitized_params[str(key)] = numeric
        if not sanitized_params:
            return

        completed_at = ltc_entry.get("completed_at")
        completed_iso: str | None = None
        if isinstance(completed_at, int | float):
            try:
                completed_iso = datetime.fromtimestamp(float(completed_at) / 1000.0, tz=timezone.utc).isoformat()
            except (OSError, OverflowError, ValueError):
                completed_iso = None

        metadata = {
            "ltc": {
                "status": ltc_entry.get("status"),
                "winrate": ltc_entry.get("winrate"),
                "elo": ltc_entry.get("elo"),
                "baseline_update_idx": ltc_entry.get("baseline_update_idx"),
                "baseline_variant_token": ltc_entry.get("baseline_variant_token"),
                "tuned_variant_token": ltc_entry.get("tuned_variant_token"),
                "pairs_played": ltc_entry.get("total_games"),
                "completed_at": completed_iso,
            }
        }

        self._store.save_best_params_snapshot(
            variant_token=variant_token,
            update_idx=update_idx,
            params=sanitized_params,
            metadata=metadata,
        )
