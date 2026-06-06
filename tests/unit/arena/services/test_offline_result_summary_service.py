from __future__ import annotations

from shogiarena._core.contexts.game_session.application.summary.offline_result_summary_service import (
    OfflineResultSummaryRequest,
    OfflineResultSummaryService,
)
from shogiarena._core.contexts.game_session.domain.result_summary_models import ResultSummaryGameRow
from shogiarena._core.shared.kernel.game_results import GameResult


class _Reader:
    def read_games(self) -> tuple[ResultSummaryGameRow, ...]:
        return (
            ResultSummaryGameRow(
                game_name="g1",
                black_player="engine-a",
                white_player="engine-b",
                result=GameResult.BLACK_WIN,
                raw_result="BLACK_WIN",
            ),
            ResultSummaryGameRow(
                game_name="g2",
                black_player="engine-b",
                white_player="engine-a",
                result=GameResult.DRAW_BY_REPETITION,
                raw_result="DRAW_BY_REPETITION",
            ),
            ResultSummaryGameRow(
                game_name="g3",
                black_player="engine-a",
                white_player="engine-b",
                result=GameResult.WHITE_WIN,
                raw_result="WHITE_WIN",
            ),
        )


def test_offline_result_summary_aggregates_wdl_side_split_and_raw_counts() -> None:
    summary = OfflineResultSummaryService().build_summary(
        _Reader(),
        OfflineResultSummaryRequest(
            source="run",
            run_dir="/tmp/run",
            shogiarena_version="0.test",
            total_scheduled_games=4,
        ),
    )

    assert summary.completed_games == 3
    assert summary.incomplete_games == 1
    assert summary.draw_rate == 1 / 3
    assert summary.raw_result_counts == {
        "BLACK_WIN": 1,
        "DRAW_BY_REPETITION": 1,
        "WHITE_WIN": 1,
    }

    engines = {engine.engine: engine for engine in summary.engines}
    engine_a = engines["engine-a"]
    assert (engine_a.total.wins, engine_a.total.draws, engine_a.total.losses) == (1, 1, 1)
    assert engine_a.total.score_rate == 0.5
    assert (engine_a.black.wins, engine_a.black.draws, engine_a.black.losses) == (1, 0, 1)
    assert (engine_a.white.wins, engine_a.white.draws, engine_a.white.losses) == (0, 1, 0)
    assert engine_a.score_confidence_interval is not None


def test_offline_result_summary_rejects_invalid_confidence() -> None:
    try:
        OfflineResultSummaryService().build_summary(
            _Reader(),
            OfflineResultSummaryRequest(source="run", confidence=1.0),
        )
    except ValueError as exc:
        assert "confidence" in str(exc)
    else:
        raise AssertionError("expected invalid confidence to be rejected")
