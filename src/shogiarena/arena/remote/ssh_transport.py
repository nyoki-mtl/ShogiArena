from __future__ import annotations

import asyncio
import logging
import posixpath
import shlex
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import TYPE_CHECKING

import asyncssh

from shogiarena.arena.instances.models import Instance, InstanceType

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from asyncssh.connection import SSHClientConnection
    from asyncssh.sftp import SFTPClient


class SshTransportError(RuntimeError):
    pass


class SshTransport:
    """Abstract SSH transport for remote execution and file transfer."""

    def __init__(self, instance: Instance) -> None:
        if instance.config.type != InstanceType.SSH:
            raise ValueError("SshTransport requires SSH instance")
        self.instance = instance

    async def connect(self) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    async def close(self) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    async def run(
        self,
        command: str,
        *,
        env: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> tuple[int, str, str]:  # pragma: no cover - interface
        raise NotImplementedError

    def run_stream_lines(
        self, command: str, *, env: dict[str, str] | None = None
    ) -> AsyncIterator[str]:  # pragma: no cover - interface
        raise NotImplementedError

    async def mkdir(self, path: str, *, exist_ok: bool = True) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    async def put_file(self, local: Path, remote: str) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    async def write_bytes(self, remote: str, data: bytes) -> None:  # pragma: no cover - interface
        """Write bytes to remote file atomically where possible."""
        tmp = remote + ".tmp"
        await self.mkdir(posixpath.dirname(remote) or "/", exist_ok=True)
        with NamedTemporaryFile(delete=False) as nf:
            nf.write(data)
            tmp_local = Path(nf.name)
        move_cmd = f"mv {shlex.quote(tmp)} {shlex.quote(remote)}"
        try:
            await self.put_file(tmp_local, tmp)
            rc, out, err = await self.run(move_cmd)
            if rc != 0:
                message = err or out or "remote move failed"
                raise SshTransportError(f"Failed to finalize remote write: {message}")
        finally:
            try:
                Path(tmp_local).unlink(missing_ok=True)
            except OSError as exc:
                logger.debug("Failed to remove temporary file %s: %s", tmp_local, exc)


@dataclass
class _ConnectionConfig:
    host: str
    user: str
    port: int
    identity_file: str | None
    strict_host_key_checking: bool


def _connection_config_from_instance(inst: Instance) -> _ConnectionConfig:
    config = inst.config
    if not config.host or not config.user:
        raise ValueError("SSH instance requires host and user")
    return _ConnectionConfig(
        host=str(config.host),
        user=str(config.user),
        port=int(config.port or 22),
        identity_file=config.identity_file,
        strict_host_key_checking=bool(config.strict_host_key_checking),
    )


class AsyncSSHTransport(SshTransport):
    """asyncssh-based transport (mandatory dependency)."""

    def __init__(self, instance: Instance) -> None:
        super().__init__(instance)
        self._connection_config = _connection_config_from_instance(instance)
        self._conn: SSHClientConnection | None = None
        self._sftp: SFTPClient | None = None

    async def connect(self) -> None:
        if self._conn is not None:
            return
        client_keys = [self._connection_config.identity_file] if self._connection_config.identity_file else None
        connect_kwargs = {
            "port": self._connection_config.port,
            "username": self._connection_config.user,
            "client_keys": client_keys,
        }
        if not self._connection_config.strict_host_key_checking:
            connect_kwargs["known_hosts"] = None
        self._conn = await asyncssh.connect(self._connection_config.host, **connect_kwargs)
        self._sftp = await self._conn.start_sftp_client()

    async def close(self) -> None:
        if self._sftp is not None:
            try:
                self._sftp.exit()
            except (asyncssh.Error, OSError) as exc:
                logger.debug("SFTP client exit error: %s", exc)
        if self._conn is not None:
            try:
                self._conn.close()
                await self._conn.wait_closed()
            except (asyncssh.Error, OSError) as exc:
                logger.debug("SSH connection close error: %s", exc)
        self._conn = None
        self._sftp = None

    async def run(
        self,
        command: str,
        *,
        env: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> tuple[int, str, str]:
        conn = await self._ensure_connection()
        result = await asyncio.wait_for(conn.run(command, env=env), timeout=timeout)
        return int(result.exit_status or 0), str(result.stdout or ""), str(result.stderr or "")

    async def run_stream_lines(self, command: str, *, env: dict[str, str] | None = None) -> AsyncIterator[str]:
        conn = await self._ensure_connection()
        proc = await conn.create_process(command, env=env)
        try:
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                yield line.rstrip("\n")
        finally:
            try:
                proc.terminate()
            except ProcessLookupError:
                logger.debug("asyncssh process already exited before terminate()")
            except (asyncssh.Error, OSError) as exc:
                logger.debug("Failed to terminate asyncssh process: %s", exc)
            try:
                await proc.wait()
            except (asyncssh.Error, OSError) as exc:
                logger.debug("Error while waiting for asyncssh process exit: %s", exc)

    async def mkdir(self, path: str, *, exist_ok: bool = True) -> None:
        sftp = await self._ensure_sftp()
        parts = [segment for segment in path.split("/") if segment]
        current = "/" if path.startswith("/") else ""
        for segment in parts:
            current = f"{current}/{segment}" if current else segment
            try:
                await sftp.mkdir(current)
            except asyncssh.SFTPError:
                if not exist_ok:
                    try:
                        await sftp.stat(current)
                    except asyncssh.SFTPError as exc:
                        raise SshTransportError(f"mkdir failed for {current}: {exc}") from exc

    async def put_file(self, local: Path, remote: str) -> None:
        sftp = await self._ensure_sftp()
        parent = posixpath.dirname(remote)
        if parent:
            await self.mkdir(parent, exist_ok=True)
        await sftp.put(str(local), remote)

    async def _ensure_connection(self) -> SSHClientConnection:
        if self._conn is None:
            await self.connect()
        assert self._conn is not None
        return self._conn

    async def _ensure_sftp(self) -> SFTPClient:
        if self._sftp is None:
            await self.connect()
        assert self._sftp is not None
        return self._sftp


def create_transport(instance: Instance) -> SshTransport:
    """Create a transport (asyncssh-based)."""
    return AsyncSSHTransport(instance)
