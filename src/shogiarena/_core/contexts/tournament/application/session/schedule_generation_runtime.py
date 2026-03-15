"""Runtime schedule generation bridging scheduler and context.

Separated from mutation logic so that generation is independently
testable and clearly owned.
"""

from __future__ import annotations

from shogiarena._core.contexts.tournament.application.schedule_ordering_service import reorder_and_shuffle
from shogiarena._core.contexts.tournament.application.session.schedule_context import TournamentScheduleContext
from shogiarena._core.contexts.tournament.domain.tournament_models import GameSpec
from shogiarena._core.contexts.tournament.ports.session_state_runtime import normalize_schedule_seed


def generate_schedule_for_seed(ctx: TournamentScheduleContext, *, seed: str | None) -> list[GameSpec]:
    """Generate a full schedule using the provided seed without mutating state."""

    seed_str = normalize_schedule_seed(seed if seed is not None else ctx.config.tournament.seed)
    games = ctx.scheduler.generate_schedule(
        engines=ctx.config.engines,
        games_per_pair=ctx.config.tournament.games_per_pair,
        seed=seed_str,
        initial_positions=ctx.config.rules.initial_positions,
    )
    shuffle_seed = seed_str if seed is not None else ctx.config.tournament.seed
    return reorder_and_shuffle(
        games,
        game_order=ctx.config.tournament.game_order,
        flip_policy=ctx.config.rules.initial_positions.flip_policy,
        shuffle_seed=shuffle_seed,
    )


__all__ = ["generate_schedule_for_seed"]
