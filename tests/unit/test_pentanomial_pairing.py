"""Tests for the shared tested-perspective pentanomial pairing helpers."""

from __future__ import annotations

import pytest

from shogiarena._core.shared.kernel.game_results import GameResult
from shogiarena._core.shared.kernel.statistics.pentanomial_pairing import (
    compute_pentanomial_bins,
    is_decisive_result,
    pair_score_bin_index,
    round_index_from_game_name,
    should_sample_for_sprt,
)
from shogiarena._core.shared.kernel.statistics.pentanomial_pairing import (
    tested_score as score_from_tested,  # aliased: a `tested_*` name is collected by pytest as a test
)

_DRAW = GameResult.DRAW_BY_REPETITION


def _game(black: str, white: str, result: GameResult, game_name: str, sfen: str = "startpos") -> dict[str, object]:
    return {
        "black_player": black,
        "white_player": white,
        "result": result,
        "game_name": game_name,
        "initial_sfen": sfen,
    }


def test_tested_score_is_from_tested_perspective() -> None:
    # Tested as black.
    assert score_from_tested(GameResult.BLACK_WIN, is_tested_black=True) == 1.0
    assert score_from_tested(GameResult.WHITE_WIN, is_tested_black=True) == 0.0
    # Tested as white (not inverted by dictionary order).
    assert score_from_tested(GameResult.WHITE_WIN, is_tested_black=False) == 1.0
    assert score_from_tested(GameResult.BLACK_WIN, is_tested_black=False) == 0.0
    # Draws are 0.5 regardless of colour.
    assert score_from_tested(_DRAW, is_tested_black=True) == 0.5
    assert score_from_tested(_DRAW, is_tested_black=False) == 0.5


@pytest.mark.parametrize(
    "result",
    [GameResult.BLACK_WIN, GameResult.WHITE_WIN, GameResult.DRAW_BY_REPETITION, GameResult.DRAW_BY_MAX_PLIES],
)
def test_should_sample_for_sprt_accepts_decisive_and_draw(result: GameResult) -> None:
    assert should_sample_for_sprt(result, context="t") is True


def test_should_sample_for_sprt_excludes_paused() -> None:
    # PAUSED is a normal interruption path: excluded from the sample, not an error.
    assert should_sample_for_sprt(GameResult.PAUSED, context="t") is False


@pytest.mark.parametrize("result", [GameResult.ERROR, GameResult.INVALID])
def test_should_sample_for_sprt_fails_fast_on_non_game_outcomes(result: GameResult) -> None:
    with pytest.raises(ValueError, match="non-decisive"):
        should_sample_for_sprt(result, context="game g0001-x")


@pytest.mark.parametrize(
    ("score", "expected"),
    [(0.0, 0), (0.5, 1), (1.0, 2), (1.5, 3), (2.0, 4)],
)
def test_pair_score_bin_index(score: float, expected: int) -> None:
    assert pair_score_bin_index(score) == expected


def test_round_index_from_game_name() -> None:
    assert round_index_from_game_name("g0001-dev-base") == 0
    assert round_index_from_game_name("g0002-dev-base") == 1
    assert round_index_from_game_name("no-round-token") is None
    assert round_index_from_game_name("g0000-x") is None  # 1-based, 0 is invalid


def test_compute_bins_winning_pair_lands_in_ww() -> None:
    games = [
        _game("tested", "base", GameResult.BLACK_WIN, "g0001-x"),  # tested black wins -> 1.0
        _game("base", "tested", GameResult.WHITE_WIN, "g0002-x"),  # tested white wins -> 1.0
    ]
    assert compute_pentanomial_bins(games, tested_engine="tested", base_engine="base") == [0, 0, 0, 0, 1]


def test_compute_bins_losing_pair_lands_in_ll() -> None:
    games = [
        _game("tested", "base", GameResult.WHITE_WIN, "g0001-x"),  # tested black, white wins -> 0.0
        _game("base", "tested", GameResult.BLACK_WIN, "g0002-x"),  # tested white, black wins -> 0.0
    ]
    assert compute_pentanomial_bins(games, tested_engine="tested", base_engine="base") == [1, 0, 0, 0, 0]


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (GameResult.BLACK_WIN, True),
        (GameResult.WHITE_WIN, True),
        (GameResult.DRAW_BY_REPETITION, True),
        (GameResult.ERROR, False),
        (GameResult.INVALID, False),
        (GameResult.PAUSED, False),
    ],
)
def test_is_decisive_result(result: GameResult, expected: bool) -> None:
    assert is_decisive_result(result) is expected


def test_compute_bins_excludes_non_decided_pair() -> None:
    # Regression: an ERROR + PAUSED pair previously scored 0.5 + 0.5 -> DD (bin 2). Non-decided
    # games must be dropped, so the pair never forms.
    games = [
        _game("tested", "base", GameResult.ERROR, "g0001-x"),
        _game("base", "tested", GameResult.PAUSED, "g0002-x"),
    ]
    assert compute_pentanomial_bins(games, tested_engine="tested", base_engine="base") == [0, 0, 0, 0, 0]


def test_compute_bins_drops_incomplete_pair_when_one_game_crashes() -> None:
    # tested wins as black, but the reversed-colour game errored -> no complete pair -> no bin.
    games = [
        _game("tested", "base", GameResult.BLACK_WIN, "g0001-x"),
        _game("base", "tested", GameResult.ERROR, "g0002-x"),
    ]
    assert compute_pentanomial_bins(games, tested_engine="tested", base_engine="base") == [0, 0, 0, 0, 0]


def test_compute_bins_double_draw_lands_in_dd() -> None:
    games = [
        _game("tested", "base", _DRAW, "g0001-x"),
        _game("base", "tested", _DRAW, "g0002-x"),
    ]
    assert compute_pentanomial_bins(games, tested_engine="tested", base_engine="base") == [0, 0, 1, 0, 0]


def test_compute_bins_ignores_unpaired_and_tokenless_games() -> None:
    # Only the tested-black half of the pair is present -> no completed pair.
    only_black = [_game("tested", "base", GameResult.BLACK_WIN, "g0001-x")]
    assert compute_pentanomial_bins(only_black, tested_engine="tested", base_engine="base") == [0, 0, 0, 0, 0]

    # No recoverable round token -> ignored.
    tokenless = [
        _game("tested", "base", GameResult.BLACK_WIN, "x"),
        _game("base", "tested", GameResult.WHITE_WIN, "y"),
    ]
    assert compute_pentanomial_bins(tokenless, tested_engine="tested", base_engine="base") == [0, 0, 0, 0, 0]


def test_compute_bins_separates_distinct_openings() -> None:
    games = [
        _game("tested", "base", GameResult.BLACK_WIN, "g0001-x", sfen="sfenA"),
        _game("base", "tested", GameResult.WHITE_WIN, "g0002-x", sfen="sfenA"),
        _game("tested", "base", _DRAW, "g0001-y", sfen="sfenB"),
        _game("base", "tested", _DRAW, "g0002-y", sfen="sfenB"),
    ]
    # One WW pair (sfenA) and one DD pair (sfenB).
    assert compute_pentanomial_bins(games, tested_engine="tested", base_engine="base") == [0, 0, 1, 0, 1]


def test_compute_bins_does_not_pair_across_opponents() -> None:
    # tested vs baseA (black) and tested vs baseB (white) on the same opening must NOT be zipped
    # into a fictitious pair; only the requested 1v1 matchup is counted.
    games = [
        _game("tested", "baseA", GameResult.BLACK_WIN, "g0001-x"),
        _game("baseB", "tested", GameResult.WHITE_WIN, "g0002-x"),
    ]
    assert compute_pentanomial_bins(games, tested_engine="tested", base_engine="baseA") == [0, 0, 0, 0, 0]
    assert compute_pentanomial_bins(games, tested_engine="tested", base_engine="baseB") == [0, 0, 0, 0, 0]
