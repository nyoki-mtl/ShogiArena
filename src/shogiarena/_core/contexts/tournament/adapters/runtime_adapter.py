"""Runtime adapter for tournament application."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from shogiarena._core.contexts.game_session.adapters.orchestration.config_tournament import TournamentRunConfig
from shogiarena._core.contexts.game_session.adapters.run_storage import FilesystemRunStorage
from shogiarena._core.contexts.game_session.adapters.runtime_adapter_support import (
    normalize_runtime_payload,
    validate_instance_pool,
)
from shogiarena._core.contexts.game_session.ports.dashboard_lifecycle_factory import (
    DashboardApiServerFactory,
    InitDashboardHtmlFn,
)
from shogiarena._core.contexts.game_session.ports.run_storage import RunStoragePort
from shogiarena._core.contexts.instances.ports.engine_factory import EngineFactoryService
from shogiarena._core.contexts.tournament.adapters.runner import TournamentRunner
from shogiarena._core.contexts.tournament.ports.tournament_runtime_port import TournamentRunConfigBuildRequest


class TournamentRuntimeAdapter:
    """Adapter delegating tournament config/session runtime to the runner."""

    def __init__(
        self,
        *,
        engine_factory_service: EngineFactoryService,
        init_dashboard_html: InitDashboardHtmlFn,
        api_server_factory: DashboardApiServerFactory,
    ) -> None:
        self._engine_factory_service = engine_factory_service
        self._init_dashboard_html = init_dashboard_html
        self._api_server_factory = api_server_factory

    def build_run_config(
        self,
        payload: Mapping[str, object],
        *,
        request: TournamentRunConfigBuildRequest,
    ) -> TournamentRunConfig:
        normalized_payload = normalize_runtime_payload(payload)
        return TournamentRunConfig.from_mapping(
            normalized_payload,
            base_dir=request.base_dir,
            source_path=request.source_path,
        )

    async def run_session(
        self,
        config: TournamentRunConfig,
        *,
        storage: RunStoragePort,
        should_skip_resume: bool,
        instance_pool: object | None,
    ) -> None:
        runner = TournamentRunner(
            config,
            instance_pool=validate_instance_pool(instance_pool),
            storage=storage,
            should_skip_resume=should_skip_resume,
            engine_factory_service=self._engine_factory_service,
            init_dashboard_html=self._init_dashboard_html,
            api_server_factory=self._api_server_factory,
        )
        await runner.run()

    def create_run_storage(self, run_dir: Path) -> RunStoragePort:
        return FilesystemRunStorage(run_dir)


__all__ = ["TournamentRuntimeAdapter"]
