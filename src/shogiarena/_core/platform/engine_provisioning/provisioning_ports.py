"""Shared provisioning Protocol contracts for platform engine adapters."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Protocol, runtime_checkable

from shogiarena._core.contexts.instances.ports.secret_transport import (
    RemoteSecretCommandFactory,
    RemoteSecretFileHandle,
    RemoteSecretFileRequest,
    SshSecretFileTransportPort,
)


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
class SshFileTransportPort(SshCommandTransportPort, Protocol):
    async def mkdir(self, path: str, *, is_existing_ok: bool = True) -> None: ...

    async def put_file(self, local: Path, remote: str) -> None: ...

    async def endpoint_identity(self) -> str: ...


@runtime_checkable
class InstanceConfigPort(Protocol):
    engine_dir: str
    should_install_requirements: bool


@runtime_checkable
class NamedInstancePort(Protocol):
    # 読み取り専用（実装側は @property / frozen dataclass）。可変属性宣言だと
    # 書き込み可能性を要求してしまい protocol 適合しない。
    @property
    def name(self) -> str: ...


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
    "RemoteSecretCommandFactory",
    "RemoteSecretFileHandle",
    "RemoteSecretFileRequest",
    "SshCommandTransportPort",
    "SshFileTransportPort",
    "SshSecretFileTransportPort",
    "SshStreamTransportPort",
]
