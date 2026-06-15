"""Tests for the pentanomial SPRT auto-gate and the live completion-feed wiring."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from shogiarena._core.contexts.game_session.application.completion.session_service import (
    TournamentSessionCompletionService,
)
from shogiarena._core.contexts.game_session.application.sprt_service import (
    SPRT_MODEL_GSPRT_PENTANOMIAL,
    Sprt,
    validate_pentanomial_preconditions,
)
from shogiarena._core.shared.kernel.game_results import GameResult


def test_validate_pentanomial_preconditions_accepts_valid_schedule() -> None:
    validate_pentanomial_preconditions(flip_policy="pair_both", num_engines=2, games_per_pair=2)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"flip_policy": "alternate", "num_engines": 2, "games_per_pair": 2}, "pair_both"),
        ({"flip_policy": "pair_both", "num_engines": 3, "games_per_pair": 2}, "2 engines"),
        ({"flip_policy": "pair_both", "num_engines": 2, "games_per_pair": 3}, "even"),
    ],
)
def test_validate_pentanomial_preconditions_rejects(kwargs: dict[str, object], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        validate_pentanomial_preconditions(**kwargs)  # type: ignore[arg-type]


def _context(sprt: Sprt) -> object:
    return SimpleNamespace(sprt_service=sprt, sprt_pair=("tested", "base"))


def _spec(*, black: str, white: str, round_num: int, sfen: str = "sfenX") -> object:
    return SimpleNamespace(
        black_engine=black, white_engine=white, initial_sfen=sfen, round_num=round_num, game_id=f"g{round_num:04d}-x"
    )


def test_update_sprt_state_routes_pentanomial_to_observation() -> None:
    sprt = Sprt(elo0=0.0, elo1=5.0, model=SPRT_MODEL_GSPRT_PENTANOMIAL)
    service = TournamentSessionCompletionService()
    # round_num 2 -> pair_slot 1; tested plays black and wins -> score 1.0 buffered (not yet paired).
    service.update_sprt_state(
        _context(sprt), _spec(black="tested", white="base", round_num=2), result=GameResult.BLACK_WIN
    )

    assert sprt.games_played == 1
    pending = sprt.to_snapshot()["pending"]
    assert len(pending) == 1
    assert pending[0]["pair_slot"] == 1
    assert pending[0]["is_tested_black"] is True
    assert pending[0]["score"] == 1.0


def test_update_sprt_state_pentanomial_completes_pair_across_reversed_rounds() -> None:
    sprt = Sprt(elo0=0.0, elo1=5.0, model=SPRT_MODEL_GSPRT_PENTANOMIAL)
    service = TournamentSessionCompletionService()
    # Rounds 2 and 3 share pair_slot 1; tested wins both colours -> pair score 2.0 -> WW bin.
    service.update_sprt_state(
        _context(sprt), _spec(black="tested", white="base", round_num=2), result=GameResult.BLACK_WIN
    )
    service.update_sprt_state(
        _context(sprt), _spec(black="base", white="tested", round_num=3), result=GameResult.WHITE_WIN
    )

    snapshot = sprt.to_snapshot()
    assert snapshot["pending"] == []
    assert snapshot["penta_bins"] == [0, 0, 0, 0, 1]
    assert sprt.games_played == 2


def test_update_sprt_state_routes_trinomial_to_add_game_result() -> None:
    sprt = Sprt(elo0=0.0, elo1=5.0)  # trinomial (default)
    service = TournamentSessionCompletionService()
    service.update_sprt_state(
        _context(sprt), _spec(black="tested", white="base", round_num=0), result=GameResult.BLACK_WIN
    )

    assert sprt.wins == 1
    assert sprt.games_played == 1
    assert sprt.to_snapshot()["penta_bins"] == [0, 0, 0, 0, 0]


def test_update_sprt_state_ignores_games_without_the_tested_engine() -> None:
    sprt = Sprt(elo0=0.0, elo1=5.0, model=SPRT_MODEL_GSPRT_PENTANOMIAL)
    service = TournamentSessionCompletionService()
    service.update_sprt_state(
        _context(sprt), _spec(black="other", white="base", round_num=2), result=GameResult.BLACK_WIN
    )

    assert sprt.games_played == 0
    assert sprt.to_snapshot()["pending"] == []


@pytest.mark.parametrize("model", [None, SPRT_MODEL_GSPRT_PENTANOMIAL])
def test_update_sprt_state_skips_paused_games(model: str | None) -> None:
    # PAUSED is a normal interruption path; it must be excluded from the SPRT sample, not folded
    # into a draw.
    sprt = Sprt(elo0=0.0, elo1=5.0) if model is None else Sprt(elo0=0.0, elo1=5.0, model=model)
    service = TournamentSessionCompletionService()
    service.update_sprt_state(
        _context(sprt), _spec(black="tested", white="base", round_num=2), result=GameResult.PAUSED
    )

    assert sprt.games_played == 0
    assert sprt.to_snapshot()["pending"] == []


@pytest.mark.parametrize("result", [GameResult.ERROR, GameResult.INVALID])
def test_update_sprt_state_fails_fast_on_non_decisive_results(result: GameResult) -> None:
    # ERROR/INVALID are upstream bugs in SPRT mode and must surface rather than silently distort
    # the test by counting as draws.
    sprt = Sprt(elo0=0.0, elo1=5.0)
    service = TournamentSessionCompletionService()
    with pytest.raises(ValueError, match="non-decisive"):
        service.update_sprt_state(_context(sprt), _spec(black="tested", white="base", round_num=0), result=result)

    assert sprt.games_played == 0
