"""Route handler mixin for instances API."""

from __future__ import annotations

import logging
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
from shogiarena._core.contexts.instances.application.instance_runtime_serialization import serialize_instance
from shogiarena._core.interfaces.dashboard.http_response_builder import json_error_response
from shogiarena._core.interfaces.dashboard.instances.config import (
    build_config_from_payload,
)
from shogiarena._core.interfaces.dashboard.instances.instance_api_models import (
    _InstanceConfigPayload,
)
from shogiarena._core.interfaces.dashboard.instances.lookup import get_instance_or_404

logger = logging.getLogger(__name__)


class InstancesApiRouteMixin:
    """Route handlers shared by InstancesAPI."""

    store: InstanceConfigStore
    _db_path: Path | None
    _instances_port: DashboardInstancesPort

    def _get_pool(self) -> InstancePool:
        raise NotImplementedError

    def _queue_instances_update(self, kind: str, *, instance_ids: list[str] | None = None) -> None:
        raise NotImplementedError

    async def get_instance(self, request: web.Request) -> web.Response:
        """GET /api/instances/{id} - Get specific instance details."""

        pool = self._get_pool()
        instance_id = request.match_info["id"]

        instance = get_instance_or_404(pool, instance_id)
        if isinstance(instance, web.Response):
            return instance

        return web.json_response(serialize_instance(instance))

    async def create_instance(self, request: web.Request) -> web.Response:
        pool = self._get_pool()
        try:
            payload = await request.json()
            if not isinstance(payload, dict):
                raise ValueError
        except (JSONDecodeError, HTTPBadRequest, ValueError):
            return json_error_response("Invalid JSON payload", status=400, code="invalid_json")
        try:
            _InstanceConfigPayload.model_validate(payload)
        except ValidationError as exc:
            return json_error_response(str(exc), status=400, code="invalid_payload")

        try:
            config = build_config_from_payload(payload)
        except ValueError as exc:
            return json_error_response(str(exc), status=400, code="invalid_payload")

        if pool.get_instance(config.name) is not None:
            return json_error_response(
                f"Instance '{config.name}' already exists",
                status=409,
                code="instance_exists",
            )

        instance: Instance | None = None
        try:
            instance = pool.add_instance(config)
            path = self.store.save(instance.config)
            instance.source_path = path
        except (OSError, ValueError):
            if instance is not None:
                pool.remove_instance(config.name)
            return json_error_response(
                "Failed to persist instance configuration",
                status=500,
                code="persist_failed",
            )

        self._queue_instances_update("upsert", instance_ids=[instance.name])
        return web.json_response(serialize_instance(instance), status=201)

    async def patch_instance(self, request: web.Request) -> web.Response:
        pool = self._get_pool()
        instance_id = request.match_info["id"]
        instance = get_instance_or_404(pool, instance_id)
        if isinstance(instance, web.Response):
            return instance

        try:
            payload = await request.json()
            if not isinstance(payload, dict):
                raise ValueError
        except (JSONDecodeError, HTTPBadRequest, ValueError):
            return json_error_response("Invalid JSON payload", status=400, code="invalid_json")
        try:
            _InstanceConfigPayload.model_validate(payload)
        except ValidationError as exc:
            return json_error_response(str(exc), status=400, code="invalid_payload")

        try:
            updated_config = build_config_from_payload(payload, existing=instance.config)
        except ValueError as exc:
            return json_error_response(str(exc), status=400, code="invalid_payload")

        in_use = instance.metrics.in_use_slots
        if updated_config.slots is not None:
            capacity = updated_config.slots
        else:
            cpu_count = instance.metrics.cpu_count
            if isinstance(cpu_count, int) and cpu_count > 0:
                capacity = cpu_count
            else:
                capacity = max(1, in_use)
        if capacity < in_use:
            return json_error_response(
                "slots cannot be decreased below current in-use slots",
                status=409,
                code="slots_in_use",
                in_use_slots=in_use,
            )

        if updated_config.type != instance.type and (in_use or instance.active_game_by_id):
            return json_error_response(
                "cannot change instance type while jobs are running",
                status=409,
                code="instance_busy",
            )

        try:
            path = self.store.save(updated_config)
        except OSError:
            return json_error_response(
                "Failed to persist instance configuration",
                status=500,
                code="persist_failed",
            )

        instance = pool.update_instance_config(instance_id, updated_config, source_path=path)
        self._queue_instances_update("upsert", instance_ids=[instance_id])
        return web.json_response(serialize_instance(instance))

    async def delete_instance(self, request: web.Request) -> web.Response:
        pool = self._get_pool()
        instance_id = request.match_info["id"]
        instance = get_instance_or_404(pool, instance_id)
        if isinstance(instance, web.Response):
            return instance

        if instance.is_local:
            return json_error_response(
                "cannot delete local instance",
                status=400,
                code="local_instance",
            )

        if instance.metrics.in_use_slots > 0 or instance.active_game_by_id:
            return json_error_response(
                "cannot delete instance with active jobs",
                status=409,
                code="instance_busy",
                in_use_slots=instance.metrics.in_use_slots,
                active_games=list(instance.active_game_by_id.keys()),
            )

        pool.remove_instance(instance_id)
        try:
            self.store.delete(instance_id)
        except OSError as exc:
            logger.warning("Failed to delete persisted instance config for '%s': %s", instance_id, exc)
        self._queue_instances_update("remove", instance_ids=[instance_id])
        return web.Response(status=204)


__all__ = ["InstancesApiRouteMixin"]
