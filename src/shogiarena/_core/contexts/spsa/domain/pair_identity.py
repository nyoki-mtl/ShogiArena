"""Canonical SPSA/LTC pair identity helpers."""

from __future__ import annotations


def canonical_pair_ids(*, kind: str, update_idx: int, count: int) -> tuple[str, ...]:
    """Return the exact ordered pair IDs required by one update."""

    if kind not in {"SPSA", "LTC"}:
        raise ValueError(f"Unsupported SPSA pair kind: {kind}")
    if count < 0:
        raise ValueError("SPSA pair count must not be negative")
    prefix = "spsa" if kind == "SPSA" else "ltc"
    return tuple(f"{prefix}-u{update_idx:06d}-p{pair_idx:06d}" for pair_idx in range(count))


__all__ = ["canonical_pair_ids"]
