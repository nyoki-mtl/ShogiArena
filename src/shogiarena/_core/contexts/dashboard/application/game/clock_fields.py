"""Shared clock field names used by dashboard game payloads."""

from __future__ import annotations

CLOCK_FIELDS: tuple[str, ...] = (
    "side",
    "active",
    "black_remain_ms",
    "white_remain_ms",
    "applied_increment_ms",
    "occurred_at_ms",
    "started_at_ms",
    "pre_black_remain_ms",
    "pre_white_remain_ms",
    "byoyomi_ms_black",
    "byoyomi_ms_white",
    "increment_ms_black",
    "increment_ms_white",
)

__all__ = ["CLOCK_FIELDS"]
