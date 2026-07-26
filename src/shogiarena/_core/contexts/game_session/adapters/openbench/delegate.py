"""OpenBench integration delegate for TournamentRunner.

Encapsulates all OpenBench client lifecycle, heartbeat management,
and configuration resolution so that ``TournamentRunner`` can delegate
to a single ``OpenBenchDelegate`` instance.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping
from contextlib import suppress
from pathlib import Path
from typing import Protocol, TypeAlias

from shogiarena._core.contexts.game_session.adapters.openbench.client import (
    OpenBenchClient,
)
from shogiarena._core.contexts.game_session.adapters.openbench.client_types import (
    OpenBenchClientConfig,
    OpenBenchError,
)
from shogiarena._core.contexts.game_session.adapters.openbench.delegate_config import (
    resolve_openbench_client_config,
)
from shogiarena._core.contexts.game_session.adapters.orchestration.config_tournament import TournamentRunConfig
from shogiarena._core.shared.kernel.json_coercion import to_json_object
from shogiarena._core.shared.kernel.json_types import JsonObject, JsonValue
from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_str
from shogiarena._core.shared.kernel.service_ports import DatabaseServicePort

logger = logging.getLogger(__name__)


class _StopController(Protocol):
    """Minimal protocol for requesting a tournament stop."""

    def request_stop(self, *, reason: str) -> None: ...


StopControllerProvider: TypeAlias = Callable[[], _StopController]
"""現在の stop controller を返す。**instance を直接持たない**（task 0052 / review 第7次 H1）。

run loop は pause のたびに controller を差し替える。heartbeat のような長寿命 producer が
起動時の instance を握ると、差し替え後の停止要求が死んだ controller に立ち、
実行中の run は止まらない。"""


class OpenBenchDelegate:
    """Owns OpenBench client lifecycle and heartbeat management."""

    def __init__(
        self,
        config: TournamentRunConfig,
        *,
        run_dir: Path,
        should_skip_resume: bool,
    ) -> None:
        self._config = config
        self._run_dir = run_dir
        self._no_resume = should_skip_resume
        self._client: OpenBenchClient | None = None
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._heartbeat_error: OpenBenchError | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def client(self) -> OpenBenchClient | None:
        return self._client

    async def init(self, *, stop_controller_provider: StopControllerProvider) -> None:
        """Resolve configuration and initialize the OpenBench client."""
        self._heartbeat_error = None
        await self._stop_heartbeat()
        if self._client is not None:
            await self._client.close()
            self._client = None
        cfg = self._resolve_config()
        if cfg is None:
            self._client = None
            return
        tested_engine = coerce_str(self._config.engines[0].name)
        base_engine = coerce_str(self._config.engines[1].name)
        if tested_engine is None or base_engine is None:
            raise ValueError("openbench requires non-empty engine names")
        client = OpenBenchClient(
            cfg,
            tested_engine=tested_engine,
            base_engine=base_engine,
        )
        try:
            await client.initialize()
        except OpenBenchError as exc:
            await client.close()
            if cfg.is_strict:
                raise
            logger.warning("OpenBench initialization failed; continuing (strict=false): %s", exc)
            self._client = None
            return
        self._client = client
        self._start_heartbeat(
            client=client,
            interval_sec=cfg.heartbeat_interval_sec,
            stop_controller_provider=stop_controller_provider,
        )

    async def sync_after_game(
        self,
        *,
        db_service: DatabaseServicePort | None,
        stop_controller: _StopController,
        persist_state: Callable[[], None],
    ) -> None:
        """Sync results to OpenBench after a game completion."""
        self.raise_pending_error()
        if self._client is None or db_service is None:
            return
        should_stop = await self._client.try_sync(db_service, persist_state=persist_state)
        if should_stop:
            stop_controller.request_stop(reason="openbench-stop")

    async def flush(
        self,
        *,
        db_service: DatabaseServicePort | None,
        stop_controller: _StopController,
        persist_state: Callable[[], None],
    ) -> None:
        """Flush all pending results to OpenBench."""
        self.raise_pending_error()
        if self._client is None or db_service is None:
            return
        should_stop = await self._client.try_flush(db_service, persist_state=persist_state)
        if should_stop:
            stop_controller.request_stop(reason="openbench-stop")

    async def stop(self) -> None:
        """Stop heartbeat task and close client."""
        await self._stop_heartbeat()
        self._heartbeat_error = None
        if self._client is not None:
            await self._client.close()
            self._client = None

    def raise_pending_error(self) -> None:
        """Re-raise any background heartbeat error."""
        if self._heartbeat_error is None:
            return
        exc = self._heartbeat_error
        self._heartbeat_error = None
        raise exc

    def restore_state(self, openbench_state: Mapping[str, JsonValue]) -> None:
        """Restore client state from a persisted run state."""
        if self._client is not None and openbench_state is not None:
            self._client.restore_state(openbench_state)

    def snapshot_state(self) -> JsonObject | None:
        """Return client state for persistence, or ``None`` if inactive."""
        if self._client is None:
            return None
        return to_json_object(self._client.snapshot_state())

    # ------------------------------------------------------------------
    # Heartbeat management
    # ------------------------------------------------------------------

    def _start_heartbeat(
        self,
        *,
        client: OpenBenchClient,
        interval_sec: float,
        stop_controller_provider: StopControllerProvider,
    ) -> None:
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
        self._heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(
                client=client, interval_sec=interval_sec, stop_controller_provider=stop_controller_provider
            ),
            name="openbench-heartbeat",
        )

    async def _stop_heartbeat(self) -> None:
        task = self._heartbeat_task
        self._heartbeat_task = None
        if task is None:
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    async def _heartbeat_loop(
        self,
        *,
        client: OpenBenchClient,
        interval_sec: float,
        stop_controller_provider: StopControllerProvider,
    ) -> None:
        try:
            while True:
                await asyncio.sleep(interval_sec)
                if self._client is not client:
                    return
                try:
                    should_stop = await client.should_stop_after_heartbeat()
                except OpenBenchError as exc:
                    if client.is_strict:
                        logger.error("OpenBench heartbeat failed in strict mode: %s", exc)
                        self._heartbeat_error = exc
                        # 停止のたびに現在の controller を解決する。起動時の instance を
                        # 握ると、reschedule 後の停止要求が死んだ controller に立つ。
                        stop_controller_provider().request_stop(reason="openbench-heartbeat-error")
                        return
                    logger.warning("OpenBench heartbeat failed; continuing (strict=false): %s", exc)
                    continue
                if should_stop:
                    stop_controller_provider().request_stop(reason="openbench-stop")
                    return
        except asyncio.CancelledError:
            return

    # ------------------------------------------------------------------
    # Configuration resolution
    # ------------------------------------------------------------------

    def _resolve_config(self) -> OpenBenchClientConfig | None:
        return resolve_openbench_client_config(
            self._config,
            run_dir=self._run_dir,
            should_skip_resume=self._no_resume,
        )


__all__ = [
    "OpenBenchDelegate",
]
