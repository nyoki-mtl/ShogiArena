"""Canonical engine catalog adapter shared by session runners."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from shogiarena._core.contexts.engine_catalog.adapters.yaml_file_loader import YamlFileLoaderAdapter
from shogiarena._core.contexts.engine_catalog.application.catalog_service import EngineCatalogService
from shogiarena._core.contexts.engine_catalog.domain.rules_sync import build_max_ply_sync_options
from shogiarena._core.contexts.engine_catalog.ports.catalog_service import (
    EngineCatalogEntryRequest,
    EngineCatalogRequest,
)
from shogiarena._core.contexts.game_session.adapters.orchestration.config_core import RulesConfig
from shogiarena._core.contexts.game_session.adapters.orchestration.config_engine import EngineConfig
from shogiarena._core.platform.settings import project_dirs
from shogiarena._core.shared.kernel.json_types import JsonObject
from shogiarena._core.shared.kernel.serialization import json_serialize
from shogiarena._core.shared.kernel.service_ports import ArtifactResolutionPort
from shogiarena._core.shared.kernel.snapshots import EngineInfoSnapshots, EngineOptionsSnapshots


def collect_engine_metadata(
    *,
    engines: Iterable[EngineConfig],
    rules: RulesConfig,
    config_source_path: Path | str | None,
    run_dir: Path,
    runtime_options: EngineOptionsSnapshots,
    runtime_info: EngineInfoSnapshots,
    artifact_resolver: ArtifactResolutionPort,
) -> list[JsonObject]:
    engine_catalog_service = EngineCatalogService(
        file_loader=YamlFileLoaderAdapter(),
        artifact_resolver=artifact_resolver,
    )
    request = _build_engine_catalog_request(
        engines=engines,
        rules=rules,
        config_source_path=config_source_path,
        run_dir=run_dir,
        runtime_options=runtime_options,
        runtime_info=runtime_info,
    )
    return engine_catalog_service.build_metadata(request)


def engine_instance_defaults(engines: Iterable[EngineConfig]) -> dict[str, str | None]:
    engine_catalog_service = EngineCatalogService(file_loader=YamlFileLoaderAdapter())
    return engine_catalog_service.compute_instance_defaults(
        _build_engine_catalog_request(
            engines=engines,
            rules=None,
            config_source_path=None,
            run_dir=project_dirs.output_dir,
        )
    )


def compute_engine_time_control_specs(
    rules: RulesConfig,
    engines: Iterable[EngineConfig],
) -> tuple[dict[str, str], str | None]:
    engine_catalog_service = EngineCatalogService(file_loader=YamlFileLoaderAdapter())
    return engine_catalog_service.compute_time_control_specs(
        _build_engine_catalog_request(
            engines=engines,
            rules=rules,
            config_source_path=None,
            run_dir=project_dirs.output_dir,
        )
    )


def _build_engine_catalog_request(
    *,
    engines: Iterable[EngineConfig],
    rules: RulesConfig | None,
    config_source_path: Path | str | None,
    run_dir: Path,
    runtime_options: EngineOptionsSnapshots | None = None,
    runtime_info: EngineInfoSnapshots | None = None,
) -> EngineCatalogRequest:
    entries = tuple(_build_entry_request(engine) for engine in engines)
    return EngineCatalogRequest(
        entries=entries,
        base_time_control=rules.time_control if rules is not None else None,
        run_dir=run_dir,
        path_output_dir=project_dirs.output_dir,
        engine_dir=project_dirs.engine_dir,
        config_source_path=config_source_path,
        rules_synced_options=_build_rules_synced_options(rules),
        runtime_options=runtime_options or {},
        runtime_info=runtime_info or {},
    )


def _build_entry_request(engine: EngineConfig) -> EngineCatalogEntryRequest:
    inline_options: JsonObject = {str(key): json_serialize(value) for key, value in (engine.options or {}).items()}
    build_options: JsonObject = {str(key): json_serialize(value) for key, value in (engine.build_options or {}).items()}
    return EngineCatalogEntryRequest(
        name=str(engine.name),
        engine_config_path=engine.engine_path,
        artifact=engine.artifact,
        artifact_overlay_path=_resolve_artifact_overlay_path(engine.artifact),
        build_options=build_options,
        inline_options=inline_options,
        options_overlay_paths=tuple(engine.options_overlays),
        time_control=engine.time_control,
        instance_id=engine.instance_id,
    )


def _resolve_artifact_overlay_path(artifact: str | None) -> Path | None:
    if not artifact or not artifact.strip():
        return None
    repo_name = artifact.split("/", 1)[0]
    return project_dirs.overlays.get(repo_name)


def _build_rules_synced_options(rules: RulesConfig | None) -> JsonObject:
    if rules is None:
        return {}
    adjudication = rules.adjudication
    return build_max_ply_sync_options(
        is_max_plies_enabled=adjudication.is_max_plies_enabled,
        should_sync_max_plies_with_engine=adjudication.should_sync_max_plies_with_engine,
        max_plies=adjudication.max_plies,
        engine_max_ply_option_names=adjudication.engine_max_ply_option_names,
    )


__all__ = [
    "collect_engine_metadata",
    "compute_engine_time_control_specs",
    "engine_instance_defaults",
]
