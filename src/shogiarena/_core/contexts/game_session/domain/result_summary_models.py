"""Domain models for offline result summaries."""

from __future__ import annotations

from dataclasses import dataclass, field

from shogiarena._core.shared.kernel.game_results import GameResult
from shogiarena._core.shared.kernel.json_types import JsonObject


@dataclass(frozen=True, slots=True)
class ResultSummaryGameRow:
    """保存済みDBから読み出した1対局分の集計入力。"""

    game_name: str
    black_player: str
    white_player: str
    result: GameResult
    raw_result: str


@dataclass(slots=True)
class ResultSummaryWdl:
    """勝敗分布。"""

    wins: int = 0
    draws: int = 0
    losses: int = 0

    @property
    def games(self) -> int:
        return self.wins + self.draws + self.losses

    @property
    def score(self) -> float:
        return float(self.wins) + 0.5 * float(self.draws)

    @property
    def score_rate(self) -> float | None:
        return self.score / float(self.games) if self.games > 0 else None

    @property
    def win_rate_excluding_draws(self) -> float | None:
        decisive = self.wins + self.losses
        if decisive <= 0:
            return None
        return float(self.wins) / float(decisive)

    @property
    def draw_rate(self) -> float | None:
        return float(self.draws) / float(self.games) if self.games > 0 else None

    def to_payload(self) -> JsonObject:
        return {
            "games": self.games,
            "wins": self.wins,
            "draws": self.draws,
            "losses": self.losses,
            "score": self.score,
            "score_rate": self.score_rate,
            "win_rate_excluding_draws": self.win_rate_excluding_draws,
            "draw_rate": self.draw_rate,
        }


@dataclass(slots=True)
class EngineResultSummary:
    """エンジン単位の結果集計。"""

    engine: str
    total: ResultSummaryWdl = field(default_factory=ResultSummaryWdl)
    black: ResultSummaryWdl = field(default_factory=ResultSummaryWdl)
    white: ResultSummaryWdl = field(default_factory=ResultSummaryWdl)
    score_confidence_interval: tuple[float, float] | None = None

    def to_payload(self) -> JsonObject:
        payload = self.total.to_payload()
        payload["engine"] = self.engine
        payload["score_confidence_interval"] = (
            {
                "low": self.score_confidence_interval[0],
                "high": self.score_confidence_interval[1],
            }
            if self.score_confidence_interval is not None
            else None
        )
        payload["side_split"] = {
            "black": self.black.to_payload(),
            "white": self.white.to_payload(),
        }
        return payload


@dataclass(frozen=True, slots=True)
class OfflineResultSummary:
    """保存済みrunの結果サマリ。"""

    source: str
    run_dir: str | None
    shogiarena_version: str | None
    total_scheduled_games: int | None
    completed_games: int
    incomplete_games: int | None
    failed_games: int | None
    confidence: float
    engines: tuple[EngineResultSummary, ...]
    draw_rate: float | None
    raw_result_counts: dict[str, int]
    failures: tuple[JsonObject, ...] = ()
    failures_by_phase: JsonObject | None = None
    manifest_status: str | None = None
    is_resumable: bool | None = None

    def to_payload(self) -> JsonObject:
        return {
            "schema_version": 1,
            "source": self.source,
            "run_dir": self.run_dir,
            "shogiarena_version": self.shogiarena_version,
            "total_scheduled_games": self.total_scheduled_games,
            "completed_games": self.completed_games,
            "incomplete_games": self.incomplete_games,
            "failed_games": self.failed_games,
            "confidence": self.confidence,
            "draw_rate": self.draw_rate,
            "engines": [engine.to_payload() for engine in self.engines],
            "raw_result_counts": dict(sorted(self.raw_result_counts.items())),
            "failures": [dict(failure) for failure in self.failures],
            "failures_by_phase": dict(self.failures_by_phase or {}),
            "manifest_status": self.manifest_status,
            "is_resumable": self.is_resumable,
        }


__all__ = [
    "EngineResultSummary",
    "OfflineResultSummary",
    "ResultSummaryGameRow",
    "ResultSummaryWdl",
]
