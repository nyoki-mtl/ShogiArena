"""Action-related route handlers for instances API."""

from __future__ import annotations

import asyncio
import time
from json import JSONDecodeError
from pathlib import Path

from aiohttp import web
from aiohttp.web_exceptions import HTTPBadRequest
from pydantic import ValidationError

from shogiarena._core.contexts.dashboard.ports.interface_dependencies import DashboardInstancesPort
from shogiarena._core.contexts.instances.application.entrypoints import (
    Instance,
    InstanceConfigStore,
)
from shogiarena._core.contexts.instances.application.instance_pool import InstancePool
from shogiarena._core.contexts.instances.application.instance_runtime_serialization import serialize_instance_metrics
from shogiarena._core.interfaces.dashboard.http_response_builder import json_error_response
from shogiarena._core.interfaces.dashboard.instances.actions import (
    perform_drain,
    perform_health_check,
    perform_provision,
)
from shogiarena._core.interfaces.dashboard.instances.instance_api_models import (
    _InstanceActionPayload,
)
from shogiarena._core.interfaces.dashboard.instances.lookup import get_instance_or_404
from shogiarena._core.shared.kernel.json_types import JsonObject


class InstancesApiActionMixin:
    """Action handlers and execution helpers."""

    store: InstanceConfigStore
    _db_path: Path | None
    _last_health_checks: dict[str, float]
    _health_check_min_interval: float
    _health_check_lock: asyncio.Lock
    _instances_port: DashboardInstancesPort
    instance_pool: object | None

    def _get_pool(self) -> InstancePool:
        raise NotImplementedError

    def _queue_instances_update(self, kind: str, *, instance_ids: list[str] | None = None) -> None:
        raise NotImplementedError

    @staticmethod
    def _coerce_pool(pool: object) -> InstancePool:
        raise NotImplementedError

    async def post_instance_action(self, request: web.Request) -> web.Response:
        """POST /api/instances/{id}/actions - Perform action on instance."""

        pool = self._get_pool()
        instance_id = request.match_info["id"]

        try:
            body = await request.json()
            if not isinstance(body, dict):
                raise ValueError
        except (JSONDecodeError, HTTPBadRequest, ValueError):
            return json_error_response(
                "Invalid JSON in request body",
                status=400,
                code="invalid_json",
            )

        try:
            action_payload = _InstanceActionPayload.model_validate(body)
        except ValidationError as exc:
            message = str(exc)
            if "Field required" in message and "action" in message:
                return json_error_response(
                    "Missing 'action' field in request body",
                    status=400,
                    code="missing_action",
                )
            return json_error_response(message, status=400, code="invalid_payload")

        action = action_payload.action
        if not action:
            return json_error_response(
                "Missing 'action' field in request body",
                status=400,
                code="missing_action",
            )

        instance = get_instance_or_404(pool, instance_id)
        if isinstance(instance, web.Response):
            return instance

        if action == "health_check":
            result = await self._perform_health_check(instance)
        elif action == "drain":
            result = self._perform_drain(instance, is_drain_enabled=True)
        elif action == "undrain":
            result = self._perform_drain(instance, is_drain_enabled=False)
        elif action == "provision":
            try:
                result = await self._perform_provision(instance, body)
            except ValueError as exc:
                return json_error_response(str(exc), status=400, code="invalid_payload")
        else:
            return json_error_response(
                f"Unknown action: {action}",
                status=400,
                code="unknown_action",
            )

        if result.get("success"):
            self._queue_instances_update("upsert", instance_ids=[instance.name])
        return web.json_response(result)

    async def get_instance_metrics(self, request: web.Request) -> web.Response:
        """GET /api/instances/{id}/metrics - Get current metrics for instance."""

        pool = self._get_pool()
        instance_id = request.match_info["id"]

        instance = get_instance_or_404(pool, instance_id)
        if isinstance(instance, web.Response):
            return instance

        return web.json_response(
            {
                "instance_id": instance_id,
                "metrics": serialize_instance_metrics(instance.metrics),
                "timestamp": time.time(),
            }
        )

    async def _perform_health_check(self, instance: Instance, *, should_force: bool = False) -> JsonObject:
        return await perform_health_check(
            instance,
            should_force=should_force,
            last_health_checks=self._last_health_checks,
            health_check_min_interval=self._health_check_min_interval,
            db_path=self._db_path,
            queue_instances_update=self._queue_instances_update,
            instances_port=self._instances_port,
        )

    async def run_health_checks(self, *, should_force: bool = False) -> None:
        """Run health checks for all instances, avoiding overlap."""

        if self._health_check_lock.locked():
            return
        async with self._health_check_lock:
            pool = self._get_pool()
            instances = pool.list_instances()
            if not instances:
                return
            await asyncio.gather(
                *[self._perform_health_check(instance, should_force=should_force) for instance in instances],
            )

    def _perform_drain(self, instance: Instance, is_drain_enabled: bool) -> JsonObject:
        pool: InstancePool | None = None
        try:
            pool = self._coerce_pool(self.instance_pool)
        except TypeError:
            pool = None
        return perform_drain(
            instance,
            is_drain_enabled=is_drain_enabled,
            pool=pool,
        )

    async def _perform_provision(self, instance: Instance, payload: JsonObject) -> JsonObject:
        return await perform_provision(instance, payload)


__all__ = ["InstancesApiActionMixin"]
