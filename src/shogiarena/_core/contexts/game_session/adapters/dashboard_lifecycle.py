"""Dashboard asset and server lifecycle management.

Manages dashboard HTML/CSS/JS asset generation and API server startup/shutdown.
Interface-layer dependencies (``ArenaAPIServer``, ``init_dashboard_html``) are
resolved via port registries populated during bootstrap, avoiding any static or
dynamic dependency on the ``interfaces`` layer.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from shogiarena._core.contexts.game_session.ports.dashboard_lifecycle_factory import (
    DashboardApiServerFactory,
    DashboardScheduleBoundaryPort,
    InitDashboardHtmlFn,
)
from shogiarena._core.contexts.game_session.ports.session_lifecycle_ports import DashboardProfile
from shogiarena._core.contexts.instances.application.instance_pool import InstancePool

logger = logging.getLogger(__name__)


class DashboardLifecycleCoordinator:
    """Manage dashboard assets and API server lifecycle."""

    def __init__(
        self,
        *,
        instance_pool: InstancePool | None,
        schedule_boundary: DashboardScheduleBoundaryPort | None,
        profiles: tuple[DashboardProfile, ...],
        init_dashboard_html: InitDashboardHtmlFn,
        api_server_factory: DashboardApiServerFactory,
    ) -> None:
        self._instance_pool = instance_pool
        self._schedule_boundary = schedule_boundary
        self._profiles = profiles
        self._init_dashboard_html = init_dashboard_html
        self._api_server_factory = api_server_factory
        self.api_server: Any | None = None

    def ensure_assets(self, run_dir: Path, num_workers: int) -> None:
        self._init_dashboard_html(run_dir / "dashboard", num_workers=num_workers, profiles=self._profiles)

    async def start_server(self, run_dir: Path, host: str, preferred_port: int, num_workers: int) -> int:
        """Start dashboard API server with port fallback. Returns actual_port."""
        self.ensure_assets(run_dir, num_workers=num_workers)

        create_api_server = self._api_server_factory

        port = int(preferred_port)

        def pick_free_port(start: int, max_tries: int = 20) -> int:
            p = start
            for _ in range(max_tries):
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    try:
                        sock.bind((host, p))
                        return p
                    except OSError as exc:
                        logger.debug("Dashboard port %s unavailable: %s", p, exc)
                        p += 1
            return start

        chosen = pick_free_port(port)
        if chosen != port:
            logger.info("Dashboard port %s is in use; switching to %s", port, chosen)
            port = chosen

        srv = create_api_server(
            run_dir / "game.db",
            port,
            run_dir,
            self._instance_pool,
            host=host,
            schedule_boundary=self._schedule_boundary,
        )
        await srv.start()
        self.api_server = srv

        port_js = run_dir / "dashboard" / "data" / "arena_port.js"
        port_js.parent.mkdir(parents=True, exist_ok=True)
        port_js.write_text(f"window.ARENA_API_PORT = {port};\n", encoding="utf-8")

        logger.debug("Output directory: %s", str(run_dir))
        index_host = "localhost" if host in {"127.0.0.1", "localhost"} else host
        index_url = f"http://{index_host}:{port}/index.html"
        logger.info("Dashboard URL: %s", index_url)
        return port

    async def stop_server(self) -> None:
        srv = self.api_server
        if not srv:
            return
        await asyncio.wait_for(srv.stop(), timeout=3.0)
        self.api_server = None

    @staticmethod
    def cleanup_run_dir(run_dir: Path, *, files: list[str] | None = None, dirs: list[str] | None = None) -> None:
        files = files or []
        dirs = dirs or []

        for name in files:
            target = run_dir / name
            if target.exists():
                target.unlink()
                logger.debug("Removed %s", target)
        for name in dirs:
            target = run_dir / name
            if target.exists():
                shutil.rmtree(target)
                logger.debug("Removed directory %s", target)

    @staticmethod
    def cleanup_dashboard_assets(run_dir: Path) -> None:
        DashboardLifecycleCoordinator.cleanup_run_dir(
            run_dir,
            files=[
                "dashboard/index.html",
                "dashboard/data/shogi-board.js",
                "dashboard/data/arena_port.js",
            ],
            dirs=["dashboard/static", "dashboard/html"],
        )


@dataclass
class DashboardCoordinator:
    manager: DashboardLifecycleCoordinator
    api_server: Any | None = None

    def ensure_assets(self, run_dir: Path, num_workers: int) -> None:
        self.manager.ensure_assets(run_dir, num_workers)

    async def start_server(self, run_dir: Path, host: str, preferred_port: int, num_workers: int) -> int:
        port = await self.manager.start_server(run_dir, host, preferred_port, num_workers)
        self.api_server = self.manager.api_server
        return port

    async def stop_server(self) -> None:
        await self.manager.stop_server()
        self.api_server = self.manager.api_server
