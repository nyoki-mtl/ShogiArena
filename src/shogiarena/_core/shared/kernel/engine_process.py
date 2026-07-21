"""Shared engine process metadata models."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True, kw_only=True)
class EngineProcessInfo:
    """Engine process metadata exposed to lifecycle hooks."""

    pid: int
    executable: str | None = None
    working_directory: str | None = None
    command_line: tuple[str, ...] | None = None


__all__ = ["EngineProcessInfo"]
