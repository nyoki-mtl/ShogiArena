"""SPSA summary calculation service with incremental caching."""

from __future__ import annotations

import json
import math
import time
from collections import deque
from collections.abc import Mapping
from pathlib import Path
from threading import Lock
from typing import Any

from shogiarena.utils.types.coerce import coerce_bool, coerce_float, coerce_int
from shogiarena.utils.types.types import GameResult

from .models import SpsaMetaData, SummaryCachePayload
from .store import SpsaStore
from .types import SpsaSummaryGames, SpsaSummaryPayload

_STORE_LOG_THRESHOLD_MS = 50.0
_STEP_HISTORY_CAPACITY = 256
_TIMESTAMP_HISTORY_CAPACITY = 32
_CACHE_VERSION = 1
_CACHE_FILENAME = ".cache/summary_cache_v1.json"


def _coerce_bool_with_color(value: Any) -> bool:
    """``coerce_bool`` に ``"black"``/``"white"`` 解釈を追加したドメイン特化版。"""
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered == "black":
            return True
        if lowered == "white":
            return False
    return coerce_bool(value)


class _SummaryAccumulator:
    """Maintain incremental aggregates for SPSA summary metrics."""

    __slots__ = (
        "wins",
        "losses",
        "draws",
        "tuned_black_wins",
        "tuned_black_losses",
        "tuned_white_wins",
        "tuned_white_losses",
        "last_update_idx",
        "last_delta_norm",
        "updates_seen",
        "step_history",
        "update_timestamps",
    )

    def __init__(self) -> None:
        self.wins = 0
        self.losses = 0
        self.draws = 0
        self.tuned_black_wins = 0
        self.tuned_black_losses = 0
        self.tuned_white_wins = 0
        self.tuned_white_losses = 0
        self.last_update_idx: int | None = None
        self.last_delta_norm: float | None = None
        self.updates_seen: set[int] = set()
        self.step_history: deque[float] = deque(maxlen=_STEP_HISTORY_CAPACITY)
        self.update_timestamps: deque[int] = deque(maxlen=_TIMESTAMP_HISTORY_CAPACITY)

    def to_dict(self) -> dict[str, Any]:
        return {
            "wins": self.wins,
            "losses": self.losses,
            "draws": self.draws,
            "tuned_black_wins": self.tuned_black_wins,
            "tuned_black_losses": self.tuned_black_losses,
            "tuned_white_wins": self.tuned_white_wins,
            "tuned_white_losses": self.tuned_white_losses,
            "last_update_idx": self.last_update_idx,
            "last_delta_norm": self.last_delta_norm,
            "updates_seen": sorted(self.updates_seen),
            "step_history": list(self.step_history),
            "update_timestamps": list(self.update_timestamps),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> _SummaryAccumulator:
        acc = cls()
        acc.wins = int(payload.get("wins", 0) or 0)
        acc.losses = int(payload.get("losses", 0) or 0)
        acc.draws = int(payload.get("draws", 0) or 0)
        acc.tuned_black_wins = int(payload.get("tuned_black_wins", 0) or 0)
        acc.tuned_black_losses = int(payload.get("tuned_black_losses", 0) or 0)
        acc.tuned_white_wins = int(payload.get("tuned_white_wins", 0) or 0)
        acc.tuned_white_losses = int(payload.get("tuned_white_losses", 0) or 0)
        acc.last_update_idx = coerce_int(payload.get("last_update_idx"))
        acc.last_delta_norm = coerce_float(payload.get("last_delta_norm"))
        updates_seen = payload.get("updates_seen", [])
        if isinstance(updates_seen, list):
            rebuilt: set[int] = set()
            for item in updates_seen:
                coerced = coerce_int(item)
                if coerced is not None:
                    rebuilt.add(coerced)
            acc.updates_seen = rebuilt
        steps = payload.get("step_history", [])
        if isinstance(steps, list):
            for item in steps:
                value = coerce_float(item)
                if value is not None:
                    acc.step_history.append(value)
        timestamps = payload.get("update_timestamps", [])
        if isinstance(timestamps, list):
            for item in timestamps:
                value = coerce_int(item)
                if value is not None:
                    acc.update_timestamps.append(value)
        return acc

    def consume_event(self, event: Mapping[str, Any]) -> bool:
        event_type = event.get("event")
        if event_type == "game_result":
            return self._consume_game_result(event)
        if event_type == "update":
            return self._consume_update(event)
        return False

    def _consume_game_result(self, event: Mapping[str, Any]) -> bool:
        tuned_as_black = _coerce_bool_with_color(event.get("tuned_as_black"))
        winner_flag = coerce_int(event.get("winner"))

        if winner_flag not in {0, 1}:
            result_code = coerce_int(event.get("result_code"))
            if result_code is not None:
                try:
                    result = GameResult(result_code)
                except ValueError:
                    result = None
                if result is not None:
                    if result.is_draw():
                        winner_flag = 2
                    elif result.is_black_win():
                        winner_flag = 1 if tuned_as_black else 0
                    elif result.is_white_win():
                        winner_flag = 0 if tuned_as_black else 1
        if winner_flag == 1:
            self.wins += 1
            if tuned_as_black:
                self.tuned_black_wins += 1
            else:
                self.tuned_white_wins += 1
        elif winner_flag == 0:
            self.losses += 1
            if tuned_as_black:
                self.tuned_black_losses += 1
            else:
                self.tuned_white_losses += 1
        else:
            self.draws += 1
        return True

    def _consume_update(self, event: Mapping[str, Any]) -> bool:
        changed = False
        idx = coerce_int(event.get("update_idx"))
        if idx is not None:
            if idx not in self.updates_seen:
                changed = True
            self.updates_seen.add(idx)
            if self.last_update_idx is None or idx > self.last_update_idx:
                self.last_update_idx = idx
        step_val = coerce_float(event.get("step"))
        if step_val is not None:
            self.step_history.append(step_val)
            changed = True
        timestamp = event.get("ts") if "ts" in event else event.get("timestamp")
        ts_val = coerce_int(timestamp)
        if ts_val is not None:
            self.update_timestamps.append(ts_val)
            changed = True
        delta_norm = coerce_float(event.get("delta_norm"))
        if delta_norm is not None:
            self.last_delta_norm = delta_norm
            changed = True
        return changed


class SpsaSummaryService:
    """Service for computing SPSA summary statistics with incremental caching."""

    def __init__(self, store: SpsaStore) -> None:
        self._store = store
        self._lock = Lock()
        self._aggregates = _SummaryAccumulator()
        self._session_uuid: str | None = None
        self._events_offset = 0
        self._events_size = 0
        self._events_mtime_ns = 0
        cache_path = store.spsa_path(_CACHE_FILENAME)
        self._cache_path = cache_path if isinstance(cache_path, Path) else None
        self._load_cache()

    def compute_summary(self) -> SpsaSummaryPayload:
        """Compute summary statistics from cached aggregates and new events."""

        start_meta = time.perf_counter()
        meta_data = self._store.load_meta_data()
        meta_elapsed = (time.perf_counter() - start_meta) * 1000.0

        with self._lock:
            self._refresh_from_events(meta_data.session_uuid)
            summary = self._build_summary(meta_data)

        if meta_elapsed >= _STORE_LOG_THRESHOLD_MS:
            print(f"[spsa:store:meta] duration_ms={meta_elapsed:.1f}", flush=True)

        return summary

    def _build_summary(self, meta_data: SpsaMetaData) -> SpsaSummaryPayload:
        agg = self._aggregates
        num_updates_total = meta_data.effective_num_updates or 0
        experiment_name = meta_data.experiment_name

        wins = agg.wins
        losses = agg.losses
        draws = agg.draws
        games_total = wins + losses + draws

        steps = list(agg.step_history)
        recent_steps = steps[-100:]
        step_mean_20, step_std_20 = self._compute_window_stats(steps[-20:])

        eta_seconds = self._estimate_eta_seconds(num_updates_total, agg)
        winrate = (wins / games_total) if games_total > 0 else 0.0
        updates_completed = len(agg.updates_seen)
        progress = (updates_completed / num_updates_total) if num_updates_total > 0 else 0.0
        recent_step = recent_steps[-1] if recent_steps else 0.0
        recent_delta_norm = agg.last_delta_norm if agg.last_delta_norm is not None else 0.0

        engine_time_controls = dict(meta_data.engine_time_controls)
        default_time_control = meta_data.default_time_control
        engines = list(meta_data.engines)
        engine_instances = dict(meta_data.engine_instances)
        engine_stats: dict[str, dict[str, int | float]] = {
            name: {"wins": stat.wins, "losses": stat.losses, "draws": stat.draws, "games": stat.games}
            for name, stat in meta_data.engine_stats.items()
        }
        engines_meta = list(meta_data.engines_meta)

        # Prefer live aggregates over stale meta.json values
        if engines:
            primary = engines[0]
            tuned = engines[1] if len(engines) > 1 else engines[0]
            games_total = wins + losses + draws
            engine_stats[primary] = {
                "wins": losses,
                "losses": wins,
                "draws": draws,
                "games": games_total,
            }
            engine_stats[tuned] = {
                "wins": wins,
                "losses": losses,
                "draws": draws,
                "games": games_total,
            }

        spsa_config = meta_data.resolve_spsa_config()

        return SpsaSummaryPayload(
            mode="spsa",
            experiment_name=experiment_name,
            wins=wins,
            losses=losses,
            draws=draws,
            elo=None,
            btd_elo=None,
            btd_se=None,
            btd_los=None,
            games_total=games_total,
            draw_rate=(draws / games_total) if games_total > 0 else None,
            tuned_black_wins=agg.tuned_black_wins,
            tuned_black_losses=agg.tuned_black_losses,
            tuned_white_wins=agg.tuned_white_wins,
            tuned_white_losses=agg.tuned_white_losses,
            games=SpsaSummaryGames(
                completed=updates_completed,
                total=num_updates_total,
            ),
            last_update_idx=agg.last_update_idx,
            step_mean_20=step_mean_20,
            step_std_20=step_std_20,
            recent_steps=recent_steps,
            delta_norm_last=agg.last_delta_norm,
            eta_seconds=eta_seconds,
            winrate=winrate,
            progress=progress,
            recent_step=recent_step,
            recent_delta_norm=recent_delta_norm,
            engineTimeControls=engine_time_controls,
            defaultTimeControl=default_time_control,
            engines=engines,
            enginesMeta=engines_meta,
            engineInstances=engine_instances,
            engineStats=engine_stats,
            spsaConfig=spsa_config,
        )

    @staticmethod
    def _compute_window_stats(values: list[float]) -> tuple[float | None, float | None]:
        if not values:
            return None, None
        count = len(values)
        mean_val = sum(values) / count
        if count < 2:
            return mean_val, 0.0
        variance = sum((x - mean_val) ** 2 for x in values) / (count - 1)
        return mean_val, math.sqrt(variance)

    def _estimate_eta_seconds(self, num_updates_total: int | None, agg: _SummaryAccumulator) -> int | None:
        if not num_updates_total or num_updates_total <= 0:
            return None
        if not agg.updates_seen:
            return None
        timestamps = list(agg.update_timestamps)[-10:]
        if len(timestamps) < 2:
            return None
        timestamps.sort()
        intervals = [max(0, timestamps[i] - timestamps[i - 1]) / 1000.0 for i in range(1, len(timestamps))]
        if not intervals:
            return None
        avg_interval = sum(intervals) / len(intervals)
        remaining = max(0, int(num_updates_total) - len(agg.updates_seen))
        return int(remaining * avg_interval) if avg_interval > 0 else None

    def _refresh_from_events(self, session_uuid: str | None) -> None:
        events_path = self._store.spsa_path("events.jsonl")
        path: Path | None = events_path if isinstance(events_path, Path) else None
        if path is None or not path.exists():
            if self._events_size != 0:
                self._reset_state(session_uuid)
                self._persist_cache()
            return

        try:
            stat = path.stat()
        except OSError:
            return

        need_reset = False
        if session_uuid != self._session_uuid:
            need_reset = True
        elif stat.st_size < self._events_offset:
            need_reset = True
        elif stat.st_mtime_ns != self._events_mtime_ns and stat.st_size <= self._events_size:
            need_reset = True

        changed = False
        start_offset = 0
        if need_reset:
            self._reset_state(session_uuid)
            changed = True
        else:
            start_offset = self._events_offset

        try:
            new_offset = start_offset
            if stat.st_size > start_offset:
                with path.open("rb") as handle:
                    handle.seek(start_offset)
                    for raw_line in handle:
                        if not raw_line.strip():
                            continue
                        try:
                            payload = json.loads(raw_line.decode("utf-8"))
                        except (UnicodeDecodeError, json.JSONDecodeError):
                            continue
                        if isinstance(payload, Mapping) and self._aggregates.consume_event(payload):
                            changed = True
                    new_offset = handle.tell()
        except OSError:
            return

        self._events_offset = new_offset
        self._events_size = stat.st_size
        self._events_mtime_ns = stat.st_mtime_ns
        self._session_uuid = session_uuid

        if changed:
            self._persist_cache()

    def _reset_state(self, session_uuid: str | None) -> None:
        self._aggregates = _SummaryAccumulator()
        self._session_uuid = session_uuid
        self._events_offset = 0
        self._events_size = 0
        self._events_mtime_ns = 0

    def _load_cache(self) -> None:
        if not self._cache_path or not self._cache_path.exists():
            return
        try:
            raw = json.loads(self._cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(raw, dict) or raw.get("version") != _CACHE_VERSION:
            return
        cache = SummaryCachePayload.model_validate(raw)
        if cache.aggregates:
            self._aggregates = _SummaryAccumulator.from_dict(cache.aggregates)
        self._session_uuid = cache.session_uuid
        self._events_offset = cache.events_offset
        self._events_size = cache.events_size
        self._events_mtime_ns = cache.events_mtime_ns

    def _persist_cache(self) -> None:
        if not self._cache_path:
            return
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            snapshot = {
                "version": _CACHE_VERSION,
                "session_uuid": self._session_uuid,
                "events_offset": self._events_offset,
                "events_size": self._events_size,
                "events_mtime_ns": self._events_mtime_ns,
                "aggregates": self._aggregates.to_dict(),
            }
            self._cache_path.write_text(json.dumps(snapshot), encoding="utf-8")
        except OSError:
            # Cache persistence is best-effort; ignore failures.
            return
