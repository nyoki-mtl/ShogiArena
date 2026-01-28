from __future__ import annotations

from shogiarena.arena.configs.tournament import ArenaConfig
from shogiarena.arena.instances.pool import InstancePool
from shogiarena.arena.runners.tournament_runner import TournamentRunner


class SprtRunner(TournamentRunner):
    """SPRT専用のトーナメントランナー。"""

    def __init__(
        self,
        config: ArenaConfig,
        *,
        instance_pool: InstancePool | None = None,
        overwrite: bool = False,
    ) -> None:
        if config.sprt is None:
            raise ValueError("SprtRunner requires sprt configuration")
        if len(config.engines) != 2:
            raise ValueError("SprtRunner requires exactly two engines")
        super().__init__(
            config,
            overwrite=overwrite,
            instance_pool=instance_pool,
        )
