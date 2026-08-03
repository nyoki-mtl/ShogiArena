"""Runtime adapter for SPSA application."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from pathlib import Path

from shogiarena._core.contexts.game_session.adapters.orchestration.config_spsa_models import SpsaRunConfig
from shogiarena._core.contexts.game_session.adapters.orchestration.config_spsa_parser import parse_spsa_config_mapping
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
from shogiarena._core.contexts.spsa.adapters.fixed_option_preflight import (
    run_yaneuraou_fixed_option_preflight,
)
from shogiarena._core.contexts.spsa.adapters.runner import SpsaRunner
from shogiarena._core.contexts.spsa.application.space_spec import (
    inspect_spsa_manifest_request,
    load_spsa_space_spec,
)
from shogiarena._core.contexts.spsa.ports.dashboard_factory import DashboardSpsaServicesFactory
from shogiarena._core.contexts.spsa.ports.spsa_runtime_port import SpsaRunConfigBuildRequest
from shogiarena._core.platform.settings import project_dirs

LOGGER = logging.getLogger("shogiarena.spsa.runtime_adapter")


class SpsaRuntimeAdapter:
    """Adapter delegating SPSA config/session runtime to the runner."""

    def __init__(
        self,
        *,
        engine_factory_service: EngineFactoryService,
        init_dashboard_html: InitDashboardHtmlFn,
        api_server_factory: DashboardApiServerFactory,
        dashboard_service_factory: DashboardSpsaServicesFactory,
    ) -> None:
        self._engine_factory_service = engine_factory_service
        self._init_dashboard_html = init_dashboard_html
        self._api_server_factory = api_server_factory
        self._dashboard_service_factory = dashboard_service_factory

    def build_run_config(
        self,
        payload: Mapping[str, object],
        *,
        request: SpsaRunConfigBuildRequest,
    ) -> SpsaRunConfig:
        normalized_payload = normalize_runtime_payload(payload)
        return parse_spsa_config_mapping(normalized_payload, source_path=request.source_path)

    async def run_session(
        self,
        config: SpsaRunConfig,
        *,
        storage: RunStoragePort,
        should_skip_resume: bool,
        instance_pool: object | None,
    ) -> None:
        runner = SpsaRunner(
            config,
            instance_pool=validate_instance_pool(instance_pool),
            storage=storage,
            should_skip_resume=should_skip_resume,
            engine_factory_service=self._engine_factory_service,
            init_dashboard_html=self._init_dashboard_html,
            api_server_factory=self._api_server_factory,
            dashboard_service_factory=self._dashboard_service_factory,
        )
        await runner.run()

    def create_run_storage(self, run_dir: Path) -> RunStoragePort:
        return FilesystemRunStorage(run_dir)

    def preflight_dry_run(
        self,
        config: SpsaRunConfig,
        *,
        work_dir: Path,
        instance_pool: object | None,
    ) -> None:
        """Scan resolved local fixed options in an isolated directory."""

        manifest_request = inspect_spsa_manifest_request(config.space_path)
        if manifest_request.has_selection and not manifest_request.has_explicit_parameters:
            # Selection-only spaces are normalized against the live engine manifest
            # during the authoritative run preflight.
            LOGGER.info("SPSA dry-run: tunable manifest selection is deferred to execution preflight")
            return
        params = load_spsa_space_spec(config.space_path).to_param_entries()
        if not params:
            raise ValueError("SPSA space spec has no parameters")
        run_yaneuraou_fixed_option_preflight(
            engines=config.tuned,
            target_option_names=(entry.engine_option_name for entry in params if not entry.is_not_used),
            run_dir=work_dir,
            output_dir=project_dirs.output_dir,
            engine_dir=project_dirs.engine_dir,
            instance_pool=validate_instance_pool(instance_pool),
        )

    def engine_trace_logger_names(self) -> tuple[str, ...]:
        return ("shogiarena",)


__all__ = ["SpsaRuntimeAdapter"]
