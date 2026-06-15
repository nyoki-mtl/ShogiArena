"""Parser helpers for dashboard snapshot boundaries."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, model_validator

from shogiarena._core.interfaces.boundaries.parsers.json_object import BoundaryObject as _BoundaryObject
from shogiarena._core.shared.kernel.contracts import parse_wire
from shogiarena._core.shared.kernel.json_coercion import coerce_json_object_serialized
from shogiarena._core.shared.kernel.json_types import JsonObject

_BOUNDARY_ID_DASH_SNAPSHOT = "BND-DASH-SNAPSHOT"


class _DashboardSummaryPayloadModel(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    games: _BoundaryObject | None = None
    live_view: _BoundaryObject | None = None
    is_summary_ready: bool | None = None
    timestamp: str | None = None


class _DashboardGamesSnapshotPayloadModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    kind: Literal["bulk", "delta"]
    revision: int = Field(ge=0)
    base_revision: int | None = Field(default=None, ge=0)
    rows: list[_BoundaryObject] = Field(default_factory=list)
    snapshot_meta: _BoundaryObject = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_revision_chain(self) -> _DashboardGamesSnapshotPayloadModel:
        if self.kind == "delta" and self.base_revision is None:
            raise ValueError("delta games snapshot requires base_revision")
        return self


DashboardSnapshotPayloadKind: TypeAlias = Literal["summary", "games"]

_PAYLOAD_MODEL_BY_KIND: dict[DashboardSnapshotPayloadKind, type[BaseModel]] = {
    "summary": _DashboardSummaryPayloadModel,
    "games": _DashboardGamesSnapshotPayloadModel,
}


def parse_dashboard_snapshot_payload(
    kind: DashboardSnapshotPayloadKind,
    payload: Mapping[str, object],
    *,
    path: str = "root",
) -> JsonObject:
    """Validate and normalize dashboard snapshot payload by kind."""
    parsed = parse_wire(
        boundary_id=_BOUNDARY_ID_DASH_SNAPSHOT,
        payload=payload,
        model=_PAYLOAD_MODEL_BY_KIND[kind],
        path=path,
    )
    return coerce_json_object_serialized(
        parsed.model_dump(mode="python", by_alias=True),
        field_name="Parsed payload",
    )


__all__ = [
    "DashboardSnapshotPayloadKind",
    "parse_dashboard_snapshot_payload",
]
