"""I/O model for persisted SPSA summary cache snapshots."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class SummaryCachePayload(BaseModel):
    """Serialized cache payload for SPSA summary aggregation."""

    version: int = 1
    session_uuid: str | None = None
    events_offset: int = 0
    events_size: int = 0
    events_mtime_ns: int = 0
    aggregates: dict[str, object] = Field(default_factory=dict)

    model_config = ConfigDict(extra="ignore")


__all__ = ["SummaryCachePayload"]
