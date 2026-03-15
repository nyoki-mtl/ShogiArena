"""Shared remote/local execution mode selection for orchestrators."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Generic, TypeVar

ResultT = TypeVar("ResultT")
RemoteInstanceT = TypeVar("RemoteInstanceT")


@dataclass(frozen=True)
class OrchestratorExecutionModeRequest(Generic[RemoteInstanceT]):
    """Execution mode input DTO."""

    should_require_install: bool
    black_instance_id: str | None
    white_instance_id: str | None
    selected_remote_instance: RemoteInstanceT | None


class OrchestratorGameExecutionModeService:
    """Route execution to remote/local path with optional install pre-step."""

    @staticmethod
    def _resolve_install_target_instance_ids(
        *,
        black_instance_id: str | None,
        white_instance_id: str | None,
    ) -> set[str]:
        targets: set[str] = set()
        for instance_id in (black_instance_id, white_instance_id):
            if instance_id is None or instance_id == "local":
                continue
            targets.add(instance_id)
        return targets

    async def execute(
        self,
        *,
        request: OrchestratorExecutionModeRequest[RemoteInstanceT],
        ensure_remote_install: Callable[[set[str]], Awaitable[None]] | None = None,
        mark_install_complete: Callable[[], None] | None = None,
        run_remote: Callable[[], Awaitable[ResultT]],
        run_local: Callable[[], Awaitable[ResultT]],
    ) -> ResultT:
        if request.should_require_install:
            if ensure_remote_install is None or mark_install_complete is None:
                raise ValueError("Install callbacks are required when should_require_install is True")
            install_targets = self._resolve_install_target_instance_ids(
                black_instance_id=request.black_instance_id,
                white_instance_id=request.white_instance_id,
            )
            if install_targets:
                await ensure_remote_install(install_targets)
            mark_install_complete()
        if request.selected_remote_instance is not None:
            return await run_remote()
        return await run_local()


__all__ = ["OrchestratorExecutionModeRequest", "OrchestratorGameExecutionModeService"]
