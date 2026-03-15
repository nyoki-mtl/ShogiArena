from __future__ import annotations

from shogiarena._core.shared.kernel.statistics.btd_rating import BTDEstimator


def test_btd_estimator_estimates_two_engine_match() -> None:
    games = [
        {"black_player": "A", "white_player": "B", "result": "BLACK_WIN"},
        {"black_player": "B", "white_player": "A", "result": "WHITE_WIN"},
        {"black_player": "A", "white_player": "B", "result": "DRAW"},
    ]

    result = BTDEstimator().estimate(games, anchor_name="B")

    assert result.anchor == "B"
    assert set(result.ratings.keys()) == {"A", "B"}
    assert result.ratings["B"] == 0.0
    assert result.rating_cov is not None
    assert ("A", "A") in result.rating_cov
    assert result.gamma_elo_se is not None
    assert result.nu > 0.0
    assert 0.0 < result.draw_eq < 1.0


def test_btd_estimator_returns_zeroes_when_no_games_with_engine_names() -> None:
    result = BTDEstimator().estimate([], anchor_name="engine-c", engine_names=["engine-a", "engine-c"])

    assert result.anchor == "engine-c"
    assert result.ratings == {"engine-a": 0.0, "engine-c": 0.0}
    assert result.rating_se == {"engine-a": 0.0, "engine-c": 0.0}
    assert result.rating_cov is None
    assert result.gamma_elo == 0.0
    assert result.draw_eq == 0.5
