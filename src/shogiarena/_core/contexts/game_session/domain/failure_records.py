"""Run failure record models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Final, Literal, cast

from shogiarena._core.shared.kernel.json_types import JsonObject

FailurePhase = Literal[
    "engine_start",
    "isready",
    "think",
    "move_validation",
    "persistence",
    "shutdown",
    "user_interruption",
    "unknown",
]

FAILURE_PHASE_VALUES: Final = frozenset(
    {"engine_start", "isready", "think", "move_validation", "persistence", "shutdown", "user_interruption", "unknown"}
)


def coerce_failure_phase(raw: str | None) -> FailurePhase:
    """失敗 phase 文字列を正規値へ丸める。"""

    if raw in FAILURE_PHASE_VALUES:
        return cast(FailurePhase, raw)
    return "unknown"


@dataclass(frozen=True, slots=True)
class RunFailureRecord:
    """run 中に発生した構造化失敗レコード。"""

    game_id: str | None
    scheduled_black_engine: str | None
    scheduled_white_engine: str | None
    failure_phase: FailurePhase
    exception_class: str
    short_message: str
    occurred_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    engine: str | None = None
    log_artifact_path: str | None = None
    diagnostic: JsonObject | None = None

    def to_payload(self) -> JsonObject:
        payload: JsonObject = {
            "game_id": self.game_id,
            "scheduled_black_engine": self.scheduled_black_engine,
            "scheduled_white_engine": self.scheduled_white_engine,
            "failure_phase": self.failure_phase,
            "exception_class": self.exception_class,
            "short_message": self.short_message,
            "occurred_at": self.occurred_at,
            "engine": self.engine,
            "log_artifact_path": self.log_artifact_path,
        }
        if self.diagnostic is not None:
            payload["diagnostic"] = dict(self.diagnostic)
        return payload


__all__ = ["FAILURE_PHASE_VALUES", "FailurePhase", "RunFailureRecord", "coerce_failure_phase"]
