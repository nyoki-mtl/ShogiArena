from __future__ import annotations

from collections.abc import Iterable

from shogiarena._core.shared.kernel.time_control import TimeControlLimits


def build_time_control_limits(
    base_time_control: TimeControlLimits | None,
    override_time_control: TimeControlLimits | None,
) -> TimeControlLimits | None:
    """Combine base and override time controls into a validated limit object."""
    if base_time_control is None and override_time_control is None:
        return None

    def _pick_int(name: str) -> int | None:
        if override_time_control is not None:
            override_value = getattr(override_time_control, name, None)
            if override_value is not None:
                return int(override_value)
        if base_time_control is not None:
            base_value = getattr(base_time_control, name, None)
            if base_value is not None:
                return int(base_value)
        return None

    expiry_margin_ms = (
        override_time_control.expiry_margin_ms
        if override_time_control is not None
        else base_time_control.expiry_margin_ms
        if base_time_control is not None
        else 500
    )
    limits = TimeControlLimits(
        time_ms=_pick_int("time_ms"),
        increment_ms=_pick_int("increment_ms"),
        byoyomi_ms=_pick_int("byoyomi_ms"),
        fixed_time_ms=_pick_int("fixed_time_ms"),
        depth_limit=_pick_int("depth_limit"),
        node_limit=_pick_int("node_limit"),
        expiry_margin_ms=expiry_margin_ms,
        should_allow_timeout=(
            override_time_control.should_allow_timeout
            if override_time_control is not None
            else base_time_control.should_allow_timeout
            if base_time_control is not None
            else False
        ),
        max_wait_ms=_pick_int("max_wait_ms") or TimeControlLimits.max_wait_ms,
    )

    increment_ms = int(limits.increment_ms or 0)
    byoyomi_ms = int(limits.byoyomi_ms or 0)
    if increment_ms > 99_900:
        raise ValueError(f"increment_ms must be <= 99.9s (99900 ms), got {increment_ms} ms")
    if byoyomi_ms > 99_900:
        raise ValueError(f"byoyomi_ms must be <= 99.9s (99900 ms), got {byoyomi_ms} ms")
    if limits.time_ms is not None and int(limits.time_ms) > 6_099_900:
        raise ValueError(f"time_ms must be <= 99m99.9s (6099900 ms), got {limits.time_ms} ms")
    return limits


def compute_time_control_specs(
    *,
    base_time_control: TimeControlLimits | None,
    entries: Iterable[tuple[str, TimeControlLimits | None]],
) -> tuple[dict[str, str], str | None]:
    engine_map: dict[str, str] = {}
    default_spec: str | None = None
    for engine_name, override_time_control in entries:
        if engine_name in engine_map:
            continue
        limits = build_time_control_limits(base_time_control, override_time_control)
        if limits is None:
            engine_map[engine_name] = "-"
            if default_spec is None:
                default_spec = "-"
            continue
        spec = limits.to_spec_str()
        engine_map[engine_name] = spec
        if default_spec is None:
            default_spec = spec
    return engine_map, default_spec


def time_control_limits_to_dict(limits: TimeControlLimits | None) -> dict[str, int | bool | None]:
    """Convert ``TimeControlLimits`` to a JSON-serialisable dictionary."""
    if limits is None:
        raise ValueError("time control limits must be provided")
    return {
        "time_ms": limits.time_ms,
        "increment_ms": limits.increment_ms,
        "byoyomi_ms": limits.byoyomi_ms,
        "fixed_time_ms": limits.fixed_time_ms,
        "depth_limit": limits.depth_limit,
        "node_limit": limits.node_limit,
        "expiry_margin_ms": limits.expiry_margin_ms,
        "should_allow_timeout": limits.should_allow_timeout,
        "max_wait_ms": limits.max_wait_ms,
    }


__all__ = [
    "build_time_control_limits",
    "compute_time_control_specs",
    "time_control_limits_to_dict",
]
