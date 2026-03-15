"""Shared helpers for runtime-port adapter implementations."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from shogiarena._core.shared.kernel.json_types import JsonValue
from shogiarena._core.shared.kernel.serialization import json_serialize


def normalize_runtime_payload(payload: Mapping[str, object]) -> dict[str, JsonValue]:
    """Convert adapter payload to JsonValue-compatible mapping."""

    return {str(key): json_serialize(value) for key, value in payload.items()}


def validate_instance_pool(instance_pool: object | None) -> Any | None:
    """Validate optional instance-pool boundary required by runtime runners."""

    if instance_pool is None:
        return None
    required_methods = ("get_instance", "ensure_local_instance")
    if not all(callable(getattr(instance_pool, method, None)) for method in required_methods):
        raise TypeError(
            f"instance_pool must expose get_instance() and ensure_local_instance(), got {type(instance_pool).__name__}"
        )
    return instance_pool


__all__ = ["normalize_runtime_payload", "validate_instance_pool"]
