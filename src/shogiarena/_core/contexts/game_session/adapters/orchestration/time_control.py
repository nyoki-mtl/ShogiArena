"""Game-session specific time-control helpers."""

from __future__ import annotations

from typing import Any

from shogiarena._core.shared.kernel.time_control import TimeControlLimits


def compute_time_control_from_rules(rules: Any, engines: list[Any]) -> tuple[TimeControlLimits | None, bool]:
    """Compute global ``TimeControlLimits`` and enable flag from rules/engines."""
    tc_limits = None
    is_time_control_enabled = False
    tc = rules.time_control
    has_override = any(engine.time_control is not None for engine in engines)
    if tc is not None:
        tc_limits = TimeControlLimits(
            time_ms=tc.time_ms,
            increment_ms=tc.increment_ms,
            byoyomi_ms=tc.byoyomi_ms,
            fixed_time_ms=tc.fixed_time_ms,
            expiry_margin_ms=tc.expiry_margin_ms,
            should_allow_timeout=tc.should_allow_timeout,
            depth_limit=tc.depth_limit,
            node_limit=tc.node_limit,
        )
        is_time_control_enabled = True
    elif has_override:
        is_time_control_enabled = True
    return tc_limits, is_time_control_enabled


__all__ = ["compute_time_control_from_rules"]
