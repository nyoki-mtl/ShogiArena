"""Completion lifecycle port contracts and DTOs for game-runtime sessions."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, MutableMapping, MutableSet
from dataclasses import dataclass
from typing import Protocol, TypedDict

import rshogi.record

from shogiarena._core.shared.kernel.service_ports import (
    DatabaseServicePort,
    RatingServicePort,
    SprtServicePort,
)


class CompletionRecordWriterPort(Protocol):
    """Minimal record-writer contract used by completion service."""

    def append_record(self, record: rshogi.record.GameRecord) -> None: ...


class CompletionStopControllerPort(Protocol):
    """Minimal stop-controller contract used by completion service."""

    def request_stop(self, *, reason: str | None = None) -> None: ...


class CompletionGameSpecPort(Protocol):
    """Minimal game-spec contract consumed by completion service."""

    @property
    def game_id(self) -> str: ...

    @property
    def black_engine(self) -> str: ...

    @property
    def white_engine(self) -> str: ...


class CompletionGameSummary(TypedDict, total=False):
    game_result: str | None
    total_plies: int | None
    start_time: str | None
    end_time: str | None


@dataclass(slots=True)
class CompletionMetadataContext:
    is_generate_run: bool
    summary_source: str
    experiment_name: str | None
    record_format: str | None


@dataclass(slots=True)
class CompletionPersistenceContext:
    db_service: DatabaseServicePort | None
    record_writer: CompletionRecordWriterPort | None


@dataclass(slots=True)
class CompletionRatingContext:
    rating_service: RatingServicePort | None


@dataclass(slots=True)
class CompletionStateContext:
    completed_game_ids: MutableSet[str]
    completed_game_summaries: MutableMapping[str, CompletionGameSummary]
    sprt_service: SprtServicePort | None
    sprt_pair: tuple[str, str] | None
    sprt_min_games: int
    stop_controller: CompletionStopControllerPort
    is_dashboard_enabled: bool
    total_games: int
    save_run_state: Callable[[], None]


@dataclass(slots=True)
class CompletionOpenBenchContext:
    is_strict_mode: bool
    sync_after_game: Callable[[], Awaitable[None]]


@dataclass(slots=True)
class CompletionRuntimeContext:
    metadata: CompletionMetadataContext
    persistence: CompletionPersistenceContext
    rating: CompletionRatingContext
    state: CompletionStateContext
    openbench: CompletionOpenBenchContext


__all__ = [
    "CompletionGameSpecPort",
    "CompletionGameSummary",
    "CompletionMetadataContext",
    "CompletionOpenBenchContext",
    "CompletionPersistenceContext",
    "CompletionRatingContext",
    "CompletionRecordWriterPort",
    "CompletionRuntimeContext",
    "CompletionStateContext",
    "CompletionStopControllerPort",
]
