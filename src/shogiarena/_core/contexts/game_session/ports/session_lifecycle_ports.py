"""Shared session lifecycle port contracts for runtime runners."""

from __future__ import annotations

from collections.abc import Awaitable, Mapping
from dataclasses import dataclass
from typing import Generic, Literal, Protocol, TypeAlias, TypeVar

from shogiarena._core.contexts.game_session.ports.session_context import SessionContext
from shogiarena._core.shared.kernel.json_types import JsonValue
from shogiarena._core.shared.kernel.session_hooks import GameLifecycleHooks, SessionStopController

ProgressPayload: TypeAlias = Mapping[str, JsonValue]
DashboardProfile: TypeAlias = Literal["tournament", "spsa", "match", "sprt", "generate"]
PROFILE_KEYS: tuple[DashboardProfile, ...] = ("tournament", "spsa", "match", "sprt", "generate")


class ProgressReporterPort(Protocol):
    """進捗レポートの Protocol。"""

    def on_game_start(self, payload: ProgressPayload) -> None: ...

    def on_game_complete(self, payload: ProgressPayload) -> None: ...

    def finalize(self, payload: ProgressPayload) -> None: ...


class SessionStopControllerPort(Protocol):
    def request_stop(self, *, reason: str | None = None) -> None: ...

    @property
    def reason(self) -> str | None: ...

    @property
    def is_stop_requested(self) -> bool: ...


@dataclass(slots=True)
class RunOptions:
    should_skip_resume: bool = False


TRunResult = TypeVar("TRunResult")
TRunFlow = TypeVar("TRunFlow")


class OrchestratorPort(Protocol, Generic[TRunResult]):
    """Minimal runtime-orchestrator contract used by run-loop service."""

    def request_stop(self) -> None: ...

    async def shutdown(self) -> None: ...

    def run(self) -> Awaitable[TRunResult]: ...


class SessionRunnerPort(Protocol[TRunResult]):
    async def prepare_run_dir(self) -> None: ...
    async def prepare_domain(self) -> None: ...
    async def init_services(self) -> None: ...
    def get_dashboard_params(self) -> tuple[object, int, int] | None: ...
    async def start_dashboard_server(self, run_dir: object, preferred_port: int, num_workers: int) -> int: ...
    async def seed_initial_summary(self) -> None: ...
    def build_session_context(self) -> SessionContext | None: ...
    def set_session_context(self, session_context: SessionContext | None) -> None: ...
    def create_lifecycle_hooks(self, controller: SessionStopController) -> GameLifecycleHooks: ...
    def set_lifecycle_hooks(self, hooks: GameLifecycleHooks) -> None: ...
    async def create_orchestrator(
        self, hooks: GameLifecycleHooks, session_context: SessionContext | None
    ) -> OrchestratorPort[TRunResult]: ...
    async def run_pre_orchestration_hooks(self, orchestrator: OrchestratorPort[TRunResult]) -> None: ...
    async def run_orchestrator(
        self, orchestrator: OrchestratorPort[TRunResult], run_coro: Awaitable[TRunResult]
    ) -> TRunResult | None: ...
    async def finalize_and_persist(self, run_result: TRunResult | None) -> TRunResult | None: ...
    async def stop_services(self) -> None: ...
    def are_services_closed(self) -> bool: ...

    @property
    def stop_controller(self) -> SessionStopController: ...


__all__ = [
    "DashboardProfile",
    "OrchestratorPort",
    "PROFILE_KEYS",
    "ProgressPayload",
    "ProgressReporterPort",
    "RunOptions",
    "SessionStopControllerPort",
    "SessionRunnerPort",
]
