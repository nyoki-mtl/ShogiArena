"""Arena orchestrator entrypoints and shared helpers.

Re-exports are provided lazily to avoid circular imports during package initialisation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, overload

from . import base_orchestrator_utils

if TYPE_CHECKING:  # pragma: no cover - for type checkers only
    from .base_orchestrator import BaseOrchestrator
    from .engine_pool import EnginePool
    from .spsa_orchestrator import SpsaOrchestrator
    from .tournament_orchestrator import TournamentOrchestrator

__all__ = [
    "BaseOrchestrator",
    "EnginePool",
    "TournamentOrchestrator",
    "SpsaOrchestrator",
    "base_orchestrator_utils",
]


@overload
def __getattr__(name: Literal["BaseOrchestrator"]) -> type[BaseOrchestrator]: ...


@overload
def __getattr__(name: Literal["EnginePool"]) -> type[EnginePool]: ...


@overload
def __getattr__(name: Literal["SpsaOrchestrator"]) -> type[SpsaOrchestrator]: ...


@overload
def __getattr__(name: Literal["TournamentOrchestrator"]) -> type[TournamentOrchestrator]: ...


def __getattr__(name: str) -> object:
    if name == "BaseOrchestrator" or name == "EnginePool":
        from .base_orchestrator import BaseOrchestrator
        from .engine_pool import EnginePool

        return BaseOrchestrator if name == "BaseOrchestrator" else EnginePool
    if name == "SpsaOrchestrator":
        from .spsa_orchestrator import SpsaOrchestrator

        return SpsaOrchestrator
    if name == "TournamentOrchestrator":
        from .tournament_orchestrator import TournamentOrchestrator

        return TournamentOrchestrator
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")
