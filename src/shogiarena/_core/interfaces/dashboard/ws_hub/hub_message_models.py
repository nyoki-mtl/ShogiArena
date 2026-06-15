"""Message models for the dashboard WebSocket hub."""

from __future__ import annotations

from collections.abc import Callable, Iterable

from pydantic import BaseModel, ConfigDict, Field, field_validator

from shogiarena._core.shared.kernel.json_types import JsonObject, JsonValue
from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_int

LiveBootstrapProvider = Callable[[set[int] | None], Iterable[tuple[str, JsonObject]]]
LiveSnapshotResolver = Callable[[str], Iterable[tuple[str, JsonObject]]]


class DashboardWsClientMessage(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: str | None = None
    topic: str | None = None
    topics: list[str] | None = None
    from_seq: int | None = None
    should_include_analysis: bool | None = Field(default=None, alias="include_analysis")
    workers: list[int] | None = None

    @field_validator("topics", mode="before")
    @classmethod
    def _coerce_topics(cls, value: JsonValue | None) -> list[str] | None:
        if value is None:
            return None
        if not isinstance(value, list):
            raise TypeError("topics must be a list")
        return [str(item) for item in value]

    @field_validator("workers", mode="before")
    @classmethod
    def _coerce_workers(cls, value: JsonValue | None) -> list[int] | None:
        if value is None:
            return None
        if not isinstance(value, list):
            raise TypeError("workers must be a list")
        parsed: list[int] = []
        for raw in value:
            if (coerced := coerce_int(raw)) is not None:
                parsed.append(coerced)
        return parsed


class WsClientMessage(DashboardWsClientMessage):
    """Live dashboard WebSocket control message."""


__all__ = [
    "LiveBootstrapProvider",
    "LiveSnapshotResolver",
    "DashboardWsClientMessage",
    "WsClientMessage",
]
