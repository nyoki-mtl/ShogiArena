"""Offline result summary aggregation service."""

from __future__ import annotations

from dataclasses import dataclass
from statistics import NormalDist

from rshogi.types import Color

from shogiarena._core.contexts.game_session.domain.result_summary_models import (
    EngineResultSummary,
    OfflineResultSummary,
    ResultSummaryWdl,
)
from shogiarena._core.contexts.game_session.ports.result_summary_reader import ResultSummaryReaderPort
from shogiarena._core.shared.kernel.game_record_types import game_result_score
from shogiarena._core.shared.kernel.json_types import JsonObject


@dataclass(frozen=True, slots=True)
class OfflineResultSummaryRequest:
    """保存済み結果サマリの入力。"""

    source: str
    run_dir: str | None = None
    shogiarena_version: str | None = None
    total_scheduled_games: int | None = None
    failed_games: int | None = None
    failures: tuple[JsonObject, ...] = ()
    failures_by_phase: JsonObject | None = None
    manifest_status: str | None = None
    is_resumable: bool | None = None
    confidence: float = 0.95


class OfflineResultSummaryService:
    """Persisted game rows から標準的な対局結果サマリを構築する。"""

    def build_summary(
        self,
        reader: ResultSummaryReaderPort,
        request: OfflineResultSummaryRequest,
    ) -> OfflineResultSummary:
        if not 0.0 < request.confidence < 1.0:
            raise ValueError("confidence must be in the open interval (0, 1)")

        games = reader.read_games()
        engine_stats: dict[str, EngineResultSummary] = {}
        raw_result_counts: dict[str, int] = {}
        draw_count = 0

        for row in games:
            raw_result_counts[row.raw_result] = raw_result_counts.get(row.raw_result, 0) + 1
            black = engine_stats.setdefault(row.black_player, EngineResultSummary(engine=row.black_player))
            white = engine_stats.setdefault(row.white_player, EngineResultSummary(engine=row.white_player))

            black_score = game_result_score(row.result, Color.BLACK)
            white_score = game_result_score(row.result, Color.WHITE)
            if black_score is None or white_score is None:
                continue
            self._apply_score(black.total, black_score)
            self._apply_score(black.black, black_score)
            self._apply_score(white.total, white_score)
            self._apply_score(white.white, white_score)
            if row.result.is_draw():
                draw_count += 1

        engines = tuple(
            self._with_confidence_interval(engine, confidence=request.confidence)
            for engine in sorted(engine_stats.values(), key=lambda item: item.engine)
        )
        completed = len(games)
        incomplete = None
        if request.total_scheduled_games is not None:
            incomplete = max(0, request.total_scheduled_games - completed)
        draw_rate = float(draw_count) / float(completed) if completed > 0 else None
        return OfflineResultSummary(
            source=request.source,
            run_dir=request.run_dir,
            shogiarena_version=request.shogiarena_version,
            total_scheduled_games=request.total_scheduled_games,
            completed_games=completed,
            incomplete_games=incomplete,
            failed_games=request.failed_games if request.failed_games is not None else len(request.failures),
            confidence=request.confidence,
            engines=engines,
            draw_rate=draw_rate,
            raw_result_counts=raw_result_counts,
            failures=tuple(dict(failure) for failure in request.failures),
            failures_by_phase=request.failures_by_phase,
            manifest_status=request.manifest_status,
            is_resumable=request.is_resumable,
        )

    @staticmethod
    def _apply_score(wdl: ResultSummaryWdl, score: float) -> None:
        if score >= 1.0:
            wdl.wins += 1
        elif score <= 0.0:
            wdl.losses += 1
        else:
            wdl.draws += 1

    def _with_confidence_interval(
        self,
        engine: EngineResultSummary,
        *,
        confidence: float,
    ) -> EngineResultSummary:
        interval = self._wilson_interval(
            successes=engine.total.score,
            trials=engine.total.games,
            confidence=confidence,
        )
        engine.score_confidence_interval = interval
        return engine

    @staticmethod
    def _wilson_interval(
        *,
        successes: float,
        trials: int,
        confidence: float,
    ) -> tuple[float, float] | None:
        if trials <= 0:
            return None
        alpha = 1.0 - confidence
        z = NormalDist().inv_cdf(1.0 - alpha / 2.0)
        n = float(trials)
        phat = successes / n
        denominator = 1.0 + (z * z / n)
        center = (phat + (z * z) / (2.0 * n)) / denominator
        margin = z * ((phat * (1.0 - phat) / n + (z * z) / (4.0 * n * n)) ** 0.5) / denominator
        return max(0.0, center - margin), min(1.0, center + margin)


__all__ = [
    "OfflineResultSummaryRequest",
    "OfflineResultSummaryService",
]
