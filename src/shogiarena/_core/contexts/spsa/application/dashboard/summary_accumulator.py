"""Incremental SPSA summary aggregations derived from event streams."""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping

from shogiarena._core.shared.kernel.json_types import JsonObject, JsonValue
from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_bool, coerce_float, coerce_game_result, coerce_int

_STEP_HISTORY_CAPACITY = 256
_TIMESTAMP_HISTORY_CAPACITY = 32


def _coerce_bool_with_color(value: JsonValue | None) -> bool:
    """``coerce_bool`` with domain-specific ``black``/``white`` aliases."""
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered == "black":
            return True
        if lowered == "white":
            return False
    return coerce_bool(value)


class SummaryAccumulator:
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

    def to_dict(self) -> JsonObject:
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
    def from_dict(cls, payload: Mapping[str, JsonValue]) -> SummaryAccumulator:
        acc = cls()
        acc.wins = coerce_int(payload.get("wins")) or 0
        acc.losses = coerce_int(payload.get("losses")) or 0
        acc.draws = coerce_int(payload.get("draws")) or 0
        acc.tuned_black_wins = coerce_int(payload.get("tuned_black_wins")) or 0
        acc.tuned_black_losses = coerce_int(payload.get("tuned_black_losses")) or 0
        acc.tuned_white_wins = coerce_int(payload.get("tuned_white_wins")) or 0
        acc.tuned_white_losses = coerce_int(payload.get("tuned_white_losses")) or 0
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

    def consume_event(self, event: Mapping[str, JsonValue]) -> bool:
        event_type = event.get("event")
        if event_type == "game_result":
            return self._consume_game_result(event)
        if event_type == "update":
            return self._consume_update(event)
        return False

    def _consume_game_result(self, event: Mapping[str, JsonValue]) -> bool:
        is_tuned_as_black = _coerce_bool_with_color(event.get("tuned_as_black"))
        winner_flag = coerce_int(event.get("winner"))

        if winner_flag not in {0, 1}:
            result = coerce_game_result(event.get("game_result"))
            if result is not None:
                if result.is_draw():
                    winner_flag = 2
                elif result.is_black_win():
                    winner_flag = 1 if is_tuned_as_black else 0
                elif result.is_white_win():
                    winner_flag = 0 if is_tuned_as_black else 1
        if winner_flag == 1:
            self.wins += 1
            if is_tuned_as_black:
                self.tuned_black_wins += 1
            else:
                self.tuned_white_wins += 1
        elif winner_flag == 0:
            self.losses += 1
            if is_tuned_as_black:
                self.tuned_black_losses += 1
            else:
                self.tuned_white_losses += 1
        else:
            self.draws += 1
        return True

    def _consume_update(self, event: Mapping[str, JsonValue]) -> bool:
        has_changed = False
        idx = coerce_int(event.get("update_idx"))
        if idx is not None:
            if idx not in self.updates_seen:
                has_changed = True
            self.updates_seen.add(idx)
            if self.last_update_idx is None or idx > self.last_update_idx:
                self.last_update_idx = idx
        step_val = coerce_float(event.get("step"))
        if step_val is not None:
            self.step_history.append(step_val)
            has_changed = True
        timestamp = event.get("ts") if "ts" in event else event.get("timestamp")
        ts_val = coerce_int(timestamp)
        if ts_val is not None:
            self.update_timestamps.append(ts_val)
            has_changed = True
        delta_norm = coerce_float(event.get("delta_norm"))
        if delta_norm is not None:
            self.last_delta_norm = delta_norm
            has_changed = True
        return has_changed


__all__ = ["SummaryAccumulator"]
