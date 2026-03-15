"""Runtime gateway contracts for SPSA execution."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from shogiarena._core.contexts.game_session.ports.session_runtime import SessionRuntimePort


@dataclass(frozen=True, slots=True)
class SpsaRunConfigBuildRequest:
    """Typed build request for SPSA run config construction."""

    source_path: Path | None


class EngineTraceLoggerNamesPort(Protocol):
    """Narrow extension for SPSA-specific engine trace logger names."""

    def engine_trace_logger_names(self) -> tuple[str, ...]: ...


@runtime_checkable
class SpsaRuntimePort(
    SessionRuntimePort[Any, SpsaRunConfigBuildRequest],
    EngineTraceLoggerNamesPort,
    Protocol,
):
    """Port for SPSA config parsing and session execution."""


__all__ = [
    "SpsaRunConfigBuildRequest",
    "SpsaRuntimePort",
]
