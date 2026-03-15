from __future__ import annotations

from shogiarena._core.contexts.tournament.application.schedule_ordering_service import (
    reorder_and_shuffle,
)
from shogiarena._core.contexts.tournament.domain.tournament_models import GameSpec


def _spec(game_id: str, black: str, white: str) -> GameSpec:
    return GameSpec(
        black_engine=black,
        white_engine=white,
        initial_sfen="startpos",
        game_id=game_id,
    )


def test_interleave_reorders_by_pair_rounds() -> None:
    games = [
        _spec("ab-1", "A", "B"),
        _spec("ab-2", "A", "B"),
        _spec("ac-1", "A", "C"),
        _spec("ac-2", "A", "C"),
        _spec("bc-1", "B", "C"),
        _spec("bc-2", "B", "C"),
    ]

    ordered = reorder_and_shuffle(
        games,
        game_order="interleave",
        flip_policy="pair_both",
        shuffle_seed=None,
    )

    assert [spec.game_id for spec in ordered] == ["ab-1", "ac-1", "bc-1", "ab-2", "ac-2", "bc-2"]


def test_auto_pair_both_keeps_pairwise_order() -> None:
    games = [
        _spec("ab-1", "A", "B"),
        _spec("ab-2", "A", "B"),
        _spec("ac-1", "A", "C"),
    ]

    ordered = reorder_and_shuffle(
        games,
        game_order="auto",
        flip_policy="pair_both",
        shuffle_seed="seed",
    )

    assert [spec.game_id for spec in ordered] == ["ab-1", "ab-2", "ac-1"]


def test_auto_non_pair_both_resolves_to_interleave() -> None:
    games = [
        _spec("ab-1", "A", "B"),
        _spec("ab-2", "A", "B"),
        _spec("ac-1", "A", "C"),
        _spec("ac-2", "A", "C"),
    ]

    ordered = reorder_and_shuffle(
        games,
        game_order="auto",
        flip_policy="random",
        shuffle_seed=None,
    )

    assert [spec.game_id for spec in ordered] == ["ab-1", "ac-1", "ab-2", "ac-2"]


def test_shuffle_is_deterministic_for_seed() -> None:
    games = [_spec(f"g-{idx}", "A", "B") for idx in range(6)]

    first = reorder_and_shuffle(
        games,
        game_order="shuffle",
        flip_policy="pair_both",
        shuffle_seed="fixed-seed",
    )
    second = reorder_and_shuffle(
        games,
        game_order="shuffle",
        flip_policy="pair_both",
        shuffle_seed="fixed-seed",
    )

    assert [spec.game_id for spec in first] == [spec.game_id for spec in second]
    assert [spec.game_id for spec in first] != [spec.game_id for spec in games]
