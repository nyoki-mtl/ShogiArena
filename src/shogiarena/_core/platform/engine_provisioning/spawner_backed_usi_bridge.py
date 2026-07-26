"""Spawner-backed USI bridge for local/remote engine processes."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any, Protocol, cast, runtime_checkable

from shogiarena._core.platform.engine_provisioning.provisioning_ports import NamedInstancePort
from shogiarena._core.shared.kernel.engine_process import EngineProcessInfo


@runtime_checkable
class _EngineProcessPort(Protocol):
    stdin: asyncio.StreamWriter | None
    stdout: asyncio.StreamReader | None
    stderr: asyncio.StreamReader | None

    async def wait(self) -> int: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...

    @property
    def returncode(self) -> int | None: ...

    @property
    def pid(self) -> int | None: ...


EngineProcessSpawnerFn = Callable[..., Awaitable[Any]]


logger = logging.getLogger(__name__)

# ``ObservationBasis`` の value と一致させる（shared kernel への依存を platform 層に持ち込まない）。
_LOCAL_PIPE_BASIS = "local_pipe"
_REMOTE_TRANSPORT_BASIS = "remote_transport"


def _is_local_instance(instance: NamedInstancePort) -> bool:
    """ローカル subprocess として起動される instance か。判定できない場合は False（fail closed）。"""

    is_ssh = getattr(instance, "is_ssh", None)
    return is_ssh is False


class SpawnerBackedUSIBridge:
    """Thin USI bridge backed by EngineProcessSpawner (local or SSH)."""

    GRACEFUL_STOP_TIMEOUT_SECONDS = 5.0
    KILL_WAIT_TIMEOUT_SECONDS = 5.0

    def __init__(
        self,
        *,
        instance: NamedInstancePort,
        engine_path: str,
        working_dir: str | None = None,
        name: str | None = None,
        engine_args: list[str] | None = None,
        env: dict[str, str] | None = None,
        cpu_affinity: tuple[int, ...] | None = None,
        engine_spawner: EngineProcessSpawnerFn | None = None,
    ) -> None:
        self.instance = instance
        self.engine_path = engine_path
        self.working_dir = working_dir
        self._name = name or f"{instance.name}:{Path(engine_path).stem}"
        self._engine_args = engine_args or []
        self._env = env
        self._cpu_affinity = cpu_affinity
        self._engine_spawner = engine_spawner

        self.process: _EngineProcessPort | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._stderr_handler: Callable[[str], None] | None = None
        self._is_stopping = False
        self._reap_tasks: set[asyncio.Task[None]] = set()
        # timeout attribution 用の観測時刻（task 0052）。``receive_lines`` は消費側が処理を
        # 終えるまで次の ``readline`` へ進まないため、消費側が読む時点の値は必ずその行のもの。
        self._last_line_received_at_s: float | None = None
        # SSH 経由では engine の出力が network を越えるので、ローカルの watchdog は
        # delivery path を coverage できない。判定不能側へ倒すため basis を分ける。
        self._observation_basis = _LOCAL_PIPE_BASIS if _is_local_instance(instance) else _REMOTE_TRANSPORT_BASIS

    @property
    def name(self) -> str:
        return self._name

    async def start_process(self) -> None:
        if self.process:
            raise RuntimeError(f"Engine process already started: {self.name}")
        logger.debug("Starting engine process on %s: %s", self.instance.name, self.engine_path)
        spawner = self._engine_spawner or _default_engine_spawner()
        self._engine_spawner = spawner
        spawned_process = await spawner(
            instance=self.instance,
            engine_path=self.engine_path,
            working_dir=self.working_dir,
            env=self._env,
            engine_args=self._engine_args,
            cpu_affinity=self._cpu_affinity,
        )
        if not isinstance(spawned_process, _EngineProcessPort):
            raise RuntimeError("EngineProcessSpawner returned an incompatible process object")
        self.process = spawned_process

        if self.process.stderr is not None:
            self._stderr_task = asyncio.create_task(self._read_stderr(), name=f"stderr-{self.name}")
        logger.debug("Engine process %s (pid: %s) started", self.name, self.process.pid if self.process else None)

    async def stop_process(self) -> None:
        if self._is_stopping:
            logger.debug("Stop already in progress for %s", self.name)
            return
        if not self.process:
            logger.debug("Process %s not running or already stopped", self.name)
            return

        self._is_stopping = True
        proc = self.process

        try:
            if self._stderr_task and not self._stderr_task.done():
                self._stderr_task.cancel()
                try:
                    await self._stderr_task
                except asyncio.CancelledError:
                    current_task = asyncio.current_task()
                    if current_task is not None and current_task.cancelling():
                        raise
                self._stderr_task = None

            if proc and proc.returncode is None:
                writer = proc.stdin
                if writer is not None and not writer.is_closing():
                    with contextlib.suppress(RuntimeError):
                        await self.send_line("quit")
                try:
                    await asyncio.wait_for(proc.wait(), timeout=self.GRACEFUL_STOP_TIMEOUT_SECONDS)
                except TimeoutError:
                    logger.warning("Process %s did not terminate gracefully, killing", self.name)
                    proc.kill()
                    try:
                        await asyncio.wait_for(proc.wait(), timeout=self.KILL_WAIT_TIMEOUT_SECONDS)
                    except TimeoutError:
                        logger.warning("Process %s did not exit after kill; continuing reap in background", self.name)
                        self._schedule_background_reap(proc)
        except asyncio.CancelledError:
            # Cancellation of shutdown must not detach a live child process. Kill
            # and reap it before preserving the caller's cancellation signal.
            try:
                await self._kill_and_reap_cancelled_stop(proc)
            finally:
                raise
        finally:
            self.process = None
            self._is_stopping = False
        logger.debug("Engine process %s stop procedure finished", self.name)

    async def _kill_and_reap_cancelled_stop(self, proc: _EngineProcessPort) -> None:
        if proc.returncode is not None:
            return
        logger.warning("Stop cancelled for %s; killing process before propagating cancellation", self.name)
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        except Exception as exc:  # pragma: no cover - defensive adapter boundary
            logger.warning("Failed to kill process %s during cancelled stop: %s", self.name, exc, exc_info=exc)

        try:
            await asyncio.wait_for(proc.wait(), timeout=self.KILL_WAIT_TIMEOUT_SECONDS)
        except TimeoutError:
            logger.warning("Process %s did not exit after cancelled stop; continuing reap in background", self.name)
            self._schedule_background_reap(proc)
        except Exception as exc:  # pragma: no cover - defensive adapter boundary
            logger.warning("Failed to reap process %s during cancelled stop: %s", self.name, exc, exc_info=exc)

    def _schedule_background_reap(self, proc: _EngineProcessPort) -> None:
        task = asyncio.create_task(self._reap_process(proc), name=f"reap-{self.name}")
        self._reap_tasks.add(task)

        def _consume_reap_result(done_task: asyncio.Task[None]) -> None:
            self._reap_tasks.discard(done_task)
            if done_task.cancelled():
                return
            exc = done_task.exception()
            if exc is not None:
                logger.warning("Background reap failed for %s: %s", self.name, exc, exc_info=exc)

        task.add_done_callback(_consume_reap_result)

    async def _reap_process(self, proc: _EngineProcessPort) -> None:
        await proc.wait()
        logger.debug("Background reap completed for %s", self.name)

    async def send_line(self, command: str) -> None:
        proc = self.process
        writer = proc.stdin if proc is not None else None
        if proc is None or writer is None or writer.is_closing():
            msg = f"Engine process {self.name} not running or stdin closed, cannot send: {command}"
            if self._is_stopping or command.strip().lower() == "stop":
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
                    handler = self._stderr_handler
                    if handler is not None:
                        handler(stderr_line)
        except asyncio.CancelledError:
            logger.debug("Stderr monitor cancelled for %s", self.name)
            raise

    def set_stderr_handler(self, handler: Callable[[str], None] | None) -> None:
        self._stderr_handler = handler

    def get_process_info(self) -> EngineProcessInfo | None:
        proc = self.process
        pid = proc.pid if proc is not None else None
        if pid is None:
            return None
        return EngineProcessInfo(
            pid=pid,
            executable=self.engine_path,
            working_directory=self.working_dir,
            command_line=(self.engine_path, *self._engine_args),
        )

    async def receive_lines(self) -> AsyncIterator[str]:
        proc = self.process
        if proc is None or proc.stdout is None:
            msg = f"Engine process {self.name} not running or stdout unavailable"
            logger.error(msg)
            raise RuntimeError(msg)
        try:
            while True:
                try:
                    line_bytes = await proc.stdout.readline()
                except ValueError as exc:
                    # readline() raises ValueError when a single line exceeds the stream buffer
                    # limit; it discards that line from the buffer, so skip it and keep reading
                    # instead of tearing down the engine session.
                    logger.warning("Skipping oversized line from %s: %s", self.name, exc)
                    continue
                if not line_bytes:
                    break
                # decode、raw log dispatch、protocol parse、queue forwarding より前に採る。
                self._last_line_received_at_s = time.perf_counter()
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

    def last_line_received_at_s(self) -> float | None:
        """直近に受信した行の monotonic 時刻（``time.perf_counter`` 基底）。"""
        return self._last_line_received_at_s

    def observation_basis(self) -> str:
        """観測時刻の由来。``local_pipe`` だけが delivery coverage を主張できる。"""
        return self._observation_basis


def _default_engine_spawner() -> EngineProcessSpawnerFn:
    from shogiarena._core.contexts.instances.ports.engine_runtime_port import spawn_engine_process

    return cast(EngineProcessSpawnerFn, spawn_engine_process)


__all__ = ["SpawnerBackedUSIBridge"]
