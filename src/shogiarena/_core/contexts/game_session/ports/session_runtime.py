"""Generic session runtime port contract.

Provides the canonical runtime gateway that tournament and SPSA contexts
extend with thin, context-specific ports.
"""

from __future__ import annotations

from collections.abc import Awaitable, Mapping
from typing import Protocol, TypeVar, runtime_checkable

from shogiarena._core.contexts.game_session.ports.run_storage import RunStoragePort
from shogiarena._core.shared.kernel.service_ports import RunStorageFactoryPort

TConfig = TypeVar("TConfig")
TBuildRequest = TypeVar("TBuildRequest")


@runtime_checkable
class SessionRuntimePort(RunStorageFactoryPort, Protocol[TConfig, TBuildRequest]):
    """Generic runtime gateway for config parsing and session execution.

    Context-specific ports narrow ``TConfig`` and ``TBuildRequest`` to
    concrete types while keeping the method surface canonical.
    """

    def build_run_config(
        self,
        payload: Mapping[str, object],
        *,
        request: TBuildRequest,
    ) -> TConfig: ...

    def run_session(
        self,
        config: TConfig,
        *,
        storage: RunStoragePort,
        should_skip_resume: bool,
        instance_pool: object | None,
    ) -> Awaitable[None]: ...


__all__ = [
    "SessionRuntimePort",
]
