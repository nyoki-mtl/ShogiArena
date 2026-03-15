"""Shared provisioning Protocol contracts for platform engine adapters."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable


@runtime_checkable
class SshCommandTransportPort(Protocol):
    async def run(
        self,
        command: str,
        *,
        env: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> tuple[int, str, str]: ...


@runtime_checkable
class SshStreamTransportPort(Protocol):
    def run_stream_lines(self, command: str, *, env: dict[str, str] | None = None) -> AsyncIterator[str]: ...


@runtime_checkable
class InstanceConfigPort(Protocol):
    engine_dir: str
    should_install_requirements: bool


@runtime_checkable
class NamedInstancePort(Protocol):
    name: str


@runtime_checkable
class _ConfiguredInstancePort(NamedInstancePort, Protocol):
    config: InstanceConfigPort


@runtime_checkable
class _RuntimeMetricsPort(Protocol):
    engine_processes: int


@runtime_checkable
class EngineRuntimeInstancePort(_ConfiguredInstancePort, Protocol):
    is_ssh: bool
    max_engine_capacity: int
    metrics: _RuntimeMetricsPort

    def add_engine_processes(self, delta: int) -> None: ...


__all__ = [
    "EngineRuntimeInstancePort",
    "InstanceConfigPort",
    "NamedInstancePort",
    "SshCommandTransportPort",
    "SshStreamTransportPort",
]
