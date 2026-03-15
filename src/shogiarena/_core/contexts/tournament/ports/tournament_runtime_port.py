"""Runtime gateway contracts for tournament execution."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from shogiarena._core.contexts.game_session.ports.session_runtime import SessionRuntimePort


@dataclass(frozen=True, slots=True)
class TournamentRunConfigBuildRequest:
    """Typed build request for tournament run config construction."""

    base_dir: Path
    source_path: Path | None


@runtime_checkable
class TournamentRuntimePort(SessionRuntimePort[Any, TournamentRunConfigBuildRequest], Protocol):
    """Port for tournament config parsing and session execution."""


__all__ = ["TournamentRunConfigBuildRequest", "TournamentRuntimePort"]
