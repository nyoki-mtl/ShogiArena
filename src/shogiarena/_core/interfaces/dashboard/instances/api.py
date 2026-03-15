"""Instance management API handlers for Arena Dashboard."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path

from aiohttp import web
from pydantic import ValidationError

from shogiarena._core.contexts.dashboard.application.instances_stream import (
    build_instances_delta,
    drain_updates,
    signature_for_instances_snapshot,
)
from shogiarena._core.contexts.dashboard.ports.interface_dependencies import (
    DashboardInstancesPort,
    load_dashboard_interface_dependencies,
)
from shogiarena._core.contexts.instances.application.entrypoints import (
    InstanceConfigStore,
    InstancePool,
)
from shogiarena._core.contexts.instances.application.instance_runtime_serialization import serialize_instance
from shogiarena._core.interfaces.dashboard.http_response_builder import json_error_response
from shogiarena._core.interfaces.dashboard.instances.actions_mixin import (
    InstancesApiActionMixin,
)
from shogiarena._core.interfaces.dashboard.instances.instance_api_models import (
    _SseInstancesQuery,
)
from shogiarena._core.interfaces.dashboard.instances.routes_mixin import (
    InstancesApiRouteMixin,
)
from shogiarena._core.interfaces.dashboard.sse import (
    extract_last_event_id,
    inject_resume_from,
    serialize_sse_event,
)
from shogiarena._core.platform.settings.project_dirs import output_dir as _output_dir
from shogiarena._core.shared.kernel.json_types import JsonObject
from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_float, coerce_int

logger = logging.getLogger(__name__)


class InstancesAPI(InstancesApiRouteMixin, InstancesApiActionMixin):
    """API handlers for instance management endpoints."""

    def __init__(
        self,
        instance_pool: object | None = None,
        *,
        store: InstanceConfigStore | None = None,
        db_path: Path | None = None,
        instances_port: DashboardInstancesPort | None = None,
    ) -> None:
        dependencies = load_dashboard_interface_dependencies() if instances_port is None else None
        self.instance_pool = instance_pool
        self.store = store or InstanceConfigStore(base_dir=_output_dir / "instances")
        self._db_path = Path(db_path).resolve() if db_path else None
        self._instances_sse_seq = 0
        self._last_health_checks: dict[str, float] = {}
        self._health_check_min_interval = self._load_health_check_min_interval()
        self._health_check_lock = asyncio.Lock()
        self._instances_updated = asyncio.Event()
        self._update_queue: asyncio.Queue[JsonObject] = asyncio.Queue()
        resolved_instances_port = instances_port or (dependencies.instances if dependencies is not None else None)
        if resolved_instances_port is None:
            raise RuntimeError("instances_port is not configured")
        self._instances_port = resolved_instances_port

    @staticmethod
    def _load_health_check_min_interval() -> float:
        raw = os.getenv("SHOGI_ARENA_DASHBOARD_HEALTH_CHECK_MIN_INTERVAL", "").strip()
        if not raw:
            return 5.0
        parsed = coerce_float(raw)
        if parsed is None:
            return 5.0
        return max(0.0, parsed)

    @staticmethod
    def _coerce_pool(pool: object) -> InstancePool:
        if isinstance(pool, InstancePool):
            return pool
        raise TypeError("InstancesAPI.pool must be an InstancePool instance")

    def _get_pool(self) -> InstancePool:
        """Get an instance pool, ensuring a local instance exists."""

        if self.instance_pool is None:
            self.instance_pool = InstancePool()
        pool = self._coerce_pool(self.instance_pool)
        pool.ensure_local_instance()
        return pool

    def _next_instances_seq(self) -> int:
        self._instances_sse_seq += 1
        return self._instances_sse_seq

    def _queue_instances_update(self, kind: str, *, instance_ids: list[str] | None = None) -> None:
        payload = {"kind": kind}
        if instance_ids:
            payload["instance_ids"] = list(instance_ids)
        self._update_queue.put_nowait(payload)
        self._instances_updated.set()

    def _build_instances_payload(self) -> JsonObject:
        pool = self._get_pool()
        instances = pool.list_instances()
        instance_data = [serialize_instance(instance) for instance in instances]
        stats = pool.get_stats()
        return {
            "instances": instance_data,
            "stats": stats,
            "timestamp": time.time(),
        }

    async def get_instances(self, _request: web.Request) -> web.Response:
        """GET /api/instances - List all instances with current status."""

        return web.json_response(self._build_instances_payload())

    async def sse_instances(self, request: web.Request) -> web.StreamResponse:
        try:
            parsed_query = _SseInstancesQuery.model_validate(dict(request.rel_url.query))
        except ValidationError as exc:
            return json_error_response(str(exc), status=400, code="invalid_query")

        poll_interval = max(1.0, min(parsed_query.poll_interval, 15.0))
        should_send_initial = parsed_query.should_send_initial

        response = web.StreamResponse(
            status=200,
            reason="OK",
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "Access-Control-Allow-Origin": "*",
            },
        )
        await response.prepare(request)

        last_event_id = extract_last_event_id(request)
        heartbeat_interval = 15.0
        last_heartbeat = time.monotonic()
        last_signature: str | None = None

        async def push_event(payload: JsonObject) -> None:
            payload = inject_resume_from(payload, last_event_id)
            seq = self._next_instances_seq()
            envelope = dict(payload)
            envelope.setdefault("stream", "instances")
            envelope["seq"] = seq
            chunk = serialize_sse_event("instances", envelope, event_id=str(seq))
            await response.write(chunk)
            await response.drain()

        try:
            while True:
                updates = await drain_updates(self._update_queue)
                snapshot = self._build_instances_payload()
                signature = signature_for_instances_snapshot(snapshot)
                if should_send_initial or signature != last_signature:
                    now_timestamp = coerce_int(time.time() * 1000.0) or 0
                    await push_event(
                        {
                            "type": "instances_update",
                            "data": snapshot,
                            "timestamp": now_timestamp,
                        }
                    )
                    last_signature = signature
                    should_send_initial = False
                elif updates:
                    delta = build_instances_delta(snapshot, updates)
                    if delta:
                        now_timestamp = coerce_int(time.time() * 1000.0) or 0
                        await push_event(
                            {
                                "type": "instances_delta",
                                "data": delta,
                                "timestamp": now_timestamp,
                            }
                        )
                        last_signature = signature

                now = time.monotonic()
                if now - last_heartbeat >= heartbeat_interval:
                    now_timestamp = coerce_int(time.time() * 1000.0) or 0
                    await push_event({"type": "heartbeat", "timestamp": now_timestamp})
                    last_heartbeat = now

                try:
                    await asyncio.wait_for(self._instances_updated.wait(), timeout=poll_interval)
                    self._instances_updated.clear()
                except TimeoutError:
                    continue
        except asyncio.CancelledError:
            raise
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            logger.debug("Instances SSE client disconnected")

        return response

    def register_routes(self, app: web.Application) -> None:
        """Setup instance management routes."""

        app.router.add_post("/api/instances", self.create_instance)
        app.router.add_get("/api/instances", self.get_instances)
        app.router.add_get("/api/instances/stream", self.sse_instances)
        app.router.add_get("/api/instances/{id}", self.get_instance)
        app.router.add_patch("/api/instances/{id}", self.patch_instance)
        app.router.add_delete("/api/instances/{id}", self.delete_instance)
        app.router.add_post("/api/instances/{id}/actions", self.post_instance_action)
        app.router.add_get("/api/instances/{id}/metrics", self.get_instance_metrics)
