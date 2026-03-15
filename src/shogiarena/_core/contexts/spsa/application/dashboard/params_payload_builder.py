"""Build SPSA params payload from parsed param entries."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_bool, coerce_float, coerce_optional_text


def build_params_payload(
    *,
    entries: Sequence[Mapping[str, object]],
    initial_params: Mapping[str, float] | None,
) -> dict[str, object]:
    """Build SPSA params response payload from parsed entries."""

    normalized_initial_params: dict[str, float] = {}
    if initial_params is not None:
        for name, value in initial_params.items():
            normalized_name = coerce_optional_text(name)
            normalized_value = coerce_float(value)
            if normalized_name is None or normalized_value is None:
                continue
            normalized_initial_params[normalized_name] = normalized_value

    values_map: dict[str, float] = {}
    for entry in entries:
        name = coerce_optional_text(entry.get("name"))
        value = coerce_float(entry.get("v"))
        if name is None or value is None:
            continue
        values_map[name] = value

    if not normalized_initial_params:
        normalized_initial_params = dict(values_map)
    else:
        for name, value in values_map.items():
            normalized_initial_params.setdefault(name, value)

    num_params = len(entries)
    num_used = sum(1 for entry in entries if not coerce_bool(entry.get("is_not_used")))
    epsilon = 1e-9
    num_clamped = 0
    for entry in entries:
        value = coerce_float(entry.get("v"))
        lower = coerce_float(entry.get("min"))
        upper = coerce_float(entry.get("max"))
        if value is None or lower is None or upper is None:
            continue
        if abs(value - lower) <= epsilon or abs(value - upper) <= epsilon:
            num_clamped += 1
    clamped_ratio = (num_clamped / num_params) if num_params else None

    diffs: dict[str, float] | None = None
    if normalized_initial_params:
        diffs = {}
        for entry in entries:
            name = coerce_optional_text(entry.get("name"))
            value = coerce_float(entry.get("v"))
            if name is None or value is None:
                continue
            diffs[name] = value - normalized_initial_params.get(name, value)

    return {
        "params": [dict(entry) for entry in entries],
        # Use update_idx-based variant_id (v000001 format) instead of hash
        "variant_id": None,
        "num_params": num_params,
        "num_used": num_used,
        "num_clamped": num_clamped,
        "clamped_ratio": clamped_ratio,
        "initial_params": normalized_initial_params or None,
        "diffs": diffs,
        # Use update_idx-based variant_id (v000001 format) instead of hash
        "initial_variant_id": None,
    }


__all__ = ["build_params_payload"]
