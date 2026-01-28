"""Load, persist, and derive helper metadata for SPSA parameter files."""

from __future__ import annotations

import hashlib
import logging
import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

NOT_USED_STR = "[[NOT USED]]"


@dataclass(slots=True)
class ParamEntry:
    """Single SPSA parameter definition parsed from disk."""

    name: str
    type: str
    v: float
    min: float
    max: float
    step: float
    delta: float
    comment: str
    not_used: bool


def read_params(path: str | Path) -> list[ParamEntry]:
    """Load parameter entries from ``path``."""

    param_path = Path(path)
    if not param_path.exists():
        raise FileNotFoundError(f"parameter file not found: {param_path}")

    logger.debug("read parameters, path = %s", param_path)
    entries: list[ParamEntry] = []

    with param_path.open(encoding="utf-8") as handle:
        for line_no, raw in enumerate(handle, 1):
            line = raw.rstrip()
            if not line:
                continue
            not_used = False
            if NOT_USED_STR in line:
                line = line.replace(NOT_USED_STR, "")
                not_used = True
            if "//" in line:
                val_part, comment = line.split("//", 1)
            else:
                val_part, comment = line, ""
            values = [value.strip() for value in val_part.split(",")]
            if len(values) < 7:
                raise ValueError(f"insufficient params: {param_path}({line_no}): {raw}")
            entries.append(
                ParamEntry(
                    name=str(values[0]),
                    type=str(values[1]),
                    v=float(values[2]),
                    min=float(values[3]),
                    max=float(values[4]),
                    step=float(values[5]),
                    delta=float(values[6]),
                    comment=comment.strip(),
                    not_used=not_used,
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
            flag = NOT_USED_STR if entry.not_used else ""
            v_str = str(entry.v)
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
    snap_float: bool = False,
    round_int: bool = True,
) -> float:
    """Quantize and clamp a parameter value according to its metadata.

    - type=int: round to nearest integer, clamp to [min, max]
    - type=float: clamp; optionally snap to nearest multiple of step when snap_float=True
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
        if not round_int:
            v = max(mn, min(mx, v))
            return float(v)

        snapped = int(round(v))
        lower = int(math.ceil(mn))
        upper = int(math.floor(mx))
        clamped = max(lower, min(upper, snapped))
        return float(clamped)
    # float type
    if snap_float and st > 0:
        kf = round(v / st)
        v = kf * st
    v = max(mn, min(mx, v))
    return float(v)


def compute_variant_id_from_entries(
    params: Iterable[ParamEntry], *, sort_by_name: bool = True, snap_float: bool = False
) -> str:
    """Compute a short variant id from parameter entries.

    - Uses quantized/clamped values
    - Sorts by name for stability (default)
    - Ints formatted as integers, floats rounded to 4 decimals
    """
    items = list(params)
    if sort_by_name:
        items.sort(key=lambda p: p.name)
    parts: list[str] = []
    for p in items:
        if p.not_used:
            continue
        qv = quantize_value(p, p.v, snap_float=snap_float)
        if p.type == "int":
            parts.append(str(int(round(qv))))
        else:
            parts.append(str(round(qv, 4)))
    return hashlib.sha1(",".join(parts).encode("utf-8")).hexdigest()[:8]


def compute_variant_id_from_mapping(
    mapping: Mapping[str, float], *, types: Mapping[str, str] | None = None, sort_by_name: bool = True
) -> str:
    """Compute variant id from a plain mapping of name->value.

    If ``types`` is provided, int types are formatted as integers; otherwise,
    values are treated as floats and rounded to 4 decimals.
    """
    keys = sorted(mapping.keys()) if sort_by_name else list(mapping.keys())
    parts: list[str] = []
    for k in keys:
        v = float(mapping[k])
        t = (types or {}).get(k, "float")
        if t == "int":
            parts.append(str(int(round(v))))
        else:
            parts.append(str(round(v, 4)))
    return hashlib.sha1(",".join(parts).encode("utf-8")).hexdigest()[:8]
