"""Shared conversion from internal time-control limits to record spec strings."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import rsshogi.record


@runtime_checkable
class RecordTimeControlLimitsPort(Protocol):
    """Required shape for record time-control conversion."""

    time_ms: int | None
    increment_ms: int | None
    byoyomi_ms: int | None
    fixed_time_ms: int | None


def limits_to_record_time_spec(limits: RecordTimeControlLimitsPort) -> str:
    fixed_time_ms = limits.fixed_time_ms
    if fixed_time_ms:
        base_seconds = max(int(fixed_time_ms) // 1000, 0)
        return rsshogi.record.TimeControl(base_seconds, 0, 0).to_spec()

    base_seconds = max(int(limits.time_ms or 0) // 1000, 0)
    byoyomi_seconds = max(int(limits.byoyomi_ms or 0) // 1000, 0)
    increment_seconds = max(int(limits.increment_ms or 0) // 1000, 0)
    return rsshogi.record.TimeControl(base_seconds, byoyomi_seconds, increment_seconds).to_spec()


__all__ = ["limits_to_record_time_spec"]
