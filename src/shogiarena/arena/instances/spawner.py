"""Engine process spawner abstraction for local and SSH execution.

Notes:
- SSH execution uses asyncssh exclusively; there is no fallback to system OpenSSH.
- Host key verification follows instance settings (strict by default) via asyncssh.
"""

import asyncio
import logging
import shlex
from abc import ABC, abstractmethod
from collections.abc import Sequence
from pathlib import Path
from typing import cast

import asyncssh
import psutil

from .models import Instance

logger = logging.getLogger(__name__)


def _apply_cpu_affinity(pid: int | None, affinity: tuple[int, ...], log: logging.Logger) -> None:
    """Apply CPU affinity to a local process, logging non-fatal issues."""
    if pid is None:
        log.warning("Cannot apply cpu_affinity=%s: process PID is unavailable", affinity)
        return
    try:
        process = psutil.Process(pid)
    except psutil.NoSuchProcess:
        log.warning("Failed to apply cpu_affinity=%s: process %s exited prematurely", affinity, pid)
        return
    except psutil.Error as exc:
        log.warning("Failed to apply cpu_affinity=%s on pid=%s: %s", affinity, pid, exc)
        return

    cpu_affinity = getattr(process, "cpu_affinity", None)
    if cpu_affinity is None:
        log.info("cpu_affinity is not supported on this platform; skipping assignment")
        return
    try:
        cpu_affinity(list(affinity))
        log.debug("Applied cpu_affinity=%s to pid=%s", affinity, pid)
    except psutil.Error as exc:
        log.warning("Failed to set cpu_affinity=%s on pid=%s: %s", affinity, pid, exc)


def _wrap_with_taskset(engine_cmd: str, affinity: tuple[int, ...] | None) -> str:
    """Wrap an engine exec command with taskset when affinity is provided."""
    if not affinity:
        return f"exec {engine_cmd}"

    mask = ",".join(str(cpu) for cpu in affinity)
    quoted_mask = shlex.quote(mask)
    warn_msg = f"[WARN] taskset not found on remote host; running engine without cpu affinity (requested: {mask})"
    quoted_warn = shlex.quote(warn_msg)
    return (
        "if command -v taskset >/dev/null 2>&1; then "
        f"exec taskset -c {quoted_mask} {engine_cmd}; "
        "else "
        f"echo {quoted_warn} >&2; "
        f"exec {engine_cmd}; "
        "fi"
    )


class EngineProcess(ABC):
    """Abstract base class for engine processes."""

    @property
    @abstractmethod
    def stdin(self) -> asyncio.StreamWriter | None:
        """Get stdin stream for sending commands."""
        pass

    @property
    @abstractmethod
    def stdout(self) -> asyncio.StreamReader | None:
        """Get stdout stream for reading responses."""
        pass

    @property
    @abstractmethod
    def stderr(self) -> asyncio.StreamReader | None:
        """Get stderr stream for diagnostics (optional)."""
        pass

    @abstractmethod
    async def wait(self) -> int:
        """Wait for process to complete and return exit code."""
        pass

    @abstractmethod
    def terminate(self) -> None:
        """Terminate the process gracefully."""
        pass

    @abstractmethod
    def kill(self) -> None:
        """Kill the process forcefully."""
        pass

    @property
    @abstractmethod
    def returncode(self) -> int | None:
        """Get process return code if available."""
        pass

    @property
    @abstractmethod
    def pid(self) -> int | None:
        """Get process ID if available."""
        pass


class LocalEngineProcess(EngineProcess):
    """Wrapper for local asyncio subprocess."""

    def __init__(self, process: asyncio.subprocess.Process) -> None:
        """Initialize with asyncio subprocess."""
        self.process = process

    @property
    def stdin(self) -> asyncio.StreamWriter | None:
        """Get stdin stream."""
        return self.process.stdin

    @property
    def stdout(self) -> asyncio.StreamReader | None:
        """Get stdout stream."""
        return self.process.stdout

    @property
    def stderr(self) -> asyncio.StreamReader | None:
        """Get stderr stream."""
        return self.process.stderr

    async def wait(self) -> int:
        """Wait for process completion."""
        return await self.process.wait()

    def terminate(self) -> None:
        """Terminate process gracefully."""
        self.process.terminate()

    def kill(self) -> None:
        """Kill process forcefully."""
        self.process.kill()

    @property
    def returncode(self) -> int | None:
        """Get return code."""
        return self.process.returncode

    @property
    def pid(self) -> int | None:
        """Get process ID."""
        return self.process.pid


class SSHEngineProcess(EngineProcess):
    """Wrapper for asyncssh client process that tunnels engine communication."""

    def __init__(
        self,
        instance: Instance,
        conn: asyncssh.SSHClientConnection,
        proc: asyncssh.SSHClientProcess[bytes],
        *,
        probe_interval: float = 1.0,
    ) -> None:
        """Initialize with asyncssh connection and process."""
        self._instance = instance
        self._conn = conn
        self._proc = proc
        self._closed = False
        self._probe_interval = probe_interval
        self._probe_task: asyncio.Task[None] | None = None
        if instance.is_ssh:
            try:
                self._probe_task = instance.start_network_probe(conn, interval=probe_interval)
            except (RuntimeError, OSError) as exc:
                logger.debug("Failed to start network probe for %s: %s", instance.name, exc, exc_info=True)
                self._probe_task = None

    @property
    def stdin(self) -> asyncio.StreamWriter | None:
        return cast(asyncio.StreamWriter, self._proc.stdin)

    @property
    def stdout(self) -> asyncio.StreamReader | None:
        return cast(asyncio.StreamReader, self._proc.stdout)

    @property
    def stderr(self) -> asyncio.StreamReader | None:
        return cast(asyncio.StreamReader, self._proc.stderr)

    async def wait(self) -> int:
        wait_result = await self._proc.wait()
        if isinstance(wait_result, asyncssh.SSHCompletedProcess):
            exit_status = wait_result.exit_status
            if not isinstance(exit_status, int):
                raise RuntimeError("SSH process completed without an integer exit status")
            code = exit_status
        else:
            code = int(wait_result)
        if not self._closed:
            try:
                self._conn.close()
                await self._conn.wait_closed()
            except (asyncssh.Error, OSError) as exc:
                logger.debug("Failed to close SSH connection after wait: %s", exc)
            self._closed = True
        if self._probe_task is not None:
            await self._instance.stop_network_probe(self._probe_task)
            self._probe_task = None
        return code

    def terminate(self) -> None:
        if self._probe_task is not None:
            self._instance.cancel_network_probe(self._probe_task)
        try:
            self._proc.terminate()
        except (asyncssh.Error, OSError, RuntimeError) as exc:
            logger.debug("Failed to terminate SSH engine process cleanly: %s", exc)

    def kill(self) -> None:
        if self._probe_task is not None:
            self._instance.cancel_network_probe(self._probe_task)
        try:
            self._proc.kill()
        except (asyncssh.Error, OSError, RuntimeError) as exc:
            logger.debug("Failed to kill SSH engine process: %s", exc)

    @property
    def returncode(self) -> int | None:
        try:
            value = self._proc.exit_status
            if isinstance(value, int):
                return value
            return None
        except (AttributeError, TypeError, ValueError):
            return None

    @property
    def pid(self) -> int | None:
        # Remote process ID is not exposed in a portable way here
        return None


class EngineProcessSpawner:
    """Factory for spawning engine processes on different instance types."""

    @staticmethod
    async def spawn(
        instance: Instance,
        engine_path: str,
        working_dir: str | None = None,
        env: dict[str, str] | None = None,
        engine_args: list[str] | None = None,
        cpu_affinity: tuple[int, ...] | None = None,
    ) -> EngineProcess:
        """
        Spawn an engine process on the specified instance.

        Args:
            instance: Instance to run the engine on
            engine_path: Path to engine executable (relative to engine_dir or absolute)
            working_dir: Working directory (defaults to engine executable's parent directory)
            env: Environment variables
            engine_args: Additional engine arguments

        Returns:
            EngineProcess wrapper for the spawned process

        Raises:
            RuntimeError: If process spawning fails
            ValueError: If instance configuration is invalid
        """
        if instance.is_local:
            return await EngineProcessSpawner._spawn_local(
                instance, engine_path, working_dir, env, engine_args, cpu_affinity
            )
        elif instance.is_ssh:
            return await EngineProcessSpawner._spawn_ssh(
                instance, engine_path, working_dir, env, engine_args, cpu_affinity
            )
        else:
            raise ValueError(f"Unsupported instance type: {instance.type}")

    @staticmethod
    async def _spawn_local(
        instance: Instance,
        engine_path: str,
        working_dir: str | None = None,
        env: dict[str, str] | None = None,
        engine_args: list[str] | None = None,
        cpu_affinity: tuple[int, ...] | None = None,
    ) -> LocalEngineProcess:
        """Spawn local engine process."""
        # Resolve engine executable path relative to instance.engine_dir when needed
        if instance.config.engine_dir:
            engine_dir_path_raw = Path(instance.config.engine_dir)
            engine_dir_path = (
                engine_dir_path_raw
                if engine_dir_path_raw.is_absolute()
                else (Path.cwd() / engine_dir_path_raw).resolve()
            )
        else:
            engine_dir_path = Path.cwd().resolve()

        engine_path_obj = Path(engine_path)
        if engine_path_obj.is_absolute():
            resolved_engine_path = engine_path_obj
        else:
            resolved_engine_path = (engine_dir_path / engine_path_obj).resolve()
        # Use provided working_dir when specified; otherwise default to engine's parent directory
        work_dir_path = Path(working_dir) if working_dir else resolved_engine_path.parent
        if not work_dir_path.is_absolute():
            base_dir = engine_dir_path if instance.config.engine_dir else Path.cwd().resolve()
            work_dir = (base_dir / work_dir_path).resolve()
        else:
            work_dir = work_dir_path

        # Build command
        cmd = [str(resolved_engine_path)]
        if engine_args:
            cmd.extend(engine_args)

        logger.debug(f"Starting local engine: {' '.join(cmd)} in {work_dir}")

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=work_dir,
                env=env,
            )

            logger.debug(f"Local engine started: pid={process.pid}")

            if cpu_affinity:
                _apply_cpu_affinity(process.pid, cpu_affinity, logger)

            return LocalEngineProcess(process)

        except (OSError, ValueError) as exc:
            logger.error("Failed to start local engine %s: %s", resolved_engine_path, exc)
            raise RuntimeError(f"Failed to start local engine: {exc}") from exc

    @staticmethod
    async def _spawn_ssh(
        instance: Instance,
        engine_path: str,
        working_dir: str | None = None,
        env: dict[str, str] | None = None,
        engine_args: list[str] | None = None,
        cpu_affinity: tuple[int, ...] | None = None,
    ) -> SSHEngineProcess:
        """Spawn SSH engine process via asyncssh (no system ssh dependency)."""
        config = instance.config
        if not config.host or not config.user:
            raise ValueError("SSH instance must have host and user configured")

        def _is_remote_absolute(path_str: str) -> bool:
            return path_str.startswith(("/", "~", "$"))

        # Build remote command string (bash -lc) to prepare env/cwd and exec engine
        # Resolve $ENGINE_DIR on remote via eval echo when provided
        remote_parts: list[str] = []
        if config.engine_dir:
            remote_parts.append(f"ENGINE_DIR=$(eval echo {shlex.quote(config.engine_dir)})")

        if working_dir:
            remote_wd = Path(working_dir)
            if not remote_wd.is_absolute() and not _is_remote_absolute(working_dir) and config.engine_dir:
                remote_parts.append('cd "$ENGINE_DIR"/' + shlex.quote(remote_wd.as_posix()))
            else:
                remote_parts.append(f"cd {shlex.quote(remote_wd.as_posix())}")
        elif config.engine_dir:
            remote_parts.append('cd "$ENGINE_DIR"')

        if env:
            for key, value in env.items():
                remote_parts.append(f"export {key}={shlex.quote(value)}")

        # Resolve engine executable path
        if Path(engine_path).is_absolute() or _is_remote_absolute(engine_path):
            full_engine_expr = shlex.quote(engine_path)
        else:
            if config.engine_dir:
                full_engine_expr = '"$ENGINE_DIR"/' + shlex.quote(engine_path)
            else:
                full_engine_expr = shlex.quote(engine_path)

        engine_cmd_parts = [full_engine_expr]
        if engine_args:
            engine_cmd_parts.extend(shlex.quote(arg) for arg in engine_args)
        engine_exec = " ".join(engine_cmd_parts)
        remote_parts.append(_wrap_with_taskset(engine_exec, cpu_affinity))
        remote_command = " && ".join(remote_parts)

        # Connect via asyncssh
        client_keys = [config.identity_file] if config.identity_file else None
        try:
            if not config.strict_host_key_checking:
                conn = await asyncssh.connect(
                    config.host,
                    port=config.port,
                    username=config.user,
                    client_keys=client_keys,
                    known_hosts=None,
                )
            else:
                conn = await asyncssh.connect(
                    config.host,
                    port=config.port,
                    username=config.user,
                    client_keys=client_keys,
                )

            async def _create_binary_process(
                command: str,
                *,
                legacy_command: Sequence[str] | None = None,
            ) -> asyncssh.SSHClientProcess[bytes]:
                try:
                    return cast(asyncssh.SSHClientProcess[bytes], await conn.create_process(command, encoding=None))
                except TypeError:
                    if legacy_command is not None:
                        proc_tmp = await conn.create_process(list(legacy_command))
                    else:
                        proc_tmp = await conn.create_process(command)
                    for attr in ("stdin", "stdout", "stderr"):
                        stream = getattr(proc_tmp, attr, None)
                        if stream is not None and hasattr(stream, "set_encoding"):
                            stream.set_encoding(None)
                    return cast(asyncssh.SSHClientProcess[bytes], proc_tmp)

            try:
                uname_proc = await _create_binary_process("uname -s", legacy_command=("uname", "-s"))
                if uname_proc.stdout is not None:
                    uname_raw = await uname_proc.stdout.read()
                else:
                    uname_raw = ""
                await uname_proc.wait()

                if uname_proc.exit_status != 0:
                    logger.warning(
                        "Failed to detect remote OS via uname on %s (exit=%s); assuming Linux compatibility",
                        config.host,
                        uname_proc.exit_status,
                    )
                else:
                    normalized = uname_raw.decode() if isinstance(uname_raw, bytes) else str(uname_raw)
                    normalized = normalized.strip()
                    if "linux" not in normalized.lower():
                        raise RuntimeError(
                            f"Remote instance '{config.host}' reports unsupported OS via uname: {normalized}. "
                            "Only Linux targets are supported."
                        )

                bash_cmd = "bash -lc " + shlex.quote(remote_command)
                proc = await _create_binary_process(bash_cmd, legacy_command=("bash", "-lc", remote_command))
                logger.debug("SSH engine started via asyncssh (affinity=%s)", cpu_affinity)
                return SSHEngineProcess(instance, conn, proc)
            except (asyncssh.Error, OSError, RuntimeError, ValueError, TypeError) as exc:
                conn.close()
                try:
                    await conn.wait_closed()
                except (asyncssh.Error, OSError) as close_exc:
                    logger.debug("Failed to await SSH connection close: %s", close_exc)
                logger.debug("SSH engine spawn failed: %s", exc, exc_info=True)
                raise
        except (asyncssh.Error, OSError, RuntimeError, ValueError) as exc:
            logger.error("Failed to start SSH engine on %s: %s", config.host, exc)
            raise RuntimeError(f"Failed to start SSH engine: {exc}") from exc
