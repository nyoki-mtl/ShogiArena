"""Snapshot storage port protocol for dashboard application layer."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Literal, Protocol, TypeAlias

from shogiarena._core.contexts.dashboard.application.events import GamesSnapshotPayload
from shogiarena._core.shared.kernel.json_types import JsonObject, JsonValue

SnapshotPayloadKind: TypeAlias = Literal["summary", "games"]


class SnapshotPayloadParserFn(Protocol):
    """Boundary parser for dashboard snapshot payloads."""

    def __call__(
        self,
        kind: SnapshotPayloadKind,
        payload: Mapping[str, object],
        *,
        path: str = ...,
    ) -> JsonObject: ...


class SnapshotPayloadSerializerFn(Protocol):
    """Boundary serializer for dashboard snapshot payloads."""

    def __call__(
        self,
        kind: SnapshotPayloadKind,
        payload: Mapping[str, object] | JsonObject,
        *,
        path: str = ...,
    ) -> JsonObject: ...


class SnapshotStoragePort(Protocol):
    """Port for snapshot storage operations used by application services."""

    def store_summary(
        self,
        payload: Mapping[str, JsonValue],
        *,
        source: str,
    ) -> JsonObject | None: ...

    @staticmethod
    def extract_summary_diff(
        previous: Mapping[str, JsonValue] | None,
        current: Mapping[str, JsonValue],
    ) -> JsonObject: ...

    def store_games(self, payload: Mapping[str, JsonValue]) -> GamesSnapshotPayload | None: ...

    def compute_games_delta(
        self,
        previous: Mapping[str, JsonValue] | None,
        rows: Sequence[Mapping[str, JsonValue]],
        snapshot_meta: Mapping[str, JsonValue],
        *,
        revision: int,
        base_revision: int | None,
    ) -> GamesSnapshotPayload: ...

    @staticmethod
    def minimise_patch(payload: Mapping[str, JsonValue]) -> JsonObject: ...
