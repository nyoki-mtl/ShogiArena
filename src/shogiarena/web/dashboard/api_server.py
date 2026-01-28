"""HTTP API server for Arena Dashboard.

Provides REST API endpoints and WebSocket live updates.
This is the thin routing layer that delegates to backend/ modules.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import os
import time
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from aiohttp import web

from shogiarena.arena.instances.pool import InstancePool
from shogiarena.db import ShogiDB, SQLiteShogiDBFactory
from shogiarena.web.dashboard.backend import (
    BroadcastHandler,
    DashboardState,
    GameSnapshotCache,
    GameStateUpdater,
    SnapshotStorage,
    StaticAssetsHandler,
)
from shogiarena.web.dashboard.backend.instances import InstancesAPIHandler
from shogiarena.web.dashboard.backend.live import (
    build_live_view_snapshot,
    load_live_diagnostics_guidelines,
)
from shogiarena.web.dashboard.backend.match import MatchAPI
from shogiarena.web.dashboard.backend.scheduler import SchedulerAPIHandler
from shogiarena.web.dashboard.backend.sprt import SprtAPI
from shogiarena.web.dashboard.backend.spsa import SPSAAPI
from shogiarena.web.dashboard.backend.tournament import TournamentAPI
from shogiarena.web.dashboard.backend.ws_server import LiveWebSocketHub

if TYPE_CHECKING:  # pragma: no cover - type checking only
    from shogiarena.arena.runners.base_runner import BaseSessionRunner

logger = logging.getLogger(__name__)


class ArenaAPIServer:
    """HTTP API server for Arena Dashboard.

    A thin routing layer that delegates to backend/ modules for business logic.
    """

    def __init__(
        self,
        db_path: Path,
        port: int = 8080,
        run_dir: Path | None = None,
        instance_pool: InstancePool | None = None,
        *,
        session_runner: BaseSessionRunner[Any, Any] | None = None,
    ):
        self.db_path = db_path
        self.port = port
        self.run_dir = run_dir or db_path.parent
        self.instance_pool = instance_pool
        self._session_runner = session_runner
        self.app = web.Application(middlewares=[self._json_error_middleware])
        self.runner: web.AppRunner | None = None
        self.site: web.TCPSite | None = None
        self._instances_health_task: asyncio.Task[None] | None = None

        # Initialize shared state container
        self._state = DashboardState()
        default_summary = self._default_summary_snapshot(source="tournament")
        default_summary["runDir"] = str(self.run_dir)
        self._state.summary_snapshots["tournament"] = default_summary

        # Initialize backend components
        self._game_cache = GameSnapshotCache(self._state)
        self._game_state = GameStateUpdater(self._game_cache)
        self._snapshot_storage = SnapshotStorage(self._state, self.run_dir)
        self._static_handler = StaticAssetsHandler(self.run_dir)

        # Diagnostics config
        self._diagnostics_retention_minutes = self._load_snapshot_retention_minutes()
        self._debug_last_get_worker: dict[int, float] = {}

        # ShogiDB
        self.shogidb: ShogiDB | None = None
        self._init_shogidb()

        # Instance management API
        self.instances_api = InstancesAPIHandler(instance_pool, db_path=db_path)
        self._instances_health_interval = self._load_instances_health_interval()

        # Tournament and SPSA APIs
        self.tournament_api = TournamentAPI(
            db_path=self.db_path,
            run_dir=self.run_dir,
            ensure_shogidb=self._ensure_shogidb,
            shogidb_supplier=lambda: self.shogidb,
            engine_options_supplier=self._copy_engine_options_snapshot,
            engine_info_supplier=self._copy_engine_info_snapshot,
        )
        self.spsa_api = SPSAAPI(
            db_path=self.db_path,
            run_dir=self.run_dir,
            ensure_shogidb=self._ensure_shogidb,
            shogidb_supplier=lambda: self.shogidb,
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

        # Scheduler API
        scheduler_runner = None
        if session_runner is not None and hasattr(session_runner, "get_schedule_snapshot"):
            scheduler_runner = session_runner
        self.scheduler_api = SchedulerAPIHandler(scheduler_runner)

        # WebSocket hub
        self.ws_hub = LiveWebSocketHub(
            bootstrap_provider=self._ws_bootstrap_messages,
            snapshot_resolver=self._resolve_ws_snapshot,
            topic_ttl_seconds=self._load_ws_topic_ttl_seconds(),
            max_topics=self._load_ws_max_topics(),
        )

        # Broadcast handler (after ws_hub is created)
        self._broadcast = BroadcastHandler(
            state=self._state,
            game_cache=self._game_cache,
            game_state=self._game_state,
            snapshot_storage=self._snapshot_storage,
            publish=self._publish_ws,
            spsa_notifier=self._spsa_notify,
        )

        # Add liveView to initial summary
        self._state.summary_snapshots["tournament"]["liveView"] = build_live_view_snapshot(
            self._state.summary_snapshots["tournament"]
        )

        self.app.on_startup.append(self._on_startup)
        self.app.on_cleanup.append(self._on_cleanup)

        self._setup_routes()

    # ------------------------------------------------------------------
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
                    await self.instances_api.run_health_checks(force=True)
                except Exception:
                    logger.exception("Instances health check loop failed")
                elapsed = time.monotonic() - start
                await asyncio.sleep(max(0.0, interval - elapsed))
        except asyncio.CancelledError:
            raise

    async def _on_startup(self, _app: web.Application) -> None:
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
        self._instances_health_task = None

    # Environment config loaders
    # ------------------------------------------------------------------
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

    def _load_snapshot_retention_minutes(self) -> int:
        try:
            guidelines = load_live_diagnostics_guidelines(self.run_dir / "live_diagnostics.yml")
        except Exception:  # pragma: no cover
            return 0
        auto_snapshot = guidelines.get("autoSnapshot")
        if isinstance(auto_snapshot, Mapping):
            try:
                return max(0, int(auto_snapshot.get("retentionMinutes", 0)))
            except (TypeError, ValueError):
                return 0
        return 0

    # ------------------------------------------------------------------
    # Middleware
    # ------------------------------------------------------------------
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

    # ------------------------------------------------------------------
    # Default snapshots
    # ------------------------------------------------------------------
    @staticmethod
    def _default_summary_snapshot(*, source: str = "tournament") -> dict[str, Any]:
        timestamp = datetime.now(tz=timezone.utc).isoformat()
        return {
            "summaryReady": False,
            "summarySource": source,
            "mode": source,
            "tournamentType": None,
            "flipPolicy": None,
            "numEngines": 0,
            "runDir": None,
            "leaderboard": [],
            "ratingInitial": None,
            "engines": [],
            "enginesMeta": [],
            "engineTimeControls": {},
            "defaultTimeControl": None,
            "engineInstances": {},
            "engineStats": {},
            "pairResults": {},
            "timestamp": timestamp,
            "games": {
                "completed": 0,
                "total": 0,
                "cancelled": 0,
            },
            "btd": {
                "ratings": {},
                "anchor": None,
                "gamma_elo": 0.0,
                "gamma_elo_se": 0.0,
                "draw_eq": 0.5,
                "draw_eq_se": 0.0,
                "rating_cov": {},
            },
        }

    # ------------------------------------------------------------------
    # Route setup
    # ------------------------------------------------------------------
    def _setup_routes(self) -> None:
        self.app.router.add_get("/api/worker/{worker_idx}", self.get_worker)
        self.app.router.add_get("/api/summary", self.get_summary)
        self.app.router.add_get("/api/ws/diagnostics", self.get_ws_diagnostics)
        self.tournament_api.register_routes(self.app)
        self.spsa_api.register_routes(self.app)
        self.match_api.register_routes(self.app)
        self.sprt_api.register_routes(self.app)
        self.app.router.add_get("/ws", self.ws_hub.handler)
        self.app.router.add_post("/api/diagnostics/snapshots", self.post_diagnostics_snapshot)

        # Instance and scheduler endpoints
        self.instances_api.setup_routes(self.app)
        self.scheduler_api.setup_routes(self.app)

        # Static assets (delegated)
        self._static_handler.setup_routes(self.app)

    # ------------------------------------------------------------------
    # ShogiDB
    # ------------------------------------------------------------------
    def _init_shogidb(self) -> None:
        game_db_path = (self.run_dir / "game.db") if self.run_dir else None
        if game_db_path and game_db_path.exists():
            factory = SQLiteShogiDBFactory(game_db_path)
            self.shogidb = ShogiDB(factory.engine, factory.session_factory)
            logger.debug("Arena API will use ORM DB: %s", game_db_path)
        else:
            logger.warning("Arena API will use arena SQLite DB (no game.db detected)")

    def _ensure_shogidb(self) -> None:
        if self.shogidb is None and self.run_dir and (self.run_dir / "game.db").exists():
            self._init_shogidb()

    # ------------------------------------------------------------------
    # WebSocket helpers
    # ------------------------------------------------------------------
    def _publish_ws(self, topic: str, payload: dict[str, Any], *, worker_idx: int | None = None) -> None:
        if not hasattr(self, "ws_hub") or self.ws_hub is None:
            return
        try:
            self.ws_hub.publish(topic, payload, worker_idx=worker_idx)
        except Exception:
            logger.debug("Failed to publish WS topic=%s", topic, exc_info=True)

    def _spsa_notify(self, snapshot: dict[str, Any]) -> None:
        if hasattr(self, "spsa_api") and self.spsa_api is not None:
            try:
                self.spsa_api.notify_summary_snapshot(snapshot)
            except Exception:
                logger.debug("Failed to publish SPSA summary snapshot", exc_info=True)

    @staticmethod
    def _parse_workers_query(raw_value: str | None) -> set[int] | None:
        if not raw_value:
            return None
        try:
            parsed = {int(part.strip()) for part in raw_value.split(",") if part.strip()}
            return parsed or None
        except ValueError:
            logger.warning("Invalid workers query parameter: %s", raw_value)
            return None

    def _ws_bootstrap_messages(self, worker_filter: set[int] | None) -> list[tuple[str, Any]]:
        """Build initial WS envelopes for a newly connected client."""
        messages: list[tuple[str, Any]] = []

        games_snapshot = self._copy_games_snapshot()
        if games_snapshot:
            messages.append(("live.games.delta", games_snapshot))

        assignment_snapshot = self._build_assignment_snapshot(worker_filter=worker_filter)
        if assignment_snapshot is not None:
            messages.append(("live.assignment.snapshot", assignment_snapshot))
            gids = assignment_snapshot.get("gids")
            if isinstance(gids, list):
                for gid in gids:
                    if not isinstance(gid, str) or not gid.strip():
                        continue
                    snap = self._game_cache.get(gid)
                    if snap is None:
                        continue
                    ws_snapshot = self._game_state.build_ws_snapshot(
                        gid, snap, assignment_rev=self._state.assignment_rev
                    )
                    if ws_snapshot is None:
                        continue
                    messages.append((f"live.game.{gid}.snapshot", {"gid": gid, "snapshot": ws_snapshot}))

        return messages

    def _resolve_ws_snapshot(self, topic: str) -> list[tuple[str, Any]]:
        if topic.startswith("live.summary.snapshot."):
            source = topic.split(".", 3)[-1]
            return [(topic, self._copy_summary_snapshot(source=source))]
        if topic == "live.games.delta":
            return [("live.games.delta", self._copy_games_snapshot())]
        if topic in {"live.assignment.snapshot", "live.assignment.diff"}:
            snapshot = self._build_assignment_snapshot(worker_filter=None)
            return [("live.assignment.snapshot", snapshot)]
        if topic.startswith("live.game."):
            gid = self._extract_gid_from_topic(topic)
            if not gid:
                return []
            snap = self._game_cache.get(gid)
            if snap is None:
                return []
            ws_snapshot = self._game_state.build_ws_snapshot(gid, snap, assignment_rev=self._state.assignment_rev)
            if ws_snapshot is None:
                return []
            return [(f"live.game.{gid}.snapshot", {"gid": gid, "snapshot": ws_snapshot})]
        return []

    @staticmethod
    def _extract_gid_from_topic(topic: str) -> str | None:
        """Extract game ID from a topic string."""
        rest = topic[len("live.game.") :]
        gid: str | None = None
        if rest.endswith(".snapshot"):
            gid = rest[: -len(".snapshot")]
        elif rest.endswith(".diff"):
            gid_part = rest[: -len(".diff")]
            if gid_part.endswith(".moves"):
                gid = gid_part[: -len(".moves")]
            elif gid_part.endswith(".analysis"):
                gid = gid_part[: -len(".analysis")]
            elif gid_part.endswith(".state"):
                gid = gid_part[: -len(".state")]
            else:
                gid = gid_part
        if gid is None:
            return None
        return gid.strip(".") or None

    def _build_assignment_snapshot(self, *, worker_filter: set[int] | None) -> dict[str, Any] | None:
        assignments: dict[str, Any] = {}
        gids: list[str] = []
        seen: set[str] = set()
        indices = sorted(self._state.worker_assignment.keys()) if self._state.worker_assignment else []
        for idx in indices:
            if worker_filter is not None and idx not in worker_filter:
                continue
            gid = self._state.worker_assignment.get(idx)
            assignments[str(idx)] = gid
            if isinstance(gid, str) and gid and gid not in seen:
                seen.add(gid)
                gids.append(gid)
        return {
            "assignments": assignments,
            "gids": gids,
            "updatedAt": int(time.time() * 1000),
            "assignment_rev": self._state.assignment_rev,
        }

    # ------------------------------------------------------------------
    # Snapshot copy helpers (read from state)
    # ------------------------------------------------------------------
    def _copy_engine_options_snapshot(self) -> dict[str, dict[str, Any]]:
        return copy.deepcopy(self._state.engine_options_snapshot)

    def _copy_engine_info_snapshot(self) -> dict[str, dict[str, str]]:
        return {name: dict(meta) for name, meta in self._state.engine_info_snapshot.items()}

    def _copy_summary_snapshot(self, *, source: str = "tournament") -> dict[str, Any]:
        snapshot = self._state.summary_snapshots.get(source)
        return copy.deepcopy(snapshot) if snapshot else {}

    def _copy_games_snapshot(self) -> dict[str, Any] | None:
        return copy.deepcopy(self._state.games_snapshot) if self._state.games_snapshot else None

    # ------------------------------------------------------------------
    # Engine options
    # ------------------------------------------------------------------
    def update_engine_options(
        self,
        engine_name: str,
        options: Mapping[str, Any],
        info: Mapping[str, str] | None = None,
    ) -> None:
        """Persist latest engine option snapshot for REST access."""
        try:
            opts_serialized = json.loads(json.dumps(options, ensure_ascii=False))
        except (TypeError, ValueError):
            logger.debug("Failed to serialise engine options for %s", engine_name, exc_info=True)
            return
        self._state.engine_options_snapshot[str(engine_name)] = opts_serialized
        if info:
            self._state.engine_info_snapshot[str(engine_name)] = {str(k): str(v) for k, v in info.items()}

    # ------------------------------------------------------------------
    # HTTP Handlers
    # ------------------------------------------------------------------
    async def get_ws_diagnostics(self, request: web.Request) -> web.Response:
        """Expose lightweight WebSocket hub diagnostics."""
        diagnostics = self.ws_hub.snapshot_diagnostics()
        return web.json_response(diagnostics)

    async def get_summary(self, request: web.Request) -> web.Response:
        source = request.query.get("source", "tournament").strip().lower() or "tournament"
        return web.json_response(self._copy_summary_snapshot(source=source))

    async def get_worker(self, request: web.Request) -> web.Response:
        start = time.perf_counter()
        idx = int(request.match_info["worker_idx"])
        last = self._debug_last_get_worker.get(idx)
        if last is not None:
            gap_ms = (start - last) * 1000.0
            if gap_ms <= 500.0:
                logger.debug("Rapid get_worker for %s (gap %.1f ms)", idx, gap_ms)
        self._debug_last_get_worker[idx] = start
        snap = self._state.worker_snapshots.get(idx)
        if not snap:
            return web.json_response(None)
        response = web.json_response(snap)
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        if elapsed_ms >= 50.0:
            print(f"[rest:get_worker] worker={idx} duration_ms={elapsed_ms:.1f}", flush=True)
        return response

    async def post_diagnostics_snapshot(self, request: web.Request) -> web.Response:
        try:
            payload = await request.json()
        except json.JSONDecodeError as exc:
            raise web.HTTPBadRequest(text="Invalid JSON payload") from exc

        if not isinstance(payload, Mapping):
            raise web.HTTPBadRequest(text="Diagnostics snapshot payload must be a JSON object")

        snapshot = payload.get("snapshot")
        if not isinstance(snapshot, Mapping):
            raise web.HTTPBadRequest(text="'snapshot' field must be an object")

        timestamp = datetime.now(tz=timezone.utc)
        metadata = {key: value for key, value in payload.items() if key != "snapshot"}
        metadata["receivedAt"] = timestamp.isoformat()
        record = {"snapshot": snapshot, "metadata": metadata}

        diagnostics_root = self._diagnostics_root()
        diagnostics_root.mkdir(parents=True, exist_ok=True)
        target_dir = diagnostics_root / timestamp.strftime("%Y%m%d")
        target_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{timestamp.strftime('%H%M%S')}_{uuid4().hex[:6]}.json"
        file_path = target_dir / filename
        file_path.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")

        self._prune_diagnostics_snapshots(diagnostics_root, self._diagnostics_retention_minutes)

        relative_path = file_path.relative_to(self.run_dir)
        response_payload = {"stored": str(relative_path), "receivedAt": metadata["receivedAt"]}
        return web.json_response(response_payload, status=201)

    def _diagnostics_root(self) -> Path:
        return (self.run_dir / "diagnostics").expanduser()

    def _prune_diagnostics_snapshots(self, root: Path, retention_minutes: int) -> None:
        if retention_minutes <= 0 or not root.exists():
            return
        cutoff = time.time() - (retention_minutes * 60)
        for path in root.rglob("*.json"):
            try:
                stat = path.stat()
            except OSError:
                continue
            if stat.st_mtime < cutoff:
                try:
                    path.unlink()
                except OSError:
                    continue
        for directory in sorted(root.rglob("*"), reverse=True):
            if not directory.is_dir():
                continue
            try:
                directory.rmdir()
            except OSError:
                continue

    # ------------------------------------------------------------------
    # Broadcast delegates (public API for orchestrators)
    # ------------------------------------------------------------------
    def broadcast_worker_update(self, worker_idx: int, payload: dict[str, Any]) -> None:
        """Broadcast a worker update to WebSocket clients."""
        self._broadcast.worker_update(worker_idx, payload)

    def broadcast_summary_update(self, payload: dict[str, Any], *, source: str = "tournament") -> None:
        """Broadcast a summary update to WebSocket clients."""
        self._broadcast.summary_update(payload, source=source)

    def broadcast_games_snapshot(self, snapshot: Mapping[str, Any], *, event_type: str = "bulk") -> None:
        """Broadcast a games list snapshot."""
        self._broadcast.games_snapshot(snapshot, event_type=event_type)

    def set_worker_snapshot(self, worker_idx: int, snapshot: dict[str, Any], *, broadcast: bool = True) -> None:
        """Set a worker snapshot."""
        self._broadcast.set_worker(worker_idx, snapshot, broadcast=broadcast)

    # ------------------------------------------------------------------
    # Legacy property access (backward compatibility)
    # ------------------------------------------------------------------
    @property
    def worker_snapshots(self) -> dict[int, dict[str, Any]]:
        """Access worker snapshots (for backward compatibility)."""
        return self._state.worker_snapshots

    @property
    def engine_options_snapshot(self) -> dict[str, dict[str, Any]]:
        """Access engine options snapshot (for backward compatibility)."""
        return self._state.engine_options_snapshot

    @engine_options_snapshot.setter
    def engine_options_snapshot(self, value: dict[str, dict[str, Any]]) -> None:
        self._state.engine_options_snapshot = value

    @property
    def engine_info_snapshot(self) -> dict[str, dict[str, str]]:
        """Access engine info snapshot (for backward compatibility)."""
        return self._state.engine_info_snapshot

    @engine_info_snapshot.setter
    def engine_info_snapshot(self, value: dict[str, dict[str, str]]) -> None:
        self._state.engine_info_snapshot = value

    @property
    def summary_snapshot(self) -> dict[str, Any]:
        """Access summary snapshot (for backward compatibility)."""
        return self._state.summary_snapshots.get("tournament", {})

    @summary_snapshot.setter
    def summary_snapshot(self, value: dict[str, Any]) -> None:
        self._state.summary_snapshots["tournament"] = value

    @property
    def games_snapshot(self) -> dict[str, Any] | None:
        """Access games snapshot (for backward compatibility)."""
        return self._state.games_snapshot

    @games_snapshot.setter
    def games_snapshot(self, value: dict[str, Any] | None) -> None:
        self._state.games_snapshot = value

    # ------------------------------------------------------------------
    # Lifecycle management
    # ------------------------------------------------------------------
    async def start(self) -> None:
        self.runner = web.AppRunner(self.app, shutdown_timeout=1.0)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, "0.0.0.0", self.port)
        await self.site.start()
        logger.debug("Arena API server started on http://0.0.0.0:%s", self.port)
        logger.debug("Dashboard available at http://localhost:%s/", self.port)

    async def stop(self) -> None:
        sources = list(self._state.summary_snapshots.keys()) or ["tournament"]
        for source in sources:
            self.broadcast_summary_update({"tournament_ended": True}, source=source)

        if hasattr(self, "ws_hub") and self.ws_hub is not None:
            try:
                await self.ws_hub.shutdown()
            except Exception:
                logger.debug("Failed to shutdown WS hub cleanly", exc_info=True)

        if self.site:
            await self.site.stop()
            self.site = None

        if self.runner:
            try:
                await asyncio.wait_for(self.runner.cleanup(), timeout=3.0)
            except asyncio.TimeoutError:
                logger.warning("API server cleanup timed out after 3 seconds")

        self._state.worker_snapshots.clear()
        logger.debug("Arena API server stopped")
