"""Tests for the GSPRT-backed Sprt service (trinomial) and its model boundary."""

from __future__ import annotations

import pytest

from shogiarena._core.contexts.game_session.application.sprt_service import (
    SPRT_MODEL_GSPRT_PENTANOMIAL,
    SPRT_MODEL_GSPRT_TRINOMIAL,
    Sprt,
)
from shogiarena._core.shared.kernel.game_results import GameResult
from shogiarena._core.shared.kernel.run_artifact_hashes import build_sprt_test_definition
from shogiarena._core.shared.kernel.statistics.gsprt import compute_llr


def _play(sprt: Sprt, *, wins: int, draws: int, losses: int) -> None:
    for _ in range(wins):
        sprt.add_game_result(GameResult.WHITE_WIN)
    for _ in range(draws):
        sprt.add_game_result(GameResult.DRAW_BY_REPETITION)
    for _ in range(losses):
        sprt.add_game_result(GameResult.BLACK_WIN)


def test_default_model_is_gsprt_trinomial() -> None:
    assert Sprt(elo0=0.0, elo1=5.0).model == SPRT_MODEL_GSPRT_TRINOMIAL


def test_llr_matches_gsprt_from_counts() -> None:
    sprt = Sprt(elo0=0.0, elo1=5.0)
    _play(sprt, wins=20, draws=70, losses=10)
    assert sprt.wins == 20
    assert sprt.draws == 70
    assert sprt.losses == 10
    expected = compute_llr([sprt.losses, sprt.draws, sprt.wins], elo0=0.0, elo1=5.0)
    assert sprt.llr == pytest.approx(expected)


def test_draws_now_affect_llr() -> None:
    # The legacy model treated draws as a neutral lr=1.0 (no LLR change); under GSPRT, the number
    # of draws changes the empirical distribution (and therefore the LLR). Both samples populate
    # all three outcomes so the all-bins decision guard does not confound the comparison.
    few_draws = Sprt(elo0=0.0, elo1=5.0)
    _play(few_draws, wins=10, draws=2, losses=10)

    many_draws = Sprt(elo0=0.0, elo1=5.0)
    _play(many_draws, wins=10, draws=100, losses=10)

    assert few_draws.llr != pytest.approx(many_draws.llr)


def test_does_not_finish_on_tiny_one_sided_sample() -> None:
    # 2-0 (or 0-2) must not trigger a decision: with a degenerate one-sided sample the regularized
    # variance is tiny and the raw GSPRT LLR would otherwise blow past the bound after 2 games.
    wins_only = Sprt(elo0=0.0, elo1=5.0)
    _play(wins_only, wins=2, draws=0, losses=0)
    assert wins_only.llr == 0.0
    assert not wins_only.is_finished()

    losses_only = Sprt(elo0=0.0, elo1=5.0)
    _play(losses_only, wins=0, draws=0, losses=2)
    assert losses_only.llr == 0.0
    assert not losses_only.is_finished()


def test_llr_suppressed_until_every_outcome_occurs() -> None:
    sprt = Sprt(elo0=0.0, elo1=5.0)
    _play(sprt, wins=5, draws=0, losses=0)
    assert sprt.llr == 0.0  # no draws or losses yet
    sprt.add_game_result(GameResult.DRAW_BY_REPETITION)
    assert sprt.llr == 0.0  # still no losses
    sprt.add_game_result(GameResult.BLACK_WIN)
    # L, D, W are all populated now -> the real GSPRT LLR is used.
    assert sprt.llr == pytest.approx(compute_llr([sprt.losses, sprt.draws, sprt.wins], elo0=0.0, elo1=5.0))


def test_snapshot_roundtrip_preserves_model_and_recomputes_llr() -> None:
    sprt = Sprt(elo0=0.0, elo1=5.0)
    _play(sprt, wins=15, draws=30, losses=5)
    snapshot = sprt.to_snapshot()
    assert snapshot["model"] == SPRT_MODEL_GSPRT_TRINOMIAL

    restored = Sprt.from_snapshot(snapshot)
    assert restored.model == sprt.model
    assert (restored.wins, restored.draws, restored.losses) == (sprt.wins, sprt.draws, sprt.losses)
    assert restored.llr == pytest.approx(sprt.llr)


def test_unsupported_model_raises() -> None:
    with pytest.raises(ValueError, match="Unsupported SPRT model"):
        Sprt(elo0=0.0, elo1=5.0, model="not-a-real-model")


def test_sprt_test_definition_includes_model_for_resume_boundary() -> None:
    # The model is part of the resume test definition, so a payload carrying it differs from a
    # legacy payload without it (preventing cross-model resume compatibility).
    with_model = build_sprt_test_definition(
        {"model": "gsprt-trinomial-v1", "elo0": 0.0, "elo1": 5.0, "alpha": 0.05, "beta": 0.05}
    )
    without_model = build_sprt_test_definition({"elo0": 0.0, "elo1": 5.0, "alpha": 0.05, "beta": 0.05})
    assert with_model is not None and "model" in with_model
    assert without_model is not None and "model" not in without_model


def test_sprt_test_definition_includes_tested_engine_role() -> None:
    tested_dev = build_sprt_test_definition(
        {"tested_engine": "dev", "model": "gsprt-trinomial-v1", "elo0": 0.0, "elo1": 5.0}
    )
    tested_base = build_sprt_test_definition(
        {"tested_engine": "base", "model": "gsprt-trinomial-v1", "elo0": 0.0, "elo1": 5.0}
    )
    assert tested_dev != tested_base


# --- Pentanomial mode -------------------------------------------------------------------------
def _penta() -> Sprt:
    return Sprt(elo0=0.0, elo1=5.0, model=SPRT_MODEL_GSPRT_PENTANOMIAL)


def test_pentanomial_paired_observation_feeds_bins_and_llr() -> None:
    sprt = _penta()
    for _ in range(25):
        sprt.add_paired_observation(black_score=1.0, white_score=1.0)  # WW (bin 4)
    for _ in range(5):
        sprt.add_paired_observation(black_score=0.5, white_score=0.0)  # score 0.5 -> bin 1 (LD)
    assert sprt.games_played == 60  # 30 pairs * 2 games
    expected = compute_llr([0, 5, 0, 0, 25], elo0=0.0, elo1=5.0)
    assert sprt.llr == pytest.approx(expected)


def test_pentanomial_guard_blocks_known_two_pair_false_acceptance() -> None:
    sprt = _penta()
    for _ in range(2):
        sprt.add_paired_observation(black_score=1.0, white_score=1.0)
    assert sprt.llr == 0.0
    assert not sprt.is_finished()


def test_pentanomial_guard_blocks_known_seven_pair_two_bin_false_decision() -> None:
    sprt = _penta()
    for _ in range(6):
        sprt.add_paired_observation(black_score=0.0, white_score=0.0)  # LL
    sprt.add_paired_observation(black_score=0.0, white_score=0.5)  # LD
    assert sprt.llr == 0.0
    assert not sprt.is_finished()


def test_pentanomial_guard_blocks_support_poor_sample_after_pair_floor() -> None:
    sprt = _penta()
    for _ in range(30):
        sprt.add_paired_observation(black_score=1.0, white_score=1.0)
    assert sprt.llr == 0.0
    assert not sprt.is_finished()

    for _ in range(4):
        sprt.add_paired_observation(black_score=0.5, white_score=0.5)
    assert sprt.llr == 0.0
    sprt.add_paired_observation(black_score=0.5, white_score=0.5)
    assert sprt.llr != 0.0


def test_pentanomial_observation_buffers_until_pair_completes() -> None:
    sprt = _penta()
    first = sprt.add_game_observation(sfen="s", pair_slot=0, is_tested_black=True, tested_score=1.0)
    assert first.pending_games == 1
    assert first.pending_pairs == 1

    second = sprt.add_game_observation(sfen="s", pair_slot=0, is_tested_black=False, tested_score=1.0)
    assert second.pending_games == 0
    assert second.pending_pairs == 0
    assert sprt.games_played == 2
    assert sum(sprt.to_snapshot()["penta_bins"]) == 1  # one WW pair completed


def test_pentanomial_snapshot_roundtrip_with_pending() -> None:
    sprt = _penta()
    sprt.add_paired_observation(black_score=1.0, white_score=1.0)  # WW
    sprt.add_paired_observation(black_score=0.5, white_score=0.5)  # DD
    sprt.add_game_observation(sfen="s", pair_slot=3, is_tested_black=True, tested_score=1.0)  # unpaired

    snapshot = sprt.to_snapshot()
    assert snapshot["model"] == SPRT_MODEL_GSPRT_PENTANOMIAL
    assert len(snapshot["pending"]) == 1

    restored = Sprt.from_snapshot(snapshot)
    assert restored.to_snapshot()["penta_bins"] == snapshot["penta_bins"]
    assert restored.llr == pytest.approx(sprt.llr)
    # Completing the restored pending half lands its pair.
    restored.add_game_observation(sfen="s", pair_slot=3, is_tested_black=False, tested_score=1.0)
    assert sum(restored.to_snapshot()["penta_bins"]) == 3
    assert restored.to_snapshot()["pending"] == []


def test_pentanomial_min_pairs_survives_snapshot_roundtrip() -> None:
    # Regression: min_pairs must be restored rather than reverting to the default safety floor.
    sprt = Sprt(elo0=0.0, elo1=5.0, model=SPRT_MODEL_GSPRT_PENTANOMIAL, min_pairs=100)
    for _ in range(3):
        sprt.add_paired_observation(black_score=1.0, white_score=1.0)  # 3 pairs < 100 -> no decision
    assert sprt.llr == 0.0
    assert not sprt.is_finished()

    restored = Sprt.from_snapshot(sprt.to_snapshot())
    assert restored.llr == 0.0
    assert not restored.is_finished()  # still gated by min_pairs=100, not reverted to 30


def test_pentanomial_rejects_off_grid_scores() -> None:
    sprt = _penta()
    with pytest.raises(ValueError, match="game score"):
        sprt.add_game_observation(sfen="s", pair_slot=0, is_tested_black=True, tested_score=0.25)
    with pytest.raises(ValueError, match="game score"):
        sprt.add_paired_observation(black_score=float("nan"), white_score=1.0)


def test_mode_guards_reject_cross_model_ingestion() -> None:
    trinomial = Sprt(elo0=0.0, elo1=5.0)
    with pytest.raises(ValueError, match="pentanomial model"):
        trinomial.add_game_observation(sfen="s", pair_slot=0, is_tested_black=True, tested_score=1.0)
    with pytest.raises(ValueError, match="pentanomial model"):
        trinomial.add_paired_observation(black_score=1.0, white_score=1.0)

    pentanomial = _penta()
    with pytest.raises(ValueError, match="trinomial model"):
        pentanomial.add_game_result(GameResult.WHITE_WIN)


@pytest.mark.parametrize("result", [GameResult.ERROR, GameResult.INVALID, GameResult.PAUSED])
def test_add_game_result_rejects_non_decisive_outcomes(result: GameResult) -> None:
    # The trinomial input contract is strict: non-game outcomes must be normalized/skipped by the
    # caller, not silently folded into draws.
    sprt = Sprt(elo0=0.0, elo1=5.0)
    with pytest.raises(ValueError, match="decisive or draw"):
        sprt.add_game_result(result)
    assert sprt.games_played == 0
