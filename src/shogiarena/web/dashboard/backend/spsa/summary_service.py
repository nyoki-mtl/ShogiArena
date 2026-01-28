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

from shogiarena.utils.types.types import GameResult

from .store import SpsaStore

_STORE_LOG_THRESHOLD_MS = 50.0
_STEP_HISTORY_CAPACITY = 256
_TIMESTAMP_HISTORY_CAPACITY = 32
_CACHE_VERSION = 1
_CACHE_FILENAME = ".cache/summary_cache_v1.json"


def _coerce_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            return int(stripped)
        except ValueError:
            return None
    return None


def _coerce_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            return float(stripped)
        except ValueError:
            return None
    return None


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "t", "yes", "y", "on", "black"}:
            return True
        if lowered in {"0", "false", "f", "no", "n", "off", "white"}:
            return False
    return False


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
        acc.last_update_idx = _coerce_int(payload.get("last_update_idx"))
        acc.last_delta_norm = _coerce_float(payload.get("last_delta_norm"))
        updates_seen = payload.get("updates_seen", [])
        if isinstance(updates_seen, list):
            rebuilt: set[int] = set()
            for item in updates_seen:
                coerced = _coerce_int(item)
                if coerced is not None:
                    rebuilt.add(coerced)
            acc.updates_seen = rebuilt
        steps = payload.get("step_history", [])
        if isinstance(steps, list):
            for item in steps:
                value = _coerce_float(item)
                if value is not None:
                    acc.step_history.append(value)
        timestamps = payload.get("update_timestamps", [])
        if isinstance(timestamps, list):
            for item in timestamps:
                value = _coerce_int(item)
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
        tuned_as_black = _coerce_bool(event.get("tuned_as_black"))
        winner_flag = _coerce_int(event.get("winner"))

        if winner_flag not in {0, 1}:
            result_code = _coerce_int(event.get("result_code"))
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
        idx = _coerce_int(event.get("update_idx"))
        if idx is not None:
            if idx not in self.updates_seen:
                changed = True
            self.updates_seen.add(idx)
            if self.last_update_idx is None or idx > self.last_update_idx:
                self.last_update_idx = idx
        step_val = _coerce_float(event.get("step"))
        if step_val is not None:
            self.step_history.append(step_val)
            changed = True
        timestamp = event.get("ts") if "ts" in event else event.get("timestamp")
        ts_val = _coerce_int(timestamp)
        if ts_val is not None:
            self.update_timestamps.append(ts_val)
            changed = True
        delta_norm = _coerce_float(event.get("delta_norm"))
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

    def compute_summary(self) -> dict[str, Any]:
        """Compute summary statistics from cached aggregates and new events."""

        start_meta = time.perf_counter()
        meta_data = self._store.load_meta_data()
        meta_elapsed = (time.perf_counter() - start_meta) * 1000.0

        session_uuid = None
        if isinstance(meta_data, dict):
            raw_uuid = meta_data.get("session_uuid")
            if isinstance(raw_uuid, str):
                session_uuid = raw_uuid.strip() or None

        with self._lock:
            self._refresh_from_events(session_uuid)
            summary = self._build_summary(meta_data)

        if meta_elapsed >= _STORE_LOG_THRESHOLD_MS:
            print(f"[spsa:store:meta] duration_ms={meta_elapsed:.1f}", flush=True)

        return summary

    def _build_summary(self, meta_data: Mapping[str, Any] | None) -> dict[str, Any]:
        agg = self._aggregates
        raw_total = None
        if isinstance(meta_data, Mapping):
            raw_total = _coerce_int(meta_data.get("total"))
            if raw_total is None:
                raw_total = _coerce_int(meta_data.get("num_updates"))
        num_updates_total = raw_total if raw_total is not None else 0
        experiment_name = meta_data.get("experiment_name") if isinstance(meta_data, Mapping) else "Unknown"

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

        engine_time_controls: dict[str, str] = {}
        default_time_control: str | None = None
        engines: list[str] = []
        engine_instances: dict[str, str | None] = {}
        engine_stats: dict[str, dict[str, int | float]] = {}
        engines_meta: list[dict[str, Any]] = []
        if isinstance(meta_data, Mapping):
            raw_time_controls = meta_data.get("engine_time_controls") or meta_data.get("engineTimeControls")
            if isinstance(raw_time_controls, Mapping):
                for name, spec in raw_time_controls.items():
                    key = str(name or "").strip()
                    if not key:
                        continue
                    if spec is None:
                        engine_time_controls[key] = "-"
                    else:
                        engine_time_controls[key] = str(spec)
            raw_default_tc = meta_data.get("default_time_control") or meta_data.get("defaultTimeControl")
            if raw_default_tc is not None:
                default_time_control = str(raw_default_tc)
            raw_engines = meta_data.get("engines")
            if isinstance(raw_engines, list):
                engines = [str(name).strip() for name in raw_engines if str(name or "").strip()]
            raw_instances = meta_data.get("engine_instances") or meta_data.get("engineInstances")
            if isinstance(raw_instances, Mapping):
                for name, inst in raw_instances.items():
                    key = str(name or "").strip()
                    if not key:
                        continue
                    engine_instances[key] = None if inst is None else str(inst)
            raw_stats = meta_data.get("engine_stats") or meta_data.get("engineStats")
            if isinstance(raw_stats, Mapping):
                for name, stat in raw_stats.items():
                    key = str(name or "").strip()
                    if not key or not isinstance(stat, Mapping):
                        continue
                    engine_stats[key] = {
                        "wins": int(stat.get("wins") or 0),
                        "losses": int(stat.get("losses") or 0),
                        "draws": int(stat.get("draws") or 0),
                        "games": int(stat.get("games") or 0),
                    }
            raw_meta = meta_data.get("enginesMeta") or meta_data.get("engines_meta")
            if isinstance(raw_meta, list):
                engines_meta = [dict(entry) for entry in raw_meta if isinstance(entry, Mapping)]

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

        # Build spsaConfig from meta_data for Rules tab display
        spsa_config: dict[str, Any] | None = None
        if isinstance(meta_data, dict):
            _SPSA_CONFIG_KEYS = (
                "num_updates",
                "mobility",
                "scale",
                "a0",
                "A",
                "alpha",
                "gamma",
                "crn_enabled",
                "int_rounding",
                "int_ck_floor",
                "update_mode",
                "snap_float_to_step",
                "early_stop",
                "update_batch_size",
                "inflight_factor",
            )
            spsa_config = {}
            for key in _SPSA_CONFIG_KEYS:
                if key in meta_data:
                    spsa_config[key] = meta_data[key]
            ltc_raw = meta_data.get("ltc_regression")
            if isinstance(ltc_raw, dict):
                spsa_config["ltc_regression"] = ltc_raw
            if not spsa_config:
                spsa_config = None

        return {
            "mode": "spsa",
            "experiment_name": experiment_name,
            "wins": wins,
            "losses": losses,
            "draws": draws,
            "elo": None,
            "btd_elo": None,
            "btd_se": None,
            "btd_los": None,
            "games_total": games_total,
            "draw_rate": (draws / games_total) if games_total > 0 else None,
            "tuned_black_wins": agg.tuned_black_wins,
            "tuned_black_losses": agg.tuned_black_losses,
            "tuned_white_wins": agg.tuned_white_wins,
            "tuned_white_losses": agg.tuned_white_losses,
            "games": {
                "completed": updates_completed,
                "total": num_updates_total,
            },
            "last_update_idx": agg.last_update_idx,
            "step_mean_20": step_mean_20,
            "step_std_20": step_std_20,
            "recent_steps": recent_steps,
            "delta_norm_last": agg.last_delta_norm,
            "eta_seconds": eta_seconds,
            "winrate": winrate,
            "progress": progress,
            "recent_step": recent_step,
            "recent_delta_norm": recent_delta_norm,
            "engineTimeControls": engine_time_controls,
            "defaultTimeControl": default_time_control,
            "engines": engines,
            "enginesMeta": engines_meta,
            "engineInstances": engine_instances,
            "engineStats": engine_stats,
            "spsaConfig": spsa_config,
        }

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
            payload = json.loads(self._cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(payload, Mapping) or payload.get("version") != _CACHE_VERSION:
            return
        aggregates = payload.get("aggregates")
        if isinstance(aggregates, Mapping):
            self._aggregates = _SummaryAccumulator.from_dict(aggregates)
        session_uuid = payload.get("session_uuid")
        if isinstance(session_uuid, str):
            self._session_uuid = session_uuid or None
        self._events_offset = int(payload.get("events_offset", 0) or 0)
        self._events_size = int(payload.get("events_size", 0) or 0)
        self._events_mtime_ns = int(payload.get("events_mtime_ns", 0) or 0)

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
