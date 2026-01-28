"""
Spawner-backed USI bridge that unifies local and SSH execution.

Implements AsyncUSIProcessBridgeProtocol and delegates process creation to
EngineProcessSpawner based on the provided Instance.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from pathlib import Path

from shogiarena.arena.engines.usi_bridge import AsyncUSIProcessBridgeProtocol
from shogiarena.arena.instances.models import Instance
from shogiarena.arena.instances.spawner import EngineProcess, EngineProcessSpawner

logger = logging.getLogger(__name__)


class SpawnerBackedUSIBridge(AsyncUSIProcessBridgeProtocol):
    """Thin USI bridge backed by EngineProcessSpawner (local or SSH).

    - Exposes a simple process lifecycle and USI I/O surface.
    - Delegates process creation to EngineProcessSpawner based on the Instance.
    - Avoids hidden fallbacks; errors are logged and propagated.
    """

    def __init__(
        self,
        *,
        instance: Instance,
        engine_path: str,
        working_dir: str | None = None,
        name: str | None = None,
        engine_args: list[str] | None = None,
        env: dict[str, str] | None = None,
        cpu_affinity: tuple[int, ...] | None = None,
    ) -> None:
        self.instance = instance
        self.engine_path = engine_path
        self.working_dir = working_dir
        # Keep a private backing field and expose read-only @property to
        # satisfy AsyncUSIProcessBridgeProtocol (read-only `.name`).
        self._name = name or f"{instance.name}:{Path(engine_path).stem}"
        self._engine_args = engine_args or []
        self._env = env
        self._cpu_affinity = cpu_affinity

        self.process: EngineProcess | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._stopping = False

    @property
    def name(self) -> str:
        """Engine name for logging and diagnostics (read-only)."""
        return self._name

    async def start_process(self) -> None:
        if self.process:
            raise RuntimeError(f"Engine process already started: {self.name}")
        logger.debug("Starting engine process on %s: %s", self.instance.name, self.engine_path)
        self.process = await EngineProcessSpawner.spawn(
            instance=self.instance,
            engine_path=self.engine_path,
            working_dir=self.working_dir,
            env=self._env,
            engine_args=self._engine_args,
            cpu_affinity=self._cpu_affinity,
        )

        if getattr(self.process, "stderr", None) is not None:
            self._stderr_task = asyncio.create_task(self._read_stderr(), name=f"stderr-{self.name}")
        logger.debug("Engine process %s (pid: %s) started", self.name, self.process.pid if self.process else None)

    async def stop_process(self) -> None:
        if self._stopping:
            logger.debug("Stop already in progress for %s", self.name)
            return
        if not self.process:
            logger.debug("Process %s not running or already stopped", self.name)
            return

        self._stopping = True

        if self._stderr_task and not self._stderr_task.done():
            self._stderr_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._stderr_task
            self._stderr_task = None

        proc = self.process
        if proc and proc.returncode is None:
            writer = proc.stdin
            if writer is not None and not writer.is_closing():
                with contextlib.suppress(RuntimeError):
                    await self.send_line("quit")
            try:
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                logger.warning("Process %s did not terminate gracefully, killing", self.name)
                proc.kill()
                await proc.wait()

        self.process = None
        self._stopping = False
        logger.debug("Engine process %s stop procedure finished", self.name)

    async def send_line(self, command: str) -> None:
        proc = self.process
        writer = proc.stdin if proc is not None else None
        if proc is None or writer is None or writer.is_closing():
            msg = f"Engine process {self.name} not running or stdin closed, cannot send: {command}"
            if self._stopping:
                logger.debug(msg)
            else:
                logger.error(msg)
            raise RuntimeError(msg)
        logger.debug("[%s] > %s", self.name, command)
        try:
            writer.write(f"{command}\n".encode())
            await writer.drain()
        except (BrokenPipeError, ConnectionResetError) as exc:
            await self.stop_process()
            raise RuntimeError(f"Failed to send command due to broken pipe: {command}") from exc

    async def _read_stderr(self) -> None:
        proc = self.process
        if proc is None or proc.stderr is None:
            return
        try:
            while True:
                line = await proc.stderr.readline()
                if not line:
                    break
                stderr_line = line.decode("utf-8", errors="replace").rstrip("\r\n")
                if stderr_line:
                    logger.debug("[ERR %s] %s", self.name, stderr_line)
        except asyncio.CancelledError:
            logger.debug("Stderr monitor cancelled for %s", self.name)
            raise

    # Protocol method name (async generator)
    async def receive_lines(self) -> AsyncIterator[str]:
        proc = self.process
        if proc is None or proc.stdout is None:
            msg = f"Engine process {self.name} not running or stdout unavailable"
            logger.error(msg)
            raise RuntimeError(msg)
        try:
            while True:
                line_bytes = await proc.stdout.readline()
                if not line_bytes:
                    break
                yield line_bytes.decode("utf-8", errors="replace").rstrip("\r\n")
        except asyncio.CancelledError:
            logger.debug("Receive lines cancelled for %s", self.name)
            raise
        except (asyncio.IncompleteReadError, ConnectionResetError) as exc:
            logger.warning("Connection issue while reading from %s: %s", self.name, exc)
            raise RuntimeError(f"Connection lost while reading from engine {self.name}") from exc
        finally:
            logger.debug("Stopped receiving lines from %s", self.name)

    def is_running(self) -> bool:
        return self.process is not None and self.process.returncode is None
