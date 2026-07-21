"""Runtime and lifecycle helpers for Arena API server."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Mapping

from aiohttp import web

from shogiarena._core.contexts.dashboard.application.state_container import DashboardState
from shogiarena._core.interfaces.dashboard.instances.api import InstancesAPI
from shogiarena._core.interfaces.dashboard.security import DASHBOARD_READ_ONLY_KEY
from shogiarena._core.interfaces.dashboard.ws_server import LiveWebSocketHub
from shogiarena._core.shared.kernel.json_types import JsonValue

logger = logging.getLogger(__name__)


class ArenaApiServerRuntimeMixin:
    app: web.Application
    host: str
    port: int
    runner: web.AppRunner | None
    site: web.TCPSite | None
    _instances_health_interval: float
    _instances_health_task: asyncio.Task[None] | None
    instances_api: InstancesAPI
    ws_hub: LiveWebSocketHub
    _state: DashboardState

    def broadcast_summary_update(self, payload: Mapping[str, JsonValue], *, source: str = "tournament") -> None:
        raise NotImplementedError

    @staticmethod
    def _load_instances_health_interval() -> float:
        raw = os.getenv("SHOGI_ARENA_DASHBOARD_INSTANCES_HEALTH_INTERVAL", "").strip()
        if not raw:
            return 10.0
        try:
            parsed = float(raw)
        except ValueError:
            return 10.0
        return max(0.0, parsed)

    async def _instances_health_loop(self) -> None:
        interval = self._instances_health_interval
        if interval <= 0:
            return
        try:
            while True:
                start = time.monotonic()
                try:
                    await self.instances_api.run_health_checks(should_force=True)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    # Never let an unexpected error kill the health loop; log and keep going.
                    logger.warning("Instances health check loop iteration failed", exc_info=True)
                elapsed = time.monotonic() - start
                await asyncio.sleep(max(0.0, interval - elapsed))
        except asyncio.CancelledError:
            raise

    async def _on_startup(self, _app: web.Application) -> None:
        if _app.get(DASHBOARD_READ_ONLY_KEY, False):
            return
        if self._instances_health_interval <= 0:
            return
        if self._instances_health_task is not None:
            return
        self._instances_health_task = asyncio.create_task(
            self._instances_health_loop(),
            name="dashboard-instances-health-loop",
        )

    async def _on_cleanup(self, _app: web.Application) -> None:
        if self._instances_health_task is None:
            return
        self._instances_health_task.cancel()
        try:
            await self._instances_health_task
        except asyncio.CancelledError:
            pass
        except Exception:
            # The loop should swallow its own errors, but guard cleanup so shutdown
            # is never broken by a health task that died with an unexpected exception.
            logger.warning("Instances health task ended with an error", exc_info=True)
        self._instances_health_task = None

    @staticmethod
    def _load_ws_topic_ttl_seconds() -> float | None:
        raw = os.getenv("SHOGI_ARENA_DASHBOARD_WS_TOPIC_TTL_SECONDS", "").strip().lower()
        if not raw:
            return 30 * 60.0
        if raw in {"0", "off", "false", "no", "none"}:
            return None
        try:
            parsed = float(raw)
        except ValueError:
            logger.warning("Invalid SHOGI_ARENA_DASHBOARD_WS_TOPIC_TTL_SECONDS=%s; using default", raw)
            return 30 * 60.0
        if parsed <= 0:
            return None
        return parsed

    @staticmethod
    def _load_ws_max_topics() -> int:
        raw = os.getenv("SHOGI_ARENA_DASHBOARD_WS_MAX_TOPICS", "").strip()
        if not raw:
            return 20000
        try:
            parsed = int(raw)
        except ValueError:
            logger.warning("Invalid SHOGI_ARENA_DASHBOARD_WS_MAX_TOPICS=%s; using default", raw)
            return 20000
        return max(0, parsed)

    async def start(self) -> None:
        self.runner = web.AppRunner(self.app, shutdown_timeout=1.0)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, self.host, self.port)
        await self.site.start()
        logger.debug("Arena API server started on http://%s:%s", self.host, self.port)
        logger.debug("Dashboard available at http://localhost:%s/", self.port)

    async def stop(self) -> None:
        sources = list(self._state.get_summary_sources()) or ["tournament"]
        for source in sources:
            self.broadcast_summary_update({"tournament_ended": True}, source=source)

        if self.ws_hub is not None:
            await self.ws_hub.shutdown()

        if self.site:
            await self.site.stop()
            self.site = None

        if self.runner:
            try:
                await asyncio.wait_for(self.runner.cleanup(), timeout=3.0)
            except TimeoutError:
                logger.warning("API server cleanup timed out after 3 seconds")

        self._state.clear_worker_snapshots()
        logger.debug("Arena API server stopped")


__all__ = ["ArenaApiServerRuntimeMixin"]
