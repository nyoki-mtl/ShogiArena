"""C6 regression tests: colour assignment is seeded-fair, not biased toward list order."""

from __future__ import annotations

from shogiarena._core.contexts.tournament.application.schedule_generation import (
    GauntletScheduler,
    RoundRobinScheduler,
)
from shogiarena._core.contexts.tournament.domain.tournament_models import GameSpec


class _Engine:
    def __init__(self, name: str) -> None:
        self.name = name
        self.instance_id = None


class _PairBothPositions:
    flip_policy = "pair_both"

    def generate(self, count: int, seed: str) -> list[str]:
        return ["startpos"] * count


def _engines(n: int) -> list[_Engine]:
    return [_Engine(f"e{i:02d}") for i in range(n)]


def _earlier_listed_is_black(game: GameSpec, order: list[str]) -> bool:
    return order.index(game.black_engine) < order.index(game.white_engine)


def test_round_robin_pair_both_odd_colour_is_seeded_not_list_order() -> None:
    order = [f"e{i:02d}" for i in range(12)]
    games = RoundRobinScheduler().generate_schedule(_engines(12), 1, "7", _PairBothPositions())  # type: ignore[arg-type]

    assert len(games) == 66  # C(12, 2), one odd game per pair
    earlier_black = sum(1 for game in games if _earlier_listed_is_black(game, order))
    # The old bug always handed black to the earlier-listed engine (66/66); a seeded fair coin
    # spreads it across both engines.
    assert 0 < earlier_black < 66
    assert 0.25 < earlier_black / 66 < 0.75


def test_schedule_generation_is_deterministic_for_a_seed() -> None:
    first = RoundRobinScheduler().generate_schedule(_engines(6), 3, "7", _PairBothPositions())  # type: ignore[arg-type]
    second = RoundRobinScheduler().generate_schedule(_engines(6), 3, "7", _PairBothPositions())  # type: ignore[arg-type]
    assert [(g.black_engine, g.white_engine, g.round_num) for g in first] == [
        (g.black_engine, g.white_engine, g.round_num) for g in second
    ]


def test_round_robin_and_gauntlet_agree_on_the_odd_game_colour() -> None:
    # Both schedulers share the same seeded coin, so the same matchup gets the same colour.
    engines = _engines(2)
    round_robin = RoundRobinScheduler().generate_schedule(engines, 1, "7", _PairBothPositions())  # type: ignore[arg-type]
    gauntlet = GauntletScheduler(baseline_count=1).generate_schedule(engines, 1, "7", _PairBothPositions())  # type: ignore[arg-type]

    assert len(round_robin) == 1
    assert len(gauntlet) == 1
    assert (round_robin[0].black_engine, round_robin[0].white_engine) == (
        gauntlet[0].black_engine,
        gauntlet[0].white_engine,
    )


def test_pair_both_even_games_are_colour_balanced_per_pair() -> None:
    # An even games_per_pair must give each engine black exactly half the time within a pair.
    games = RoundRobinScheduler().generate_schedule(_engines(2), 4, "7", _PairBothPositions())  # type: ignore[arg-type]
    black_counts = {"e00": 0, "e01": 0}
    for game in games:
        black_counts[game.black_engine] += 1
    assert black_counts == {"e00": 2, "e01": 2}


def test_color_policy_version_is_recorded_in_tournament_schedule_payload() -> None:
    from shogiarena._core.shared.kernel.run_artifact_contract import build_schedule_payload
    from shogiarena._core.shared.kernel.schedule_color_policy import COLOR_POLICY_VERSION

    payload = build_schedule_payload(
        {"tournament": {"format": "round_robin", "games_per_pair": 2}, "engines": [{"name": "a"}, {"name": "b"}]}
    )
    tournament = payload["tournament"]
    assert isinstance(tournament, dict)
    assert tournament["color_policy_version"] == COLOR_POLICY_VERSION


def test_generate_schedule_payload_has_no_color_policy_version() -> None:
    from shogiarena._core.shared.kernel.run_artifact_contract import build_schedule_payload

    payload = build_schedule_payload({"generate": {"games": 4}, "engines": [{"name": "a"}]})
    assert "tournament" not in payload  # generate runs carry no colour policy
