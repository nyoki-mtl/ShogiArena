from __future__ import annotations

import asyncio
import inspect
import logging
import posixpath
import re
import shlex
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile

import asyncssh
from asyncssh.connection import SSHClientConnection
from asyncssh.sftp import SFTPClient

from shogiarena._core.contexts.instances.application.instance_models import Instance, InstanceType
from shogiarena._core.contexts.instances.ports.secret_transport import (
    RemoteSecretCommandFactory,
    RemoteSecretFileHandle,
    RemoteSecretFileRequest,
)

logger = logging.getLogger(__name__)
_SECRET_FILE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


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

    async def run_with_secret_files(
        self,
        command_factory: RemoteSecretCommandFactory,
        secret_files: Sequence[RemoteSecretFileRequest],
        *,
        env: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> tuple[int, str, str]:
        """Run a command with transport-managed temporary secret files."""

        secret_handles, temp_dir = await self._prepare_secret_files(secret_files)
        try:
            command = command_factory(secret_handles)
            return await self.run(command, env=env, timeout=timeout)
        finally:
            await self._cleanup_secret_temp_dir(temp_dir)

    async def run_stream_lines_with_secret_files(
        self,
        command_factory: RemoteSecretCommandFactory,
        secret_files: Sequence[RemoteSecretFileRequest],
        *,
        env: dict[str, str] | None = None,
    ) -> AsyncIterator[str]:
        """Stream command output with transport-managed temporary secret files."""

        secret_handles, temp_dir = await self._prepare_secret_files(secret_files)
        line_iter: AsyncIterator[str] | None = None
        try:
            command = command_factory(secret_handles)
            line_iter = self.run_stream_lines(command, env=env)
            async for line in line_iter:
                yield line
        finally:
            if line_iter is not None:
                await self._close_async_iterator(line_iter)
            await self._cleanup_secret_temp_dir(temp_dir)

    async def mkdir(self, path: str, *, is_existing_ok: bool = True) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    async def put_file(self, local: Path, remote: str) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    async def write_bytes(self, remote: str, data: bytes) -> None:  # pragma: no cover - interface
        """Write bytes to remote file atomically where possible."""
        tmp = remote + ".tmp"
        await self.mkdir(posixpath.dirname(remote) or "/", is_existing_ok=True)
        with NamedTemporaryFile(delete=False) as nf:
            nf.write(data)
            local_temp_path = Path(nf.name)
        move_cmd = f"mv {shlex.quote(tmp)} {shlex.quote(remote)}"
        try:
            await self.put_file(local_temp_path, tmp)
            rc, out, err = await self.run(move_cmd)
            if rc != 0:
                message = err or out or "remote move failed"
                raise SshTransportError(f"Failed to finalize remote write: {message}")
        finally:
            try:
                Path(local_temp_path).unlink(missing_ok=True)
            except OSError as exc:
                logger.debug("Failed to remove temporary file %s: %s", local_temp_path, exc)

    async def _prepare_secret_files(
        self, secret_files: Sequence[RemoteSecretFileRequest]
    ) -> tuple[Mapping[str, RemoteSecretFileHandle], str | None]:
        if not secret_files:
            return {}, None
        temp_dir = await self._create_secret_temp_dir()
        handles: dict[str, RemoteSecretFileHandle] = {}
        try:
            for request in secret_files:
                self._validate_secret_file_request(request)
                remote_path = posixpath.join(temp_dir, request.file_id)
                await self._write_secret_file(request, remote_path)
                handles[request.file_id] = RemoteSecretFileHandle(
                    file_id=request.file_id,
                    remote_path=remote_path,
                )
            return handles, temp_dir
        except BaseException:
            await self._cleanup_secret_temp_dir(temp_dir)
            raise

    async def _create_secret_temp_dir(self) -> str:
        command = (
            'tmp_dir=$(mktemp -d "${TMPDIR:-/tmp}/shogiarena-secret.XXXXXXXXXX") '
            '&& chmod 700 "$tmp_dir" && printf "%s" "$tmp_dir"'
        )
        rc, stdout, stderr = await self.run(command)
        if rc != 0 or not stdout.strip():
            message = stderr or stdout or "mktemp failed"
            raise SshTransportError(f"Failed to create remote secret directory: {message}")
        return stdout.strip()

    async def _write_secret_file(self, request: RemoteSecretFileRequest, remote_path: str) -> None:
        with NamedTemporaryFile(delete=False) as nf:
            nf.write(request.payload)
            local_temp_path = Path(nf.name)
        try:
            await self.put_file(local_temp_path, remote_path)
            rc, out, err = await self.run(f"chmod {request.mode:o} {shlex.quote(remote_path)}")
            if rc != 0:
                message = err or out or "chmod failed"
                raise SshTransportError(f"Failed to protect remote secret file: {message}")
        finally:
            try:
                local_temp_path.unlink(missing_ok=True)
            except OSError as exc:
                logger.debug("Failed to remove local secret temp file %s: %s", local_temp_path, exc)

    async def _cleanup_secret_temp_dir(self, temp_dir: str | None) -> None:
        if temp_dir is None:
            return
        try:
            rc, out, err = await self.run(f"rm -rf -- {shlex.quote(temp_dir)}")
        except (OSError, RuntimeError, ValueError) as exc:
            logger.debug("Failed to remove remote secret directory: %s", exc)
            return
        if rc != 0:
            logger.debug("Failed to remove remote secret directory: %s", err or out)

    @staticmethod
    def _validate_secret_file_request(request: RemoteSecretFileRequest) -> None:
        if not _SECRET_FILE_ID_PATTERN.fullmatch(request.file_id):
            raise ValueError(f"Invalid remote secret file id: {request.file_id}")
        if request.mode <= 0 or request.mode > 0o700:
            raise ValueError(f"Invalid remote secret file mode for {request.file_id}: {request.mode:o}")
        if request.mode & 0o077:
            raise ValueError(f"Remote secret file mode must not grant group/world access: {request.file_id}")
        if not request.mode & 0o400:
            raise ValueError(f"Remote secret file must be owner-readable: {request.file_id}")

    @staticmethod
    async def _close_async_iterator(iterator: AsyncIterator[str]) -> None:
        closer = getattr(iterator, "aclose", None)
        if closer is None:
            return
        close_result = closer()
        if inspect.isawaitable(close_result):
            await close_result


@dataclass
class _ConnectionConfig:
    host: str
    user: str
    port: int
    identity_file: str | None
    is_strict_host_key_checking: bool


def _connection_config_from_instance(inst: Instance) -> _ConnectionConfig:
    config = inst.config
    if not config.host or not config.user:
        raise ValueError("SSH instance requires host and user")
    return _ConnectionConfig(
        host=str(config.host),
        user=str(config.user),
        port=int(config.port or 22),
        identity_file=config.identity_file,
        is_strict_host_key_checking=bool(config.is_strict_host_key_checking),
    )


class _AsyncSshTransport(SshTransport):
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
        if not self._connection_config.is_strict_host_key_checking:
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

    async def mkdir(self, path: str, *, is_existing_ok: bool = True) -> None:
        sftp = await self._ensure_sftp()
        parts = [segment for segment in path.split("/") if segment]
        current = "/" if path.startswith("/") else ""
        for segment in parts:
            current = f"{current}/{segment}" if current else segment
            try:
                await sftp.mkdir(current)
            except asyncssh.SFTPError:
                if not is_existing_ok:
                    try:
                        await sftp.stat(current)
                    except asyncssh.SFTPError as exc:
                        raise SshTransportError(f"mkdir failed for {current}: {exc}") from exc

    async def put_file(self, local: Path, remote: str) -> None:
        sftp = await self._ensure_sftp()
        parent = posixpath.dirname(remote)
        if parent:
            await self.mkdir(parent, is_existing_ok=True)
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
    return _AsyncSshTransport(instance)
