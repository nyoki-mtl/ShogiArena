"""Public tournament runtime API."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from shogiarena._core.contexts.game_session.adapters.orchestration.config_tournament import TournamentRunConfig
from shogiarena._core.contexts.game_session.adapters.results.run_result_models import TournamentRunResult
from shogiarena._core.contexts.game_session.adapters.run_storage import FilesystemRunStorage
from shogiarena._core.contexts.game_session.application.sprt_service import SprtDecision, SprtResult
from shogiarena._core.contexts.game_session.domain.summary_models import EngineWdlCounts, TournamentResults
from shogiarena._core.contexts.game_session.ports.run_storage import RunStoragePort
from shogiarena._core.contexts.game_session.ports.session_lifecycle_ports import ProgressReporterPort
from shogiarena._core.contexts.instances.application.instance_pool import InstancePool
from shogiarena._core.contexts.tournament.adapters.runner import TournamentRunner
from shogiarena._core.contexts.tournament.domain.tournament_models import GameSpec
from shogiarena._core.contexts.tournament.ports.tournament_runtime_port import TournamentRunConfigBuildRequest
from shogiarena._core.interfaces.cli.config_file_loaders import parse_tournament_config_file
from shogiarena._core.interfaces.composition_root.default_root import DefaultRoot, build_default_root
from shogiarena._core.shared.kernel.json_types import (
    JsonValue,  # noqa: F401 - stable result fields are annotated with Mapping[str, JsonValue]
)


def create_run_storage(run_dir: str | Path) -> FilesystemRunStorage:
    """Create filesystem-backed storage for a tournament run directory."""

    return FilesystemRunStorage(Path(run_dir))


def build_tournament_runner(
    config: TournamentRunConfig,
    *,
    storage: RunStoragePort,
    instance_pool: InstancePool | None = None,
    progress_reporter: ProgressReporterPort | None = None,
    is_dashboard_enabled: bool | None = None,
    should_skip_resume: bool = False,
    root: DefaultRoot | None = None,
) -> TournamentRunner:
    """Build a tournament runner using the default ShogiArena wiring."""

    runtime_root = root or build_default_root()
    return TournamentRunner(
        config,
        instance_pool=instance_pool,
        storage=storage,
        engine_factory_service=runtime_root.engine_factory_service,
        init_dashboard_html=runtime_root.init_dashboard_html,
        api_server_factory=runtime_root.api_server_factory,
        progress_reporter=progress_reporter,
        is_dashboard_enabled=is_dashboard_enabled,
        should_skip_resume=should_skip_resume,
    )


def load_tournament_config(
    config_source: TournamentRunConfig | Mapping[str, object] | str | Path,
    *,
    base_dir: str | Path | None = None,
    source_path: str | Path | None = None,
    root: DefaultRoot | None = None,
) -> TournamentRunConfig:
    """Load or build a tournament config through the canonical runtime."""

    if isinstance(config_source, TournamentRunConfig):
        return config_source

    runtime_root = root or build_default_root()
    runtime = runtime_root.tournament_runtime

    if isinstance(config_source, Mapping):
        payload = {str(key): value for key, value in config_source.items()}
        resolved_source_path = Path(source_path).resolve() if source_path is not None else None
        if base_dir is not None:
            resolved_base_dir = Path(base_dir).resolve()
        elif resolved_source_path is not None:
            resolved_base_dir = resolved_source_path.parent
        else:
            resolved_base_dir = Path.cwd()
        request = TournamentRunConfigBuildRequest(
            base_dir=resolved_base_dir,
            source_path=resolved_source_path,
        )
        return runtime.build_run_config(payload, request=request)

    config_path = Path(config_source).resolve()
    payload = parse_tournament_config_file(config_path)
    request = TournamentRunConfigBuildRequest(
        base_dir=config_path.parent,
        source_path=config_path,
    )
    return runtime.build_run_config(payload, request=request)


async def run_tournament(
    config_source: TournamentRunConfig | Mapping[str, object] | str | Path,
    *,
    run_dir: str | Path,
    storage: RunStoragePort | None = None,
    instance_pool: InstancePool | None = None,
    progress_reporter: ProgressReporterPort | None = None,
    is_dashboard_enabled: bool | None = None,
    should_skip_resume: bool = False,
    root: DefaultRoot | None = None,
) -> TournamentRunResult | None:
    """Run a tournament session using the public ShogiArena API."""

    runtime_root = root or build_default_root()
    config = load_tournament_config(config_source, root=runtime_root)
    run_storage = storage if storage is not None else create_run_storage(run_dir)
    runner = build_tournament_runner(
        config,
        storage=run_storage,
        instance_pool=instance_pool,
        progress_reporter=progress_reporter,
        is_dashboard_enabled=is_dashboard_enabled,
        should_skip_resume=should_skip_resume,
        root=runtime_root,
    )
    return await runner.run()


__all__ = [
    "EngineWdlCounts",
    "FilesystemRunStorage",
    "GameSpec",
    "DefaultRoot",
    "InstancePool",
    "JsonValue",
    "ProgressReporterPort",
    "RunStoragePort",
    "SprtDecision",
    "SprtResult",
    "TournamentResults",
    "TournamentRunConfig",
    "TournamentRunResult",
    "TournamentRunner",
    "build_tournament_runner",
    "create_run_storage",
    "load_tournament_config",
    "run_tournament",
]
