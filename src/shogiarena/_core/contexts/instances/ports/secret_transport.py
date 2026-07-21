"""Secret-file capable SSH transport contracts."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class RemoteSecretFileRequest:
    """リモート実行へ渡す一時 secret file の要求。"""

    file_id: str
    payload: bytes
    mode: int = 0o600


@dataclass(frozen=True)
class RemoteSecretFileHandle:
    """リモート側に作成された secret file の非 secret ハンドル。"""

    file_id: str
    remote_path: str


RemoteSecretCommandFactory = Callable[[Mapping[str, RemoteSecretFileHandle]], str]


@runtime_checkable
class SshSecretFileTransportPort(Protocol):
    """secret file を伴う SSH コマンド実行 port。"""

    async def run_with_secret_files(
        self,
        command_factory: RemoteSecretCommandFactory,
        secret_files: Sequence[RemoteSecretFileRequest],
        *,
        env: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> tuple[int, str, str]: ...

    def run_stream_lines_with_secret_files(
        self,
        command_factory: RemoteSecretCommandFactory,
        secret_files: Sequence[RemoteSecretFileRequest],
        *,
        env: dict[str, str] | None = None,
    ) -> AsyncIterator[str]: ...


__all__ = [
    "RemoteSecretCommandFactory",
    "RemoteSecretFileHandle",
    "RemoteSecretFileRequest",
    "SshSecretFileTransportPort",
]
