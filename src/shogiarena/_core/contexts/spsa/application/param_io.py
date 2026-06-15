"""Load, persist, and derive helper metadata for SPSA parameter files."""

from __future__ import annotations

import logging
import math
from pathlib import Path

from shogiarena._core.contexts.spsa.domain.spsa_models import ParamEntry

logger = logging.getLogger(__name__)

NOT_USED_STR = "[[NOT USED]]"


def _parse_finite(token: str, *, field: str, location: str) -> float:
    try:
        value = float(token)
    except ValueError as exc:
        raise ValueError(f"{field} value {token!r} is not a number: {location}") from exc
    if not math.isfinite(value):
        raise ValueError(f"{field} must be finite, got {token!r}: {location}")
    return value


def read_params(path: str | Path) -> list[ParamEntry]:
    """Load parameter entries from ``path``."""

    param_path = Path(path)
    if not param_path.exists():
        raise FileNotFoundError(f"parameter file not found: {param_path}")

    logger.debug("read parameters, path = %s", param_path)
    entries: list[ParamEntry] = []
    seen_names: set[str] = set()

    with param_path.open(encoding="utf-8") as handle:
        for line_no, raw in enumerate(handle, 1):
            line = raw.rstrip()
            if not line:
                continue
            is_not_used = False
            if NOT_USED_STR in line:
                line = line.replace(NOT_USED_STR, "")
                is_not_used = True
            if "//" in line:
                val_part, comment = line.split("//", 1)
            else:
                val_part, comment = line, ""
            values = [value.strip() for value in val_part.split(",")]
            location = f"{param_path}({line_no}): {raw!r}"
            if len(values) < 7:
                raise ValueError(f"insufficient params: {location}")

            name = str(values[0])
            if not name:
                raise ValueError(f"empty parameter name: {location}")
            if name in seen_names:
                raise ValueError(f"duplicate parameter name {name!r}: {location}")
            seen_names.add(name)

            param_type = str(values[1])
            if param_type not in ("int", "float"):
                raise ValueError(f"parameter type must be 'int' or 'float', got {param_type!r}: {location}")

            value = _parse_finite(values[2], field="value", location=location)
            minimum = _parse_finite(values[3], field="min", location=location)
            maximum = _parse_finite(values[4], field="max", location=location)
            step = _parse_finite(values[5], field="step", location=location)
            delta = _parse_finite(values[6], field="delta", location=location)
            if minimum > maximum:
                raise ValueError(f"min ({minimum}) must be <= max ({maximum}): {location}")
            if not is_not_used:
                # SPSA requires c_k > 0; a non-positive step makes the gain schedule divide by
                # zero, and a negative delta inverts the update into gradient descent.
                if step <= 0:
                    raise ValueError(f"step (c_end) must be > 0 for used parameter {name!r}: {location}")
                if delta < 0:
                    raise ValueError(f"delta (r_end) must be >= 0 for used parameter {name!r}: {location}")

            entries.append(
                ParamEntry(
                    name=name,
                    type=param_type,
                    value=value,
                    min=minimum,
                    max=maximum,
                    step=step,
                    delta=delta,
                    comment=comment.strip(),
                    is_not_used=is_not_used,
                )
            )

    logger.debug("loaded %d parameters", len(entries))
    return entries


def write_params(path: str | Path, entries: list[ParamEntry]) -> None:
    """Write parameter entries back to ``path`` in the canonical format."""

    param_path = Path(path)
    with param_path.open("w", encoding="utf-8") as handle:
        for entry in entries:
            comment = f" //{entry.comment}" if entry.comment else ""
            flag = NOT_USED_STR if entry.is_not_used else ""
            v_str = str(entry.value)
            line = (
                f"{entry.name}, {entry.type}, {v_str}, {entry.min}, "
                f"{entry.max}, {entry.step}, {entry.delta}{comment}{flag}\n"
            )
            handle.write(line)
    logger.debug("wrote parameter file, %d parameters", len(entries))


# --- Quantization & Variant helpers ---------------------------------------


def quantize_value(
    e: ParamEntry,
    value: float,
    *,
    should_snap_float: bool = False,
    should_round_int: bool = True,
) -> float:
    """Quantize and clamp a parameter value according to its metadata.

    - type=int: round to nearest integer, clamp to [min, max]
    - type=float: clamp; optionally snap to nearest multiple of step when should_snap_float=True
    """
    v = float(value)
    mn = float(e.min)
    mx = float(e.max)
    st = float(e.step or 0.0)
    if mn > mx:
        raise ValueError(f"parameter '{e.name}' has min greater than max: {mn} > {mx}")
    if e.type == "int":
        # Integer parameters can optionally retain fractional parts so repeated SPSA updates
        # accumulate over time (matching YaneuraOu's reference implementation).
        if not should_round_int:
            v = max(mn, min(mx, v))
            return float(v)

        snapped = int(round(v))
        lower = int(math.ceil(mn))
        upper = int(math.floor(mx))
        clamped = max(lower, min(upper, snapped))
        return float(clamped)
    # float type
    if should_snap_float and st > 0:
        kf = round(v / st)
        v = kf * st
    v = max(mn, min(mx, v))
    return float(v)
