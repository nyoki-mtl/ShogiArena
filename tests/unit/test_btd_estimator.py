from __future__ import annotations

from shogiarena._core.shared.kernel.database_types import GameRecordPlayers
from shogiarena._core.shared.kernel.game_results import GameResult
from shogiarena._core.shared.kernel.statistics.btd_rating import BTDEstimator

_DRAW = GameResult.DRAW_BY_REPETITION


def _game(black: str, white: str, result: GameResult) -> GameRecordPlayers:
    return {"black_player": black, "white_player": white, "result": result}


def _repeat(black: str, white: str, result: GameResult, n: int) -> list[GameRecordPlayers]:
    return [_game(black, white, result) for _ in range(n)]


def test_btd_estimator_estimates_two_engine_match() -> None:
    games = [
        _game("A", "B", GameResult.BLACK_WIN),
        _game("B", "A", GameResult.WHITE_WIN),
        _game("A", "B", _DRAW),
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


def test_gamma_elo_is_the_full_first_move_advantage() -> None:
    # C9a: equal engines, black wins 75% symmetrically. The actual black-vs-white advantage is
    # ~400*log10(3) ~= 191 Elo; the old half-advantage formula would report only ~95.
    games = (
        _repeat("A", "B", GameResult.BLACK_WIN, 30)
        + _repeat("A", "B", GameResult.WHITE_WIN, 10)
        + _repeat("B", "A", GameResult.BLACK_WIN, 30)
        + _repeat("B", "A", GameResult.WHITE_WIN, 10)
    )
    result = BTDEstimator().estimate(games, anchor_name="B")
    assert result.gamma_elo > 150.0  # clearly the 2*gamma value, not the ~95 half-advantage
    assert abs(result.gamma_elo - 191.0) < 40.0


def test_draw_eq_accounts_for_colour_advantage() -> None:
    # C9b: with a colour advantage (gamma != 0), draw_eq = nu/(nu + cosh gamma) is strictly below
    # the colour-blind nu/(1+nu).
    games = (
        _repeat("A", "B", GameResult.BLACK_WIN, 20)
        + _repeat("A", "B", _DRAW, 20)
        + _repeat("A", "B", GameResult.WHITE_WIN, 5)
        + _repeat("B", "A", GameResult.BLACK_WIN, 20)
        + _repeat("B", "A", _DRAW, 20)
        + _repeat("B", "A", GameResult.WHITE_WIN, 5)
    )
    result = BTDEstimator().estimate(games, anchor_name="B")
    naive = result.nu / (1.0 + result.nu)
    assert result.draw_eq < naive
    assert result.draw_eq_se is not None


def test_disconnected_graph_reports_none_standard_errors() -> None:
    # C9c: {A,B} and {C,D} never play each other, so their relative level is unidentifiable and the
    # standard errors must be None (not a misleadingly precise 0 or a huge ridge-inflated value).
    games = [
        _game("A", "B", GameResult.BLACK_WIN),
        _game("B", "A", GameResult.BLACK_WIN),
        _game("C", "D", GameResult.BLACK_WIN),
        _game("D", "C", GameResult.BLACK_WIN),
    ]
    result = BTDEstimator().estimate(games, anchor_name="A", engine_names=["A", "B", "C", "D"])
    assert result.rating_se["B"] is None
    assert result.rating_se["C"] is None
    assert result.rating_se["A"] == 0.0  # anchor stays an exact reference
    assert result.gamma_elo_se is None
    assert result.draw_eq_se is None
    assert result.nu_se is None
    # rating_cov must be None too, otherwise pair_delta() would report a misleading 0.0 pair SE.
    assert result.rating_cov is None
    assert result.pair_delta("B", "C", cov=result.rating_cov).standard_error is None
