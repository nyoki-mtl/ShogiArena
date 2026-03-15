"""Instance lookup helpers shared by instances API mixins."""

from __future__ import annotations

from aiohttp import web

from shogiarena._core.contexts.instances.application.entrypoints import Instance
from shogiarena._core.contexts.instances.application.instance_pool import InstancePool
from shogiarena._core.interfaces.dashboard.http_response_builder import json_error_response


def get_instance_or_404(pool: InstancePool, instance_id: str) -> Instance | web.Response:
    instance = pool.get_instance(instance_id)
    if instance is not None:
        return instance
    return json_error_response(
        f"Instance '{instance_id}' not found",
        status=404,
        code="instance_not_found",
    )


__all__ = ["get_instance_or_404"]
