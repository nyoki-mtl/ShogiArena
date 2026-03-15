"""Parser helpers for session context snapshot boundaries."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, NonNegativeInt, PositiveInt, StringConstraints

from shogiarena._core.shared.kernel.contracts import parse_wire

_BOUNDARY_ID_SESSION_CONTEXT = "BND-SESSION-CONTEXT"


_NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class _SessionContextMetadataPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    runner_type: Literal["tournament", "spsa"] | None = None
    experiment_name: str | None = None
    is_dashboard_enabled: bool = False
    num_workers: NonNegativeInt | None = None
    scheduler: str | None = None
    games_per_pair: NonNegativeInt | None = None
    num_engines: NonNegativeInt | None = None
    should_skip_resume: bool = False
    session_uuid: str | None = None


class _SessionContextSnapshotPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    run_id: _NonEmptyStr
    num_workers: PositiveInt
    metadata: _SessionContextMetadataPayload | None = None


def parse_session_context_snapshot_boundary(
    payload: Mapping[str, object],
    *,
    path: str = "root",
) -> dict[str, object]:
    """Validate and parse session context snapshot payload."""
    parsed = parse_wire(
        boundary_id=_BOUNDARY_ID_SESSION_CONTEXT,
        payload=payload,
        model=_SessionContextSnapshotPayload,
        path=path,
    )
    parsed_data = parsed.model_dump(mode="python")
    return {str(key): value for key, value in parsed_data.items()}


__all__ = ["parse_session_context_snapshot_boundary"]
