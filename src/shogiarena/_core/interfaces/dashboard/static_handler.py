"""Static file serving handler for Arena Dashboard.

Handles static file serving and HTML entry points with fallback support.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from urllib.parse import unquote

from aiohttp import web

from shogiarena._core.contexts.game_session.ports.session_lifecycle_ports import DashboardProfile
from shogiarena._core.interfaces.dashboard.assets_writer import render_dashboard_html

logger = logging.getLogger(__name__)


class StaticAssetsHandler:
    """Handler for static file serving with fallback support.

    Manages static file routes including HTML entry points and asset directories
    with support for falling back to built-in dashboard assets.

    Args:
        run_dir: Runtime directory containing session-specific assets.
    """

    def __init__(
        self,
        run_dir: Path,
        *,
        port: int = 8080,
        read_only: bool = False,
        num_workers: int = 0,
        profiles: tuple[DashboardProfile, ...] | None = None,
    ) -> None:
        self._run_dir = run_dir
        self._port = port
        self._read_only = read_only
        self._num_workers = num_workers
        self._profiles = profiles

    def asset_root(self) -> Path:
        """Return the run-local dashboard asset root."""

        dashboard_dir = self._run_dir / "dashboard"
        return dashboard_dir if dashboard_dir.exists() else self._run_dir

    @staticmethod
    def dashboard_static_dir() -> Path:
        """Return the built-in dashboard static directory."""
        return Path(__file__).resolve().parent / "static"

    def resolve_html_root(self) -> Path:
        """Resolve the HTML root directory.

        Returns:
            The html subdirectory if it exists, otherwise the run directory.
        """
        asset_root = self.asset_root()
        html_dir = asset_root / "html"
        return html_dir if html_dir.exists() else asset_root

    @staticmethod
    def _check_build_integrity(run_static: Path, builtin_static: Path) -> bool:
        """Compare build-meta.json between run_dir/static and builtin static.

        Returns True if they match (or if no meta exists), False if mismatched.
        """
        run_meta_path = run_static / "build-meta.json"
        builtin_meta_path = builtin_static / "build-meta.json"

        if not run_meta_path.exists():
            logger.debug("No build-meta.json in run_dir/static, skipping integrity check")
            return True

        if not builtin_meta_path.exists():
            logger.debug("No build-meta.json in builtin static, skipping integrity check")
            return True

        try:
            run_meta = json.loads(run_meta_path.read_text(encoding="utf-8"))
            builtin_meta = json.loads(builtin_meta_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to read build-meta.json: %s", exc)
            return False

        run_build_id = run_meta.get("build_id")
        builtin_build_id = builtin_meta.get("build_id")

        if run_build_id == builtin_build_id:
            logger.debug("Static build integrity OK (build_id=%s)", run_build_id)
            return True

        logger.warning(
            "Static build integrity MISMATCH: run_dir=%s, builtin=%s. "
            "Invalidating run_dir/static and serving builtin assets only.",
            run_build_id,
            builtin_build_id,
        )
        return False

    def register_routes(self, app: web.Application) -> None:
        """Register static asset routes and HTML entry points.

        Args:
            app: The aiohttp application to register routes on.
        """
        dashboard_static_dir = self.dashboard_static_dir()
        if self._read_only:
            self._register_read_only_routes(app, dashboard_static_dir)
            return

        asset_root = self.asset_root()
        run_static_dir = asset_root / "static"
        build_integrity_ok = True

        # Integrity check: if build-meta.json mismatches, treat run_dir/static as invalid
        if run_static_dir.exists() and dashboard_static_dir.exists():
            if not self._check_build_integrity(run_static_dir, dashboard_static_dir):
                logger.info("Disabling stale run_dir/static, serving builtin assets only")
                run_static_dir = asset_root / "static_disabled"  # non-existent sentinel
                build_integrity_ok = False

        if run_static_dir.exists() and dashboard_static_dir.exists():
            self._register_fallback_directory(
                app,
                "/static",
                run_static_dir,
                fallback=dashboard_static_dir,
                name="static",
            )
        else:
            self._register_directory(
                app,
                "/static",
                run_static_dir,
                fallback=dashboard_static_dir,
                name="static",
            )

        # Vite sometimes emits absolute asset URLs rooted at `/assets/*`.
        # Serve them from the same build output to avoid WebWorker load failures.
        assets_primary = run_static_dir / "dist" / "assets"
        assets_fallback = dashboard_static_dir / "dist" / "assets"
        if assets_primary.exists() and assets_fallback.exists():
            self._register_fallback_directory(
                app,
                "/assets",
                assets_primary,
                fallback=assets_fallback,
                name="assets",
            )
        else:
            self._register_directory(
                app,
                "/assets",
                assets_primary,
                fallback=assets_fallback,
                name="assets",
            )

        data_dir = asset_root / "data"
        self._register_directory(app, "/data", data_dir, name="data")

        # When build integrity failed, skip run_dir HTML root so that stale
        # index.html (referencing old chunk names) is not served.
        html_root = self.resolve_html_root() if build_integrity_ok else dashboard_static_dir
        self._register_directory(
            app,
            "/",
            html_root,
            fallback=dashboard_static_dir,
            name="html",
        )
        self._register_html_entrypoints(app, html_root, dashboard_static_dir)

    def _register_read_only_routes(self, app: web.Application, dashboard_static_dir: Path) -> None:
        """Serve packaged assets and an in-memory entrypoint for an archive."""

        self._register_directory(app, "/static", dashboard_static_dir, name="static")
        self._register_directory(
            app,
            "/assets",
            dashboard_static_dir / "dist" / "assets",
            name="assets",
        )
        data_dir = self.asset_root() / "data"
        self._register_directory(app, "/data", data_dir, name="data")
        html_content = render_dashboard_html(
            self._run_dir,
            self._num_workers,
            api_port=self._port,
            profiles=self._profiles,
        )

        async def _index(_request: web.Request) -> web.StreamResponse:
            return web.Response(text=html_content, content_type="text/html")

        app.router.add_get("/", _index, name="archive_index")
        app.router.add_get("/index.html", _index, name="archive_index_html")

    def _register_directory(
        self,
        app: web.Application,
        url_path: str,
        directory: Path,
        *,
        fallback: Path | None = None,
        name: str,
    ) -> Path | None:
        """Register a static directory if it exists, with optional fallback.

        Args:
            app: The aiohttp application.
            url_path: URL path prefix for the route.
            directory: Primary directory to serve.
            fallback: Fallback directory if primary doesn't exist.
            name: Route name prefix.

        Returns:
            The directory being served, or None if neither exists.
        """
        if directory.exists():
            app.router.add_static(url_path, directory, name=f"{name}_static")
            return directory

        if fallback and fallback.exists():
            app.router.add_static(url_path, fallback, name=f"{name}_static_fallback")
            return fallback

        return None

    def _register_fallback_directory(
        self,
        app: web.Application,
        url_path: str,
        directory: Path,
        *,
        fallback: Path,
        name: str,
    ) -> None:
        """Register a static route that falls back per-file to a secondary directory.

        aiohttp static routes select a single directory. If `run_dir/static` exists
        but is missing the built dashboard bundle, WebWorker module loads can fail.
        This handler serves from `directory` first, then `fallback`.

        Args:
            app: The aiohttp application.
            url_path: URL path prefix for the route.
            directory: Primary directory to serve from.
            fallback: Secondary directory for missing files.
            name: Route name prefix.
        """
        prefix = url_path.rstrip("/")
        route_pattern = f"{prefix}/{{path:.*}}"

        def _resolve(root: Path, rel: str) -> Path | None:
            rel = unquote(rel or "").lstrip("/")
            if not rel:
                return None
            candidate = root / rel
            try:
                resolved = candidate.resolve()
                root_resolved = root.resolve()
                if not resolved.is_relative_to(root_resolved):
                    return None
            except OSError:
                return None
            if resolved.exists() and resolved.is_file():
                return resolved
            return None

        async def _handler(request: web.Request) -> web.StreamResponse:
            rel = request.match_info.get("path", "")
            target = _resolve(directory, rel) or _resolve(fallback, rel)
            if not target:
                raise web.HTTPNotFound()
            return web.FileResponse(target)

        app.router.add_get(route_pattern, _handler, name=f"{name}_static_fallback_files")

    def _register_html_entrypoints(
        self,
        app: web.Application,
        html_root: Path,
        fallback_static: Path | None,
    ) -> None:
        """Expose main HTML files explicitly to avoid race conditions with static routes.

        Args:
            app: The aiohttp application.
            html_root: Primary HTML directory.
            fallback_static: Fallback static directory.
        """
        pages: tuple[tuple[str, tuple[str, ...]], ...] = (("index.html", ("/", "/index.html")),)

        for filename, routes in pages:
            source = html_root / filename
            target = source if source.exists() else None

            if target is None and fallback_static is not None:
                fallback_candidate = fallback_static / filename
                if fallback_candidate.exists():
                    target = fallback_candidate

            if not target:
                continue

            for route in routes:
                self._register_file_route(app, route, target)

    @staticmethod
    def _register_file_route(app: web.Application, url_path: str, path: Path) -> None:
        """Register a single file route.

        Args:
            app: The aiohttp application.
            url_path: URL path for the route.
            path: File path to serve.
        """

        async def _handler(_req: web.Request) -> web.StreamResponse:
            return web.FileResponse(path)

        app.router.add_get(url_path, _handler)
