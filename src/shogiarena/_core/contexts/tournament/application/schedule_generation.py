"""Tournament schedule generation primitives."""

from __future__ import annotations

import logging
import math
import random
from abc import ABC, abstractmethod
from collections.abc import Sequence
from itertools import combinations
from typing import Protocol

from shogiarena._core.contexts.tournament.domain.tournament_models import GameSpec
from shogiarena._core.contexts.tournament.ports.session_state_runtime import ScheduleSeed
from shogiarena._core.shared.kernel.initial_position_entry import InitialPositionEntry
from shogiarena._core.shared.kernel.schedule_color_policy import COLOR_POLICY_VERSION

logger = logging.getLogger(__name__)

__all__ = ["COLOR_POLICY_VERSION"]


class EngineSpecPort(Protocol):
    @property
    def name(self) -> str | None: ...

    @property
    def instance_id(self) -> str | None: ...


class InitialPositionSource(Protocol):
    @property
    def flip_policy(self) -> str: ...

    def generate(self, count: int, seed: str, /) -> list[str]: ...


def _require_engine_name(spec: EngineSpecPort) -> str:
    if not spec.name:
        raise ValueError("Engine name must be set before scheduling")
    return str(spec.name)


def _first_takes_black(seed_str: str, name_a: str, name_b: str, salt: str) -> bool:
    """Deterministic fair coin: whether ``name_a`` (the earlier-listed engine) takes black.

    Used for the single odd/extra game of a pair and to seed the starting colour of an alternating
    pair, so neither is biased toward the earlier-listed engine. Both schedulers share this so the
    same matchup gets the same colour assignment.
    """
    return random.Random(f"{seed_str}-{name_a}-{name_b}-{salt}").random() < 0.5


class _PositionCursor:
    def __init__(self, positions: list[InitialPositionEntry]) -> None:
        self._positions = positions
        self._index = 0

    def next(self) -> InitialPositionEntry:
        try:
            value = self._positions[self._index]
        except IndexError as exc:
            raise RuntimeError(
                f"Initial positions exhausted: have {len(self._positions)}, need at least {self._index + 1}",
            ) from exc
        self._index += 1
        return value


def _generate_position_entries(source: InitialPositionSource, count: int, seed: str) -> list[InitialPositionEntry]:
    generate_entries = getattr(source, "generate_entries", None)
    if callable(generate_entries):
        raw_entries = generate_entries(count, seed)
        entries: list[InitialPositionEntry] = []
        for item in raw_entries:
            if isinstance(item, InitialPositionEntry):
                entries.append(item)
            else:
                entries.append(InitialPositionEntry(initial_sfen=str(item)))
        return entries
    return [InitialPositionEntry(initial_sfen=sfen) for sfen in source.generate(count, seed)]


def _matchup_key(engine_a: str, engine_b: str) -> str:
    return "|".join(sorted((engine_a, engine_b)))


def _create_game_spec(
    *,
    black: str,
    white: str,
    entry: InitialPositionEntry,
    round_num: int,
    seed: str,
    pair_slot: int | None = None,
    pair_index: int | None = None,
    matchup_key: str | None = None,
) -> GameSpec:
    spec = GameSpec.create(
        black=black,
        white=white,
        sfen=entry.initial_sfen,
        round_num=round_num,
        seed=seed,
    )
    if pair_slot is not None:
        effective_matchup = matchup_key or _matchup_key(black, white)
        spec.matchup_key = effective_matchup
        spec.pair_slot = pair_slot
        spec.pair_index = pair_index if pair_index is not None else pair_slot
        spec.pair_key = f"{effective_matchup}|slot-{pair_slot}"
    spec.opening_line_id = entry.line_id
    spec.opening_line_moves_usi = entry.line_moves_usi
    spec.opening_source = entry.source_path
    spec.opening_source_line_no = entry.source_line_no
    return spec


class GameScheduler(ABC):
    @abstractmethod
    def generate_schedule(
        self,
        engines: Sequence[EngineSpecPort],
        games_per_pair: int,
        seed: ScheduleSeed,
        initial_positions: InitialPositionSource,
    ) -> list[GameSpec]:
        raise NotImplementedError

    @abstractmethod
    def get_total_games(self, num_engines: int, games_per_pair: int) -> int:
        raise NotImplementedError


class SelfPlayScheduler(GameScheduler):
    def generate_schedule(
        self,
        engines: Sequence[EngineSpecPort],
        games_per_pair: int,
        seed: ScheduleSeed,
        initial_positions: InitialPositionSource,
    ) -> list[GameSpec]:
        if len(engines) != 1:
            raise ValueError("Selfplay requires exactly 1 engine")
        if games_per_pair <= 0:
            return []

        seed_str = str(seed)
        engine_name = _require_engine_name(engines[0])
        pair_both = initial_positions.flip_policy == "pair_both"
        positions_needed = math.ceil(games_per_pair / 2) if pair_both else games_per_pair
        positions = _generate_position_entries(initial_positions, positions_needed, seed_str)

        games: list[GameSpec] = []
        position_cursor = _PositionCursor(positions)
        round_num = 0

        if pair_both:
            remaining = games_per_pair
            while remaining > 0:
                entry = position_cursor.next()
                pair_slot = round_num // 2
                matchup_key = _matchup_key(engine_name, engine_name)
                games.append(
                    _create_game_spec(
                        black=engine_name,
                        white=engine_name,
                        entry=entry,
                        round_num=round_num,
                        seed=seed_str,
                        pair_slot=pair_slot,
                        matchup_key=matchup_key,
                    ),
                )
                round_num += 1
                remaining -= 1
                if remaining > 0:
                    games.append(
                        _create_game_spec(
                            black=engine_name,
                            white=engine_name,
                            entry=entry,
                            round_num=round_num,
                            seed=seed_str,
                            pair_slot=pair_slot,
                            matchup_key=matchup_key,
                        ),
                    )
                    round_num += 1
                    remaining -= 1
        else:
            for _ in range(games_per_pair):
                entry = position_cursor.next()
                games.append(
                    _create_game_spec(
                        black=engine_name,
                        white=engine_name,
                        entry=entry,
                        round_num=round_num,
                        seed=seed_str,
                    ),
                )
                round_num += 1

        return games

    def get_total_games(self, num_engines: int, games_per_pair: int) -> int:
        if num_engines != 1:
            return 0
        return max(0, games_per_pair)


class RoundRobinScheduler(GameScheduler):
    def generate_schedule(
        self,
        engines: Sequence[EngineSpecPort],
        games_per_pair: int,
        seed: ScheduleSeed,
        initial_positions: InitialPositionSource,
    ) -> list[GameSpec]:
        if len(engines) < 2:
            raise ValueError("Need at least 2 engines for tournament")

        rng = random.Random(seed)
        seed_str = str(seed)
        num_engines = len(engines)
        pairs_count = num_engines * (num_engines - 1) // 2

        pair_both = initial_positions.flip_policy == "pair_both"
        if pair_both:
            if games_per_pair % 2 == 1:
                logger.warning("flip_policy=pair_both with odd games_per_pair; one SFEN per pair will be single-sided")
            positions_needed = pairs_count * math.ceil(games_per_pair / 2)
        else:
            positions_needed = self.get_total_games(num_engines, games_per_pair)
        positions = _generate_position_entries(initial_positions, positions_needed, seed_str)

        games: list[GameSpec] = []
        position_cursor = _PositionCursor(positions)
        round_num = 0

        for engine_a_spec, engine_b_spec in combinations(engines, 2):
            engine_a_name = _require_engine_name(engine_a_spec)
            engine_b_name = _require_engine_name(engine_b_spec)

            if pair_both:
                games_remaining = games_per_pair
                while games_remaining >= 2:
                    entry = position_cursor.next()
                    pair_slot = round_num // 2
                    matchup_key = _matchup_key(engine_a_name, engine_b_name)
                    games.append(
                        _create_game_spec(
                            black=engine_a_name,
                            white=engine_b_name,
                            entry=entry,
                            round_num=round_num,
                            seed=seed_str,
                            pair_slot=pair_slot,
                            matchup_key=matchup_key,
                        ),
                    )
                    round_num += 1
                    games.append(
                        _create_game_spec(
                            black=engine_b_name,
                            white=engine_a_name,
                            entry=entry,
                            round_num=round_num,
                            seed=seed_str,
                            pair_slot=pair_slot,
                            matchup_key=matchup_key,
                        ),
                    )
                    round_num += 1
                    games_remaining -= 2
                if games_remaining == 1:
                    # The single odd game gets a seeded fair colour (matching the gauntlet
                    # scheduler) instead of always handing black to the earlier-listed engine.
                    entry = position_cursor.next()
                    a_black = _first_takes_black(seed_str, engine_a_name, engine_b_name, "odd")
                    black_name, white_name = (
                        (engine_a_name, engine_b_name) if a_black else (engine_b_name, engine_a_name)
                    )
                    pair_slot = round_num // 2
                    games.append(
                        _create_game_spec(
                            black=black_name,
                            white=white_name,
                            entry=entry,
                            round_num=round_num,
                            seed=seed_str,
                            pair_slot=pair_slot,
                            matchup_key=_matchup_key(engine_a_name, engine_b_name),
                        ),
                    )
                    round_num += 1
                continue

            # Seed the starting colour of an alternating pair so an odd games_per_pair does not
            # always give the extra black game to the earlier-listed engine.
            alt_start_a_black = _first_takes_black(seed_str, engine_a_name, engine_b_name, "alt-start")
            for game_num in range(games_per_pair):
                if initial_positions.flip_policy == "alternate":
                    a_black = (game_num % 2 == 0) == alt_start_a_black
                    black_name, white_name = (
                        (engine_a_name, engine_b_name) if a_black else (engine_b_name, engine_a_name)
                    )
                elif initial_positions.flip_policy == "random":
                    black_name, white_name = (
                        (engine_a_name, engine_b_name) if rng.random() < 0.5 else (engine_b_name, engine_a_name)
                    )
                else:
                    black_name, white_name = engine_a_name, engine_b_name

                entry = position_cursor.next()
                games.append(
                    _create_game_spec(
                        black=black_name,
                        white=white_name,
                        entry=entry,
                        round_num=round_num,
                        seed=seed_str,
                    ),
                )
                round_num += 1

        return games

    def get_total_games(self, num_engines: int, games_per_pair: int) -> int:
        if num_engines < 2:
            return 0
        pairs = num_engines * (num_engines - 1) // 2
        return pairs * games_per_pair


class GauntletScheduler(GameScheduler):
    def __init__(self, baseline_count: int = 1) -> None:
        if baseline_count < 1:
            raise ValueError("baseline_count must be >= 1 for gauntlet")
        self.baseline_count = baseline_count

    def generate_schedule(
        self,
        engines: Sequence[EngineSpecPort],
        games_per_pair: int,
        seed: ScheduleSeed,
        initial_positions: InitialPositionSource,
    ) -> list[GameSpec]:
        if len(engines) < 2:
            raise ValueError("Need at least 2 engines for gauntlet")

        num_engines = len(engines)
        baseline_slots = min(max(1, self.baseline_count), num_engines - 1)
        baseline_engines = engines[:baseline_slots]
        challenger_engines = engines[baseline_slots:]
        seed_str = str(seed)

        pair_both = initial_positions.flip_policy == "pair_both"
        if pair_both:
            pairs_count = max(1, len(baseline_engines) * len(challenger_engines))
            positions_needed = pairs_count * math.ceil(games_per_pair / 2)
        else:
            positions_needed = self.get_total_games(num_engines, games_per_pair)
        positions = _generate_position_entries(initial_positions, positions_needed, seed_str)

        games: list[GameSpec] = []
        position_cursor = _PositionCursor(positions)
        round_num = 0

        for baseline_engine in baseline_engines:
            if pair_both:
                remaining_games = {
                    _require_engine_name(challenger): games_per_pair for challenger in challenger_engines
                }
                baseline_name = _require_engine_name(baseline_engine)
                while any(v >= 2 for v in remaining_games.values()):
                    for challenger_engine in challenger_engines:
                        challenger_name = _require_engine_name(challenger_engine)
                        if remaining_games[challenger_name] >= 2:
                            entry = position_cursor.next()
                            pair_slot = round_num // 2
                            matchup_key = _matchup_key(baseline_name, challenger_name)
                            games.append(
                                _create_game_spec(
                                    black=baseline_name,
                                    white=challenger_name,
                                    entry=entry,
                                    round_num=round_num,
                                    seed=seed_str,
                                    pair_slot=pair_slot,
                                    matchup_key=matchup_key,
                                ),
                            )
                            round_num += 1
                            games.append(
                                _create_game_spec(
                                    black=challenger_name,
                                    white=baseline_name,
                                    entry=entry,
                                    round_num=round_num,
                                    seed=seed_str,
                                    pair_slot=pair_slot,
                                    matchup_key=matchup_key,
                                ),
                            )
                            round_num += 1
                            remaining_games[challenger_name] -= 2
                for challenger_engine in challenger_engines:
                    challenger_name = _require_engine_name(challenger_engine)
                    if remaining_games[challenger_name] == 1:
                        entry = position_cursor.next()
                        baseline_black = _first_takes_black(seed_str, baseline_name, challenger_name, "odd")
                        black, white = (
                            (baseline_name, challenger_name) if baseline_black else (challenger_name, baseline_name)
                        )
                        pair_slot = round_num // 2
                        games.append(
                            _create_game_spec(
                                black=black,
                                white=white,
                                entry=entry,
                                round_num=round_num,
                                seed=seed_str,
                                pair_slot=pair_slot,
                                matchup_key=_matchup_key(baseline_name, challenger_name),
                            ),
                        )
                        round_num += 1
            else:
                baseline_name = _require_engine_name(baseline_engine)
                for game_num in range(games_per_pair):
                    for challenger_engine in challenger_engines:
                        challenger_name = _require_engine_name(challenger_engine)
                        if initial_positions.flip_policy == "alternate":
                            start_baseline_black = _first_takes_black(
                                seed_str, baseline_name, challenger_name, "alt-start"
                            )
                            baseline_black = (game_num % 2 == 0) == start_baseline_black
                            black, white = (
                                (baseline_name, challenger_name) if baseline_black else (challenger_name, baseline_name)
                            )
                        elif initial_positions.flip_policy == "random":
                            rng = random.Random(f"{seed_str}-{baseline_name}-{challenger_name}-{game_num}")
                            black, white = (
                                (baseline_name, challenger_name)
                                if rng.random() < 0.5
                                else (challenger_name, baseline_name)
                            )
                        else:
                            black, white = (baseline_name, challenger_name)
                        entry = position_cursor.next()
                        games.append(
                            _create_game_spec(
                                black=black,
                                white=white,
                                entry=entry,
                                round_num=round_num,
                                seed=seed_str,
                            ),
                        )
                        round_num += 1

        return games

    def get_total_games(self, num_engines: int, games_per_pair: int) -> int:
        if num_engines < 2:
            return 0
        baseline_slots = min(max(1, self.baseline_count), num_engines - 1)
        pairs = baseline_slots * (num_engines - baseline_slots)
        return pairs * games_per_pair


def create_scheduler(scheduler_type: str) -> GameScheduler:
    schedulers: dict[str, type[GameScheduler]] = {
        "selfplay": SelfPlayScheduler,
        "round_robin": RoundRobinScheduler,
        "gauntlet": GauntletScheduler,
    }

    if scheduler_type not in schedulers:
        raise ValueError(f"Unknown scheduler type: {scheduler_type}. Available: {list(schedulers.keys())}")

    if scheduler_type == "gauntlet":
        return GauntletScheduler(baseline_count=1)
    return schedulers[scheduler_type]()


__all__ = [
    "EngineSpecPort",
    "InitialPositionSource",
    "GameScheduler",
    "GauntletScheduler",
    "create_scheduler",
]
