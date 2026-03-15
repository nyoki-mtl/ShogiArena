"""Parser helpers for SPSA dashboard stream boundaries."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, NonNegativeInt

from shogiarena._core.interfaces.boundaries.parsers.json_object import BoundaryObject as _BoundaryObject
from shogiarena._core.shared.kernel.contracts import parse_wire
from shogiarena._core.shared.kernel.json_coercion import coerce_json_object_serialized
from shogiarena._core.shared.kernel.json_types import JsonObject

_BOUNDARY_ID_DASH_SPSA_STREAM = "BND-DASH-SPSA-STREAM"


class _SpsaStreamPayloadModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    stream: str
    seq: NonNegativeInt
    type: str
    timestamp: NonNegativeInt | None = None
    resume_from: NonNegativeInt | None = None
    data: _BoundaryObject | None = None


class _SpsaWebSocketPayloadModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: str
    updates: list[_BoundaryObject] = Field(default_factory=list)
    total: NonNegativeInt | None = None
    timestamp: NonNegativeInt | None = None


SpsaPayloadKind: TypeAlias = Literal["stream", "websocket"]

_PAYLOAD_MODEL_BY_KIND: dict[SpsaPayloadKind, type[BaseModel]] = {
    "stream": _SpsaStreamPayloadModel,
    "websocket": _SpsaWebSocketPayloadModel,
}


def parse_spsa_payload(
    kind: SpsaPayloadKind,
    payload: Mapping[str, object],
    *,
    path: str = "root",
) -> JsonObject:
    """Validate and normalize SPSA payload by kind."""
    parsed = parse_wire(
        boundary_id=_BOUNDARY_ID_DASH_SPSA_STREAM,
        payload=payload,
        model=_PAYLOAD_MODEL_BY_KIND[kind],
        path=path,
    )
    return coerce_json_object_serialized(
        parsed.model_dump(mode="python"),
        field_name="Parsed payload",
    )


__all__ = [
    "parse_spsa_payload",
]
