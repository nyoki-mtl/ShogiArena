from __future__ import annotations

from shogiarena.arena.configs.tournament import TournamentRunConfig
from shogiarena.arena.instances.pool import InstancePool
from shogiarena.arena.runners.reporting import ProgressReporter
from shogiarena.arena.runners.tournament_runner import TournamentRunner
from shogiarena.arena.storage import RunStorage


class SprtRunner(TournamentRunner):
    """SPRT専用のトーナメントランナー。"""

    def __init__(
        self,
        config: TournamentRunConfig,
        *,
        instance_pool: InstancePool | None = None,
        storage: RunStorage,
        progress_reporter: ProgressReporter | None = None,
        dashboard_enabled: bool | None = None,
        no_resume: bool = False,
    ) -> None:
        if config.sprt is None:
            raise ValueError("SprtRunner requires sprt configuration")
        if len(config.engines) != 2:
            raise ValueError("SprtRunner requires exactly two engines")
        super().__init__(
            config,
            no_resume=no_resume,
            instance_pool=instance_pool,
            storage=storage,
            progress_reporter=progress_reporter,
            dashboard_enabled=dashboard_enabled,
        )
