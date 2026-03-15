"""Dashboard boundary serializers for interface/adapter layer."""

from __future__ import annotations

from collections.abc import Mapping

from shogiarena._core.interfaces.boundaries.parsers.snapshot import (
    _BOUNDARY_ID_DASH_SNAPSHOT as BOUNDARY_ID_DASH_SNAPSHOT,
)
from shogiarena._core.interfaces.boundaries.parsers.snapshot import (
    DashboardSnapshotPayloadKind,
)
from shogiarena._core.shared.kernel.exceptions import ContractSerializeError
from shogiarena._core.shared.kernel.json_coercion import coerce_json_object_serialized
from shogiarena._core.shared.kernel.json_types import JsonObject


def _as_json_object(payload: object, *, boundary_id: str, path: str) -> JsonObject:
    try:
        return coerce_json_object_serialized(payload, field_name=path)
    except TypeError as exc:
        raise ContractSerializeError(
            boundary_id=boundary_id,
            path=path,
            field=None,
            raw_type=type(payload).__name__,
            raw_value=payload,
            message="Failed to serialize payload to JSON object",
        ) from exc


def serialize_dashboard_snapshot_payload(
    kind: DashboardSnapshotPayloadKind,
    payload: Mapping[str, object] | JsonObject,
    *,
    path: str = "root",
) -> JsonObject:
    """Serialize dashboard snapshot payload through the dashboard snapshot boundary."""
    if kind not in {"summary", "games"}:
        raise ValueError(f"Unsupported dashboard snapshot kind: {kind}")
    return _as_json_object(payload, boundary_id=BOUNDARY_ID_DASH_SNAPSHOT, path=path)


__all__ = [
    "serialize_dashboard_snapshot_payload",
]
