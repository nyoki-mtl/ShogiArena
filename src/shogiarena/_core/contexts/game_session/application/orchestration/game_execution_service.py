"""Shared game execution wiring service for orchestrators."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Generic, TypeVar

from shogiarena._core.contexts.game_session.application.orchestration.game_execution_mode_service import (
    OrchestratorExecutionModeRequest,
    OrchestratorGameExecutionModeService,
)
from shogiarena._core.contexts.game_session.application.orchestration.local_execution_spec_service import (
    GameExecutionSpecFactory,
    LocalExecutionSpecRequest,
    build_local_execution_spec,
)

ResultT = TypeVar("ResultT")
RemoteInstanceT = TypeVar("RemoteInstanceT")
TEngineItem = TypeVar("TEngineItem")
TGameExecutionSpec = TypeVar("TGameExecutionSpec")


RunRemoteWithInstancePort = Callable[[RemoteInstanceT], Awaitable[ResultT]]
ExecuteLocalPort = Callable[[TGameExecutionSpec], Awaitable[ResultT]]


@dataclass(frozen=True)
class OrchestratorGameExecutionRequest(Generic[RemoteInstanceT, TEngineItem]):
    """Input DTO for orchestrator game execution wiring."""

    execution_mode: OrchestratorExecutionModeRequest[RemoteInstanceT]
    local_execution: LocalExecutionSpecRequest[TEngineItem]


class OrchestratorGameExecutionService:
    """Execute one game by wiring mode selection and local spec creation."""

    def __init__(
        self,
        *,
        execution_mode_service: OrchestratorGameExecutionModeService | None = None,
    ) -> None:
        self._execution_mode_service = execution_mode_service or OrchestratorGameExecutionModeService()

    async def execute(
        self,
        *,
        request: OrchestratorGameExecutionRequest[RemoteInstanceT, TEngineItem],
        game_execution_spec_factory: GameExecutionSpecFactory[TGameExecutionSpec],
        run_remote_with_instance: RunRemoteWithInstancePort[RemoteInstanceT, ResultT],
        execute_local: ExecuteLocalPort[TGameExecutionSpec, ResultT],
        ensure_remote_install: Callable[[set[str]], Awaitable[None]] | None = None,
        mark_install_complete: Callable[[], None] | None = None,
    ) -> ResultT:
        local_spec = build_local_execution_spec(
            request=request.local_execution,
            game_execution_spec_factory=game_execution_spec_factory,
        )

        async def _run_remote() -> ResultT:
            remote_instance = request.execution_mode.selected_remote_instance
            if remote_instance is None:
                raise RuntimeError("Remote dispatch instance is missing")
            return await run_remote_with_instance(remote_instance)

        async def _run_local() -> ResultT:
            return await execute_local(local_spec)

        return await self._execution_mode_service.execute(
            request=request.execution_mode,
            ensure_remote_install=ensure_remote_install,
            mark_install_complete=mark_install_complete,
            run_remote=_run_remote,
            run_local=_run_local,
        )


__all__ = ["OrchestratorGameExecutionRequest", "OrchestratorGameExecutionService"]
