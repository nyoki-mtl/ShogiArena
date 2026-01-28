"""Implementations of tournament game schedulers."""

import logging
import math
import random
from abc import ABC, abstractmethod
from itertools import combinations

from shogiarena.arena.configs.tournament import EngineSpec, GameSpec, InitialPositions

SeedLike = int | float | str | bytes | bytearray | None

logger = logging.getLogger(__name__)


def _require_engine_name(spec: EngineSpec) -> str:
    """Return the engine name or raise when unset."""

    if not spec.name:
        raise ValueError("Engine name must be set before scheduling")
    return str(spec.name)


class GameScheduler(ABC):
    """Abstract base class for tournament game scheduling."""

    @abstractmethod
    def generate_schedule(
        self,
        engines: list[EngineSpec],
        games_per_pair: int,
        seed: SeedLike,
        initial_positions: InitialPositions,
    ) -> list[GameSpec]:
        """Generate complete game schedule.

        Args:
            engines: List of engine specifications
            games_per_pair: Games per engine pair
            seed: Random seed for reproducibility
            initial_positions: Initial position configuration

        Returns:
            List of GameSpec for all scheduled games
        """
        raise NotImplementedError

    @abstractmethod
    def get_total_games(self, num_engines: int, games_per_pair: int) -> int:
        """Calculate total number of games.

        Args:
            num_engines: Number of engines
            games_per_pair: Games per engine pair

        Returns:
            Total number of games
        """
        raise NotImplementedError


class RoundRobinScheduler(GameScheduler):
    """Round robin scheduler for N-engine tournaments.

    Every engine plays against every other engine for the specified
    number of games with balanced color assignments.
    """

    def generate_schedule(
        self,
        engines: list[EngineSpec],
        games_per_pair: int,
        seed: SeedLike,
        initial_positions: InitialPositions,
    ) -> list[GameSpec]:
        """Generate round robin game schedule with stable IDs.

        Args:
            engines: List of engine specifications
            games_per_pair: Games between each pair
            seed: Random seed for reproducibility
            initial_positions: Initial position configuration

        Returns:
            Complete game schedule
        """
        if len(engines) < 2:
            raise ValueError("Need at least 2 engines for tournament")

        rng = random.Random(seed)
        seed_str = str(seed)

        num_engines = len(engines)
        pairs_count = num_engines * (num_engines - 1) // 2

        pair_both = initial_positions.flip_policy == "pair_both"
        if pair_both:
            if games_per_pair % 2 == 1:
                logger.warning(
                    "flip_policy=pair_both with odd games_per_pair; one SFEN per pair will be single-sided",
                )
            positions_needed = pairs_count * math.ceil(games_per_pair / 2)
        else:
            positions_needed = self.get_total_games(num_engines, games_per_pair)

        positions = initial_positions.generate(positions_needed, seed_str)

        games: list[GameSpec] = []
        position_idx = 0
        round_num = 0
        total_positions = len(positions)

        def next_position() -> str:
            nonlocal position_idx
            try:
                sfen_value = positions[position_idx]
            except IndexError as exc:
                raise RuntimeError(
                    f"Initial positions exhausted: have {total_positions}, need at least {position_idx + 1}",
                ) from exc
            position_idx += 1
            return sfen_value

        for engine_a_spec, engine_b_spec in combinations(engines, 2):
            engine_a_name = _require_engine_name(engine_a_spec)
            engine_b_name = _require_engine_name(engine_b_spec)

            if pair_both:
                games_remaining = games_per_pair
                while games_remaining > 0:
                    sfen = next_position()

                    games.append(
                        GameSpec.create(
                            black=engine_a_name,
                            white=engine_b_name,
                            sfen=sfen,
                            round_num=round_num,
                            seed=seed_str,
                        ),
                    )
                    round_num += 1
                    games_remaining -= 1

                    if games_remaining > 0:
                        games.append(
                            GameSpec.create(
                                black=engine_b_name,
                                white=engine_a_name,
                                sfen=sfen,
                                round_num=round_num,
                                seed=seed_str,
                            ),
                        )
                        round_num += 1
                        games_remaining -= 1
                continue

            for game_num in range(games_per_pair):
                if initial_positions.flip_policy == "alternate":
                    black_name, white_name = (
                        (engine_a_name, engine_b_name) if game_num % 2 == 0 else (engine_b_name, engine_a_name)
                    )
                elif initial_positions.flip_policy == "random":
                    black_name, white_name = (
                        (engine_a_name, engine_b_name) if rng.random() < 0.5 else (engine_b_name, engine_a_name)
                    )
                else:
                    black_name, white_name = engine_a_name, engine_b_name

                sfen = next_position()

                games.append(
                    GameSpec.create(
                        black=black_name,
                        white=white_name,
                        sfen=sfen,
                        round_num=round_num,
                        seed=seed_str,
                    ),
                )
                round_num += 1

        logger.debug(
            "Generated %s games for %s engines (%s games per pair)",
            len(games),
            len(engines),
            games_per_pair,
        )

        return games

    def get_total_games(self, num_engines: int, games_per_pair: int) -> int:
        """Calculate total games in round robin.

        Formula: C(n,2) * games_per_pair = n*(n-1)/2 * games_per_pair

        Args:
            num_engines: Number of engines
            games_per_pair: Games per engine pair

        Returns:
            Total number of games
        """
        if num_engines < 2:
            return 0

        pairs = num_engines * (num_engines - 1) // 2
        return pairs * games_per_pair


class SwissScheduler(GameScheduler):
    """Swiss system scheduler (future implementation)."""

    def generate_schedule(
        self,
        engines: list[EngineSpec],
        games_per_pair: int,
        seed: SeedLike,
        initial_positions: InitialPositions,
    ) -> list[GameSpec]:
        """Swiss system not yet implemented."""
        raise NotImplementedError("Swiss scheduler not yet implemented")

    def get_total_games(self, num_engines: int, games_per_pair: int) -> int:
        """Swiss game count calculation."""
        if num_engines < 2:
            return 0
        # Swiss typically has log2(n) rounds
        rounds = math.ceil(math.log2(num_engines))
        pairings_per_round = num_engines // 2
        return rounds * pairings_per_round * games_per_pair


class GauntletScheduler(GameScheduler):
    """Gauntlet scheduler: baselines vs the rest.

    - If baseline_count == 1: engines[0] plays against all others
    - If baseline_count > 1: first N engines are baselines; every baseline plays all non-baselines
    """

    def __init__(self, baseline_count: int = 1):
        if baseline_count < 1:
            raise ValueError("baseline_count must be >= 1 for gauntlet")
        self.baseline_count = baseline_count

    def generate_schedule(
        self,
        engines: list[EngineSpec],
        games_per_pair: int,
        seed: SeedLike,
        initial_positions: InitialPositions,
    ) -> list[GameSpec]:
        if len(engines) < 2:
            raise ValueError("Need at least 2 engines for gauntlet")

        num_engines = len(engines)
        baseline_slots = min(max(1, self.baseline_count), num_engines - 1)
        baseline_engines = engines[:baseline_slots]
        challenger_engines = engines[baseline_slots:]
        seed_str = str(seed)

        # Positions
        pair_both = initial_positions.flip_policy == "pair_both"
        # Estimate positions conservatively
        if pair_both:
            pairs_count = max(1, len(baseline_engines) * len(challenger_engines))
            positions_needed = pairs_count * math.ceil(games_per_pair / 2)
        else:
            positions_needed = self.get_total_games(num_engines, games_per_pair)
        positions = initial_positions.generate(positions_needed, seed_str)

        games: list[GameSpec] = []
        position_idx = 0
        round_num = 0
        total_positions = len(positions)

        def next_position() -> str:
            nonlocal position_idx
            try:
                sfen_value = positions[position_idx]
            except IndexError as exc:
                raise RuntimeError(
                    f"Initial positions exhausted: have {total_positions}, need at least {position_idx + 1}",
                ) from exc
            position_idx += 1
            return sfen_value

        for baseline_engine in baseline_engines:
            if pair_both:
                # Interleave 2-game mini-series across opponents: AB(表裏) -> AC(表裏) -> ... and repeat
                remaining_games = {
                    _require_engine_name(challenger): games_per_pair for challenger in challenger_engines
                }
                baseline_name = _require_engine_name(baseline_engine)
                while any(v >= 2 for v in remaining_games.values()):
                    for challenger_engine in challenger_engines:
                        challenger_name = _require_engine_name(challenger_engine)
                        if remaining_games[challenger_name] >= 2:
                            sfen = next_position()
                            games.append(
                                GameSpec.create(
                                    black=baseline_name,
                                    white=challenger_name,
                                    sfen=sfen,
                                    round_num=round_num,
                                    seed=seed_str,
                                ),
                            )
                            round_num += 1
                            games.append(
                                GameSpec.create(
                                    black=challenger_name,
                                    white=baseline_name,
                                    sfen=sfen,
                                    round_num=round_num,
                                    seed=seed_str,
                                ),
                            )
                            round_num += 1
                            remaining_games[challenger_name] -= 2
                # Leftover single games when odd
                for challenger_engine in challenger_engines:
                    challenger_name = _require_engine_name(challenger_engine)
                    if remaining_games[challenger_name] == 1:
                        sfen = next_position()
                        # Deterministic color choice
                        rng = random.Random(f"{seed_str}-{baseline_name}-{challenger_name}-odd")
                        if rng.random() < 0.5:
                            games.append(
                                GameSpec.create(
                                    black=baseline_name,
                                    white=challenger_name,
                                    sfen=sfen,
                                    round_num=round_num,
                                    seed=seed_str,
                                ),
                            )
                        else:
                            games.append(
                                GameSpec.create(
                                    black=challenger_name,
                                    white=baseline_name,
                                    sfen=sfen,
                                    round_num=round_num,
                                    seed=seed_str,
                                ),
                            )
                        round_num += 1
            else:
                # Non-pair_both: interleave single games across opponents per round
                baseline_name = _require_engine_name(baseline_engine)
                for game_num in range(games_per_pair):
                    for challenger_engine in challenger_engines:
                        challenger_name = _require_engine_name(challenger_engine)
                        if initial_positions.flip_policy == "alternate":
                            black, white = (
                                (baseline_name, challenger_name)
                                if (game_num % 2 == 0)
                                else (challenger_name, baseline_name)
                            )
                        elif initial_positions.flip_policy == "random":
                            rng = random.Random(
                                f"{seed_str}-{baseline_name}-{challenger_name}-{game_num}",
                            )
                            black, white = (
                                (baseline_name, challenger_name)
                                if rng.random() < 0.5
                                else (challenger_name, baseline_name)
                            )
                        else:
                            black, white = (baseline_name, challenger_name)
                        sfen = next_position()
                        games.append(
                            GameSpec.create(
                                black=black,
                                white=white,
                                sfen=sfen,
                                round_num=round_num,
                                seed=seed_str,
                            ),
                        )
                        round_num += 1

        return games

    def get_total_games(self, num_engines: int, games_per_pair: int) -> int:
        if num_engines < 2:
            return 0
        # baselines vs others pairs count = baseline_count * (n - baseline_count)
        baseline_slots = min(max(1, self.baseline_count), num_engines - 1)
        pairs = baseline_slots * (num_engines - baseline_slots)
        return pairs * games_per_pair


def create_scheduler(scheduler_type: str) -> GameScheduler:
    """Factory to create scheduler instances.

    Args:
        scheduler_type: Type of scheduler (round_robin, swiss, etc.)

    Returns:
        GameScheduler instance

    Raises:
        ValueError: Unknown scheduler type
    """
    schedulers: dict[str, type[GameScheduler]] = {
        "round_robin": RoundRobinScheduler,
        "gauntlet": GauntletScheduler,  # constructed below with default baseline_count
    }

    if scheduler_type not in schedulers:
        if scheduler_type == "swiss":
            raise NotImplementedError("Swiss scheduler is not implemented yet")
        raise ValueError(f"Unknown scheduler type: {scheduler_type}. Available: {list(schedulers.keys())}")

    # Gauntlet default baseline_count=1 when constructed here.
    if scheduler_type == "gauntlet":
        return GauntletScheduler(baseline_count=1)
    return schedulers[scheduler_type]()
