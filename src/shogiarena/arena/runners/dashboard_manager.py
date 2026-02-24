"""Dashboard lifecycle helpers for session runners."""

from __future__ import annotations

import asyncio
import logging
import shutil
import socket
from pathlib import Path
from typing import Any

from shogiarena.arena.instances.pool import InstancePool
from shogiarena.web.dashboard.api_server import ArenaAPIServer
from shogiarena.web.dashboard.backend.assets_writer import DashboardProfile, init_dashboard_html

logger = logging.getLogger(__name__)


class DashboardManager:
    """Manage dashboard assets and API server lifecycle."""

    def __init__(
        self,
        *,
        instance_pool: InstancePool | None,
        session_runner: Any,
        profiles: tuple[DashboardProfile, ...],
    ) -> None:
        self._instance_pool = instance_pool
        self._session_runner = session_runner
        self._profiles = profiles
        self.api_server: ArenaAPIServer | None = None

    def ensure_assets(self, run_dir: Path, num_workers: int) -> None:
        init_dashboard_html(run_dir, num_workers=num_workers, profiles=self._profiles)

    async def start_server(self, run_dir: Path, preferred_port: int, num_workers: int) -> int:
        """Start dashboard API server with port fallback. Returns actual_port."""
        self.ensure_assets(run_dir, num_workers=num_workers)

        port = int(preferred_port)

        def pick_free_port(start: int, max_tries: int = 20) -> int:
            p = start
            for _ in range(max_tries):
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    try:
                        s.bind(("0.0.0.0", p))
                        return p
                    except OSError:
                        p += 1
            return start

        chosen = pick_free_port(port)
        if chosen != port:
            logger.debug("Dashboard port %s in use; switching to %s", port, chosen)
            port = chosen

        srv = ArenaAPIServer(
            db_path=run_dir / "game.db",
            port=port,
            run_dir=run_dir,
            instance_pool=self._instance_pool,
            session_runner=self._session_runner,
        )
        await srv.start()
        self.api_server = srv

        port_js = run_dir / "data" / "arena_port.js"
        port_js.parent.mkdir(parents=True, exist_ok=True)
        port_js.write_text(f"window.ARENA_API_PORT = {port};\n", encoding="utf-8")

        logger.debug("Output directory: %s", str(run_dir))
        index_url = f"http://localhost:{port}/index.html"
        logger.debug("Dashboard URL: %s", index_url)
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
            p = run_dir / name
            if p.exists():
                p.unlink()
                logger.debug("Removed %s", p)
        for name in dirs:
            d = run_dir / name
            if d.exists():
                shutil.rmtree(d)
                logger.debug("Removed directory %s", d)

    @staticmethod
    def cleanup_dashboard_assets(run_dir: Path) -> None:
        DashboardManager.cleanup_run_dir(
            run_dir,
            files=[
                "index.html",
                "data/shogi-board.js",
                "data/arena_port.js",
            ],
            dirs=["spsa", "static", "html"],
        )
