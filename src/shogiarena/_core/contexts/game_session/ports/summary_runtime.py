"""Runtime contract for tournament summary orchestration."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol

from shogiarena._core.shared.kernel.json_types import JsonObject


class TournamentSummaryModelDumpPort(Protocol):
    """Minimal model-dump contract used by summary payload assembly."""

    def model_dump(self, *, mode: str) -> dict[str, object]: ...


class SummaryGameSpecPort(Protocol):
    """Schedule item contract required by summary services."""

    @property
    def game_id(self) -> str: ...


class TournamentSummaryConfigPort(Protocol):
    """Minimal tournament configuration contract required by summaries."""

    @property
    def engines(self) -> Sequence[Any]: ...

    @property
    def rules(self) -> Any: ...

    @property
    def rating(self) -> Any: ...

    @property
    def generate(self) -> TournamentSummaryModelDumpPort | None: ...

    @property
    def records_output(self) -> TournamentSummaryModelDumpPort | None: ...


class TournamentSummaryRecordWriterPort(Protocol):
    """Record writer contract required by summary services."""

    def get_records_summary(self) -> JsonObject: ...


class TournamentSummaryApiServerPort(Protocol):
    """Dashboard broadcast contract used by summary services."""

    def broadcast_games_snapshot(self, schedule_snapshot: JsonObject) -> None: ...
    def broadcast_summary_update(self, summary: JsonObject, *, source: str) -> None: ...


__all__ = [
    "SummaryGameSpecPort",
    "TournamentSummaryApiServerPort",
    "TournamentSummaryConfigPort",
    "TournamentSummaryRecordWriterPort",
]
