"""Progress snapshot calculation for SPSA dashboard updates."""

from __future__ import annotations


def build_progress_snapshot(*, completed_updates: int, total_updates: int | None) -> dict[str, int | float | None]:
    """Build progress snapshot from completed count and configured total."""

    percent = None
    if total_updates is not None and total_updates > 0:
        percent = completed_updates / total_updates
    return {
        "completed": completed_updates,
        "total": total_updates,
        "percent": percent,
    }


__all__ = ["build_progress_snapshot"]
