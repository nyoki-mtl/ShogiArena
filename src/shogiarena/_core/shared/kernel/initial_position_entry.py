"""Initial-position entry model shared by schedule and config code."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class InitialPositionEntry:
    """Initial position plus optional opening-line provenance."""

    initial_sfen: str
    source_line: str | None = None
    source_line_no: int | None = None
    line_moves_usi: tuple[str, ...] = ()
    line_id: str | None = None
    source_path: str | None = None


__all__ = ["InitialPositionEntry"]
