"""HTTP API server for Arena Dashboard.

Provides REST API endpoints and WebSocket live updates.
This is the thin routing layer that delegates to backend/ modules.
"""

from __future__ import annotations

import asyncio
import copy
import logging
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path

from aiohttp import web

from shogiarena._core.contexts.dashboard.application.archived_schedule import build_archived_schedule_snapshot
from shogiarena._core.contexts.dashboard.application.broadcast import BroadcastHandler
from shogiarena._core.contexts.dashboard.application.event_bus import EventBus
from shogiarena._core.contexts.dashboard.application.events import DashboardEvent
from shogiarena._core.contexts.dashboard.application.game.state import GameStateUpdater
from shogiarena._core.contexts.dashboard.application.live.snapshot_builder import attach_live_view_payload
from shogiarena._core.contexts.dashboard.application.state_container import DashboardState
from shogiarena._core.contexts.dashboard.ports.interface_dependencies import (
    DashboardGameQueryPort,
)
from shogiarena._core.contexts.dashboard.ports.snapshot_storage import (
    SnapshotStoragePort,
)
from shogiarena._core.contexts.game_session.application.progress.snapshot_payload_builders import (
    normalize_worker_snapshot_dto,
)
from shogiarena._core.contexts.game_session.ports.dashboard_lifecycle_factory import (
    DashboardScheduleBoundaryPort,
)
from shogiarena._core.contexts.game_session.ports.session_lifecycle_ports import DashboardProfile
from shogiarena._core.interfaces.dashboard.api_server.diagnostics_mixin import (
    ArenaApiServerDiagnosticsMixin,
)
from shogiarena._core.interfaces.dashboard.api_server.events_mixin import (
    ArenaApiServerEventsMixin,
)
from shogiarena._core.interfaces.dashboard.api_server.runtime_mixin import (
    ArenaApiServerRuntimeMixin,
)
from shogiarena._core.interfaces.dashboard.book.api import BookAPI
from shogiarena._core.interfaces.dashboard.generate.api import GenerateAPI
from shogiarena._core.interfaces.dashboard.instances.api import InstancesAPI
from shogiarena._core.interfaces.dashboard.match.api import MatchAPI
from shogiarena._core.interfaces.dashboard.scheduler_api import SchedulerAPI
from shogiarena._core.interfaces.dashboard.security import (
    DASHBOARD_READ_ONLY_KEY,
    local_dashboard_security_middleware,
    require_loopback_bind_host,
)
from shogiarena._core.interfaces.dashboard.sprt.api import SprtAPI
from shogiarena._core.interfaces.dashboard.spsa.api import SpsaAPI
from shogiarena._core.interfaces.dashboard.static_handler import StaticAssetsHandler
from shogiarena._core.interfaces.dashboard.tournament.api import TournamentAPI
from shogiarena._core.interfaces.dashboard.ws_server import LiveWebSocketHub
from shogiarena._core.shared.kernel.json_types import JsonObject
from shogiarena._core.shared.kernel.snapshots import EngineOptionsSnapshots

logger = logging.getLogger(__name__)


class ArenaAPIServer(ArenaApiServerEventsMixin, ArenaApiServerDiagnosticsMixin, ArenaApiServerRuntimeMixin):
    """HTTP API server for Arena Dashboard.

    A thin routing layer that delegates to backend/ modules for business logic.
    """

    def __init__(
        self,
        db_path: Path,
        host: str = "127.0.0.1",
        port: int = 8080,
        run_dir: Path | None = None,
        instance_pool: object | None = None,
        *,
        read_only: bool = False,
        dashboard_num_workers: int = 0,
        dashboard_profiles: tuple[DashboardProfile, ...] | None = None,
        schedule_boundary: DashboardScheduleBoundaryPort | None = None,
        state: DashboardState,
        game_state: GameStateUpdater,
        snapshot_storage: SnapshotStoragePort,
        event_bus: EventBus[DashboardEvent],
        game_query: DashboardGameQueryPort,
    ) -> None:
        self.db_path = db_path
        self.host = require_loopback_bind_host(host)
        self.port = port
        self.run_dir = run_dir or db_path.parent
        self.instance_pool = instance_pool
        self._is_read_only = bool(read_only)
        self.app = web.Application(middlewares=[local_dashboard_security_middleware, self._json_error_middleware])
        self.app[DASHBOARD_READ_ONLY_KEY] = self._is_read_only
        self.runner: web.AppRunner | None = None
        self.site: web.TCPSite | None = None
        self._instances_health_task: asyncio.Task[None] | None = None

        self._state = state
        self._game_state = game_state
        self._static_handler = StaticAssetsHandler(
            self.run_dir,
            port=self.port,
            read_only=self._is_read_only,
            num_workers=dashboard_num_workers,
            profiles=dashboard_profiles,
        )
        self._event_bus = event_bus
        self._register_dashboard_event_handlers()
        self._game_query = game_query

        self._diagnostics_retention_minutes = self._load_snapshot_retention_minutes()
        self._debug_last_get_worker: dict[int, float] = {}

        self.instances_api = InstancesAPI(instance_pool, db_path=db_path, is_read_only=self._is_read_only)
        self._instances_health_interval = self._load_instances_health_interval()

        self.tournament_api = TournamentAPI(
            db_path=self.db_path,
            run_dir=self.run_dir,
            engine_options_supplier=self._copy_engine_options_snapshot,
            engine_info_supplier=self._copy_engine_info_snapshot,
            game_query=self._game_query,
        )
        self.spsa_api = SpsaAPI(
            db_path=self.db_path,
            run_dir=self.run_dir,
            read_only=self._is_read_only,
            game_query=self._game_query,
        )
        self.match_api = MatchAPI(
            db_path=self.db_path,
            run_dir=self.run_dir,
            summary_supplier=lambda: self._copy_summary_snapshot(source="match"),
        )
        self.sprt_api = SprtAPI(
            db_path=self.db_path,
            run_dir=self.run_dir,
            summary_supplier=lambda: self._copy_summary_snapshot(source="sprt"),
        )
        self.generate_api = GenerateAPI(
            db_path=self.db_path,
            run_dir=self.run_dir,
        )
        self.book_api = BookAPI(db_path=self.db_path, game_query=self._game_query)

        archived_schedule = (
            build_archived_schedule_snapshot(self.run_dir, db_path=self.db_path, game_query=self._game_query)
            if self._is_read_only
            else None
        )
        if archived_schedule is not None:
            summary = self._state.get_summary_snapshot("tournament")
            if summary is not None:
                summary = copy.deepcopy(summary)
                summary["is_summary_ready"] = True
                summary["games"] = {
                    "completed": archived_schedule.get("completed_games", 0),
                    "total": archived_schedule.get("total_games", 0),
                    "cancelled": archived_schedule.get("cancelled_games", 0),
                }
                self._state.set_summary_snapshot("tournament", attach_live_view_payload(summary))
        self.scheduler_api = SchedulerAPI(
            schedule_boundary,
            games_snapshot_supplier=self._copy_games_snapshot,
            archived_schedule_supplier=(lambda: archived_schedule) if archived_schedule is not None else None,
        )

        self.ws_hub = LiveWebSocketHub(
            bootstrap_provider=self._ws_bootstrap_messages,
            snapshot_resolver=self._resolve_ws_snapshot,
            topic_ttl_seconds=self._load_ws_topic_ttl_seconds(),
            max_topics=self._load_ws_max_topics(),
        )

        self._broadcast = BroadcastHandler(
            state=self._state,
            game_state=self._game_state,
            snapshot_storage=snapshot_storage,
            publish=self._publish_ws,
            publish_assignment=self._publish_assignment_via_event_bus,
            normalize_snapshot=normalize_worker_snapshot_dto,
        )

        self.app.on_startup.append(self._on_startup)
        self.app.on_cleanup.append(self._on_cleanup)

        self._setup_routes()

    @staticmethod
    @web.middleware
    async def _json_error_middleware(
        request: web.Request,
        handler: Callable[[web.Request], Awaitable[web.StreamResponse]],
    ) -> web.StreamResponse:
        try:
            return await handler(request)
        except web.HTTPException as exc:
            if exc.content_type == "application/json":
                raise
            detail = exc.reason or exc.text or exc.__class__.__name__
            payload = {"detail": detail, "error": detail}
            if exc.text and exc.text != detail:
                payload["message"] = exc.text
            return web.json_response(payload, status=exc.status)
        except Exception as exc:  # pragma: no cover
            logger.exception("Unhandled exception during request %s %s", request.method, request.rel_url, exc_info=exc)
            payload = {
                "detail": "Internal server error",
                "error": "Internal server error",
                "code": "internal_error",
            }
            return web.json_response(payload, status=500)

    def _setup_routes(self) -> None:
        self.app.router.add_get("/api/worker/{worker_idx}", self.get_worker)
        self.app.router.add_get("/api/summary", self.get_summary)
        self.app.router.add_get("/api/ws/diagnostics", self.get_ws_diagnostics)
        self.tournament_api.register_routes(self.app)
        self.spsa_api.register_routes(self.app)
        self.match_api.register_routes(self.app)
        self.sprt_api.register_routes(self.app)
        self.generate_api.register_routes(self.app)
        self.book_api.register_routes(self.app)
        self.app.router.add_get("/ws", self.ws_hub.handler)
        self.app.router.add_post("/api/diagnostics/snapshots", self.post_diagnostics_snapshot)

        # read-only でも読み取りルートは残す。archived dashboard は instances タブを
        # 表示するため、ルートごと落とすと閲覧機能まで 404 になる。mutation は
        # local_dashboard_security_middleware が handler 到達前に 403 で止める。
        self.instances_api.register_routes(self.app)
        self.scheduler_api.register_routes(self.app)
        self._static_handler.register_routes(self.app)

    def _copy_engine_options_snapshot(self) -> EngineOptionsSnapshots:
        return copy.deepcopy(self._state.get_engine_options_snapshot())

    def _copy_engine_info_snapshot(self) -> dict[str, dict[str, str]]:
        return {name: dict(meta) for name, meta in self._state.get_engine_info_snapshot().items()}

    def _copy_summary_snapshot(self, *, source: str = "tournament") -> JsonObject:
        snapshot = self._state.get_summary_snapshot(source)
        if snapshot is None:
            return {}
        copied = copy.deepcopy(snapshot)
        return attach_live_view_payload(copied)

    def _copy_games_snapshot(self) -> Mapping[str, object] | None:
        snapshot = self._state.get_games_snapshot()
        if snapshot is None:
            return None
        return copy.deepcopy(snapshot)
