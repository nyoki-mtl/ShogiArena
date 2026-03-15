"""Engine process handle abstractions and shared helpers."""

import asyncio
import logging
import shlex
from abc import ABC, abstractmethod
from typing import Any, Protocol

import asyncssh
import psutil

from .instance_models import Instance

logger = logging.getLogger(__name__)


class ProcessStreamWriterPort(Protocol):
    def write(self, data: bytes) -> object: ...
    async def drain(self) -> object: ...
    def is_closing(self) -> bool: ...


class ProcessStreamReaderPort(Protocol):
    async def readline(self) -> bytes: ...


def apply_cpu_affinity(pid: int | None, affinity: tuple[int, ...], log: logging.Logger) -> None:
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


def wrap_with_taskset(engine_cmd: str, affinity: tuple[int, ...] | None) -> str:
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
    def stdin(self) -> ProcessStreamWriterPort | None:
        """Get stdin stream for sending commands."""
        pass

    @property
    @abstractmethod
    def stdout(self) -> ProcessStreamReaderPort | None:
        """Get stdout stream for reading responses."""
        pass

    @property
    @abstractmethod
    def stderr(self) -> ProcessStreamReaderPort | None:
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
    def stdin(self) -> ProcessStreamWriterPort | None:
        """Get stdin stream."""
        return self.process.stdin

    @property
    def stdout(self) -> ProcessStreamReaderPort | None:
        """Get stdout stream."""
        return self.process.stdout

    @property
    def stderr(self) -> ProcessStreamReaderPort | None:
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
        proc: asyncssh.SSHClientProcess[Any],
        *,
        probe_interval: float = 1.0,
    ) -> None:
        """Initialize with asyncssh connection and process."""
        self._instance = instance
        self._conn = conn
        self._proc = proc
        self._is_closed = False
        self._probe_task: asyncio.Task[None] | None = None
        if instance.is_ssh:
            try:
                self._probe_task = instance.start_network_probe(conn, interval=probe_interval)
            except (RuntimeError, OSError) as exc:
                logger.debug("Failed to start network probe for %s: %s", instance.name, exc, exc_info=True)
                self._probe_task = None

    @property
    def stdin(self) -> ProcessStreamWriterPort | None:
        return self._proc.stdin

    @property
    def stdout(self) -> ProcessStreamReaderPort | None:
        return self._proc.stdout

    @property
    def stderr(self) -> ProcessStreamReaderPort | None:
        return self._proc.stderr

    async def wait(self) -> int:
        wait_result = await self._proc.wait()
        if isinstance(wait_result, asyncssh.SSHCompletedProcess):
            exit_status = wait_result.exit_status
            if not isinstance(exit_status, int):
                raise RuntimeError("SSH process completed without an integer exit status")
            code = exit_status
        else:
            code = int(wait_result)
        if not self._is_closed:
            try:
                self._conn.close()
                await self._conn.wait_closed()
            except (asyncssh.Error, OSError) as exc:
                logger.debug("Failed to close SSH connection after wait: %s", exc)
            self._is_closed = True
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
        except (AttributeError, TypeError, ValueError) as exc:
            logger.debug("Failed to read SSH engine return code: %s", exc)
            return None

    @property
    def pid(self) -> int | None:
        return None


__all__ = [
    "EngineProcess",
    "LocalEngineProcess",
    "SSHEngineProcess",
    "apply_cpu_affinity",
    "wrap_with_taskset",
]
