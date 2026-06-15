"""Session lifecycle helpers for SPSA runner."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from shogiarena._core.contexts.game_session.adapters.context_factory import SessionContextFactory
from shogiarena._core.contexts.game_session.adapters.orchestration.config_spsa_models import (
    EarlyStopConfig,
    LtcRegressionConfig,
    SpsaRunConfig,
)
from shogiarena._core.contexts.game_session.adapters.orchestration.engine_config_artifacts import (
    resolve_engine_config_entry,
)
from shogiarena._core.contexts.game_session.adapters.results.run_result_models import (
    SpsaRunResult,
    serialize_rules_config,
)
from shogiarena._core.contexts.game_session.application.progress.hub import DashboardServerPort, SummaryUpdateCallback
from shogiarena._core.contexts.game_session.application.session.base_session_runner import BaseSessionRunner
from shogiarena._core.contexts.game_session.application.session.run_metadata_persistence_service import (
    RunMetadataPersistenceService,
)
from shogiarena._core.contexts.game_session.ports.run_storage import RunStoragePort
from shogiarena._core.contexts.game_session.ports.session_context import SessionContext
from shogiarena._core.contexts.instances.application.instance_pool import InstancePool
from shogiarena._core.contexts.instances.ports.engine_factory import EngineFactoryService
from shogiarena._core.contexts.spsa.adapters.orchestrator import SpsaOrchestrator
from shogiarena._core.contexts.spsa.application.session_state_io import load_or_init_spsa_run_state
from shogiarena._core.contexts.spsa.application.space_spec import load_spsa_space_spec, persist_normalized_space
from shogiarena._core.contexts.spsa.domain.spsa_models import ParamEntry, SpsaAlgorithmConfig
from shogiarena._core.contexts.spsa.ports.dashboard_factory import DashboardSpsaServicesFactory
from shogiarena._core.contexts.spsa.ports.spsa_store_port import (
    SpsaAnalysisPort,
    SpsaGameListingPort,
    SpsaStorePort,
    SpsaSummaryServicePort,
    SpsaUpdateQueryPort,
)
from shogiarena._core.platform.settings import project_dirs
from shogiarena._core.shared.kernel.atomic_json import write_json_atomic
from shogiarena._core.shared.kernel.exceptions import ContractParseError
from shogiarena._core.shared.kernel.game_results import GameResult
from shogiarena._core.shared.kernel.json_coercion import to_json_object
from shogiarena._core.shared.kernel.json_types import JsonObject
from shogiarena._core.shared.kernel.participation_records import extract_participation as _extract_participation
from shogiarena._core.shared.kernel.run_paths import timestamp_slug
from shogiarena._core.shared.kernel.serialization import json_serialize
from shogiarena._core.shared.kernel.service_ports import ArtifactResolutionPort, DatabaseServicePort
from shogiarena._core.shared.kernel.session_hooks import (
    GameCompletionEvent,
    GameCompletionPayload,
    GameLifecycleHooks,
)


def build_spsa_rules_payload(config: SpsaRunConfig) -> JsonObject:
    """Serialize the rules section of an SPSA run config."""
    return serialize_rules_config(config.rules)


def build_spsa_algorithm_config(config: SpsaRunConfig) -> SpsaAlgorithmConfig:
    """Build a dict of SPSA algorithm parameters for serialization."""
    return {
        "num_updates": config.num_updates,
        "pairs_per_update": config.pairs_per_update,
        "scale": config.scale,
        "A": float(config.algorithm_a) if config.algorithm_a is not None else None,
        "alpha": config.alpha,
        "gamma": config.gamma,
        "is_crn_enabled": config.is_crn_enabled,
        "int_rounding": config.int_rounding,
        "int_ck_floor": config.int_ck_floor,
        "update_mode": config.update_mode,
        "should_snap_float_to_step": config.is_snap_float_to_step,
        "early_stop": _serialize_spsa_early_stop_config(config.early_stop),
        "update_batch_size": int(config.pairs_per_update),
        "inflight_factor": config.inflight_factor,
    }


def _serialize_spsa_early_stop_config(config: EarlyStopConfig | None) -> JsonObject | None:
    """Serialize an SPSA early-stop configuration to a JSON-compatible dict."""
    if config is None:
        return None
    return to_json_object(config.model_dump(mode="json"))


def serialize_spsa_ltc_config(config: LtcRegressionConfig | None) -> JsonObject | None:
    """Serialize an SPSA LTC regression configuration to a JSON-compatible dict."""
    if config is None or not config.is_enabled:
        return None

    data: JsonObject = {
        "enabled": True,
        "every_n_updates": int(config.every_n_updates),
        "total_pairs": int(config.total_pairs),
    }

    if config.time_control is not None:
        data["time_control"] = config.time_control.model_dump()
    if config.pass_criteria is not None:
        data["pass_criteria"] = config.pass_criteria.model_dump()

    return data


def prepare_spsa_run_directory(
    *,
    run_dir: Path | None,
    should_skip_resume: bool,
    config_payload: JsonObject,
    run_metadata_service: RunMetadataPersistenceService,
) -> tuple[Path, JsonObject]:
    resolved_run_dir = run_dir or (project_dirs.output_dir / "spsa" / "exp" / timestamp_slug())
    resolved_run_dir.mkdir(parents=True, exist_ok=True)
    if should_skip_resume:
        BaseSessionRunner.cleanup_run_dir(
            resolved_run_dir,
            files=[
                "game.db",
                "state.json",
                "manifest.json",
            ],
            dirs=["spsa", "static", "html", "dashboard", "inputs", "results", "failures", "logs"],
        )
    inputs = run_metadata_service.write_inputs_only_manifest(
        run_dir=resolved_run_dir,
        config_payload=config_payload,
        package_name="shogiarena",
    )
    return resolved_run_dir, inputs.config_payload


def materialize_spsa_engine_configs(
    *,
    config: SpsaRunConfig,
    run_dir: Path,
    artifact_resolver: ArtifactResolutionPort | None,
) -> None:
    """Materialize SPSA baseline/tuned engine configs before manifest sealing."""

    output_dir = run_dir / "inputs" / "engine_configs"
    for engine in [*config.baseline, *config.tuned]:
        resolve_engine_config_entry(
            engine,
            output_dir=output_dir,
            extra_options=None,
            artifact_resolver=artifact_resolver,
        )


def prepare_spsa_domain_inputs(
    *,
    config: SpsaRunConfig,
    run_dir: Path,
    schedule_hash: str | None,
    resume_hash: str | None,
) -> tuple[list[ParamEntry], list[str], list[int]]:
    space = load_spsa_space_spec(config.space_path)
    persist_normalized_space(run_dir, space)
    state_path = run_dir / "state.json"
    try:
        load_or_init_spsa_run_state(
            state_path,
            total_updates=config.num_updates,
            schedule_hash=schedule_hash,
            resume_hash=resume_hash,
        )
    except (OSError, json.JSONDecodeError, ContractParseError, TypeError) as exc:
        raise ValueError(f"Invalid SPSA state.json: {exc}") from exc

    params = space.to_param_entries()
    if not params:
        raise ValueError("SPSA space spec has no parameters")
    _write_spsa_schedule_contract(run_dir=run_dir, config=config, params=params)
    _write_spsa_current_artifact(run_dir=run_dir, update_idx=0, pair_index_end=0, params=params)
    with open(config.start_sfens_path, encoding="utf-8") as handle:
        sfens = [line.rstrip() for line in handle if line.strip()]
    if not sfens:
        raise RuntimeError("No SFENs available")
    n = config.num_updates
    if n <= 0:
        raise ValueError("SPSA requires a positive 'num_updates'")
    update_items = list(range(1, n + 1))
    return params, sfens, update_items


def _write_spsa_schedule_contract(*, run_dir: Path, config: SpsaRunConfig, params: list[ParamEntry]) -> None:
    payload: JsonObject = {
        "kind": "spsa",
        "schema_version": 1,
        "optimization_contract": {
            "params": [entry.name for entry in params],
            "num_updates": config.num_updates,
            "pairs_per_update": config.pairs_per_update,
            "crn_enabled": config.is_crn_enabled,
            "algorithm": build_spsa_algorithm_config(config),
        },
    }
    write_json_atomic(run_dir / "schedule.json", payload)


def _write_spsa_current_artifact(
    *,
    run_dir: Path,
    update_idx: int,
    pair_index_end: int,
    params: list[ParamEntry],
) -> None:
    payload: JsonObject = {
        "schema_version": "shogiarena.spsa.current.v1",
        "update_idx": int(update_idx),
        "pair_index_end": int(pair_index_end),
        "theta": {entry.name: float(entry.value) for entry in params if not entry.is_not_used},
    }
    write_json_atomic(run_dir / "spsa" / "current.json", payload)


def persist_spsa_game_completion(
    *,
    db_service: DatabaseServicePort,
    event: GameCompletionEvent[GameCompletionPayload],
) -> bool:
    game_info = event.game_info
    game_info.update_metadata({"game_type": "spsa"}, strict=True)
    result = game_info.result
    should_persist = result != GameResult.PAUSED
    if (
        should_persist
        and event.is_stop_requested
        and result
        in (
            GameResult.PAUSED,
            GameResult.ERROR,
            GameResult.INVALID,
        )
    ):
        should_persist = False

    if not should_persist:
        return False

    db_service.append_record_list([game_info])
    participation = _extract_participation(game_info)
    if participation:
        game_id_name = game_info.game_name
        game_id = db_service.get_game_id_by_name(str(game_id_name)) if game_id_name else None
        if game_id is not None:
            db_service.record_game_participation(game_id=game_id, participation=participation)
    return True


def build_spsa_final_result(
    *,
    config: SpsaRunConfig,
    run_dir: Path,
    storage: RunStoragePort,
    summary: JsonObject | None,
    params: list[ParamEntry] | None,
) -> SpsaRunResult:
    final_params: dict[str, float] = {}
    if params is not None:
        for entry in params:
            final_params[entry.name] = float(entry.value)
    return SpsaRunResult(
        summary=summary,
        final_params=final_params,
        run_id=str(config.experiment_name or run_dir.name),
        run_dir=run_dir,
        storage=storage,
    )


def build_spsa_session_context(
    *,
    config: SpsaRunConfig,
    storage: RunStoragePort,
    num_workers: int,
    is_dashboard_enabled: bool,
    should_skip_resume: bool,
    session_uuid: str,
    instance_pool: InstancePool,
    session_context_factory: SessionContextFactory,
) -> SessionContext:
    run_id = str(config.experiment_name or storage.run_dir.name)
    metadata = {
        "runner_type": "spsa",
        "experiment_name": str(config.experiment_name or ""),
        "is_dashboard_enabled": bool(is_dashboard_enabled),
        "num_workers": int(num_workers),
        "should_skip_resume": bool(should_skip_resume),
        "session_uuid": session_uuid,
    }
    return session_context_factory.build_or_resume(
        storage=storage,
        num_workers=num_workers,
        instance_pool=instance_pool,
        run_id=run_id,
        metadata=metadata,
        should_skip_resume=bool(should_skip_resume),
    )


def init_spsa_dashboard_services(
    *,
    is_dashboard_enabled: bool,
    run_dir: Path | None,
    storage_run_dir: Path,
    db_service: DatabaseServicePort | None,
    dashboard_service_factory: DashboardSpsaServicesFactory,
    store: SpsaStorePort | None,
    summary_service: SpsaSummaryServicePort | None,
    update_query_service: SpsaUpdateQueryPort | None,
    game_listing_service: SpsaGameListingPort | None,
    analysis_service: SpsaAnalysisPort | None,
    logger: logging.Logger,
) -> tuple[
    SpsaStorePort | None,
    SpsaSummaryServicePort | None,
    SpsaUpdateQueryPort | None,
    SpsaGameListingPort | None,
    SpsaAnalysisPort | None,
]:
    if not is_dashboard_enabled:
        return store, summary_service, update_query_service, game_listing_service, analysis_service
    if run_dir is None or db_service is None:
        return store, summary_service, update_query_service, game_listing_service, analysis_service

    services = dashboard_service_factory.create_services(
        run_dir=run_dir,
        db_path=storage_run_dir / "game.db",
        store=store,
        summary_service=summary_service,
        update_query_service=update_query_service,
        game_listing_service=game_listing_service,
        analysis_service=analysis_service,
    )
    return (
        services.store,
        services.summary_service,
        services.update_query_service,
        services.game_listing_service,
        services.analysis_service,
    )


async def build_spsa_dashboard_summary_payload(
    *,
    summary_service: SpsaSummaryServicePort | None,
    engine_metadata: list[JsonObject],
) -> JsonObject | None:
    if summary_service is None:
        return None
    raw_summary_payload = await asyncio.to_thread(summary_service.compute_summary)
    if not isinstance(raw_summary_payload, dict):
        return None
    summary_payload = {str(key): json_serialize(value) for key, value in raw_summary_payload.items()}
    summary_payload.setdefault("tournament_type", "spsa")
    summary_payload.setdefault("mode", "spsa")
    summary_payload["timestamp"] = datetime.now(tz=UTC).isoformat()
    summary_payload["engines_meta"] = engine_metadata
    return summary_payload


def create_spsa_orchestrator(
    *,
    config: SpsaRunConfig,
    session_context: SessionContext,
    hooks: GameLifecycleHooks,
    db_service: DatabaseServicePort | None,
    engine_factory_service: EngineFactoryService,
    summary_updater: SummaryUpdateCallback | None,
    api_server: DashboardServerPort | None,
) -> SpsaOrchestrator:
    return SpsaOrchestrator(
        config=config,
        session=session_context,
        hooks=hooks,
        db_service=db_service,
        engine_factory_service=engine_factory_service,
        summary_updater=summary_updater,
        api_server=api_server,
    )


__all__ = [
    "build_spsa_algorithm_config",
    "build_spsa_dashboard_summary_payload",
    "build_spsa_final_result",
    "build_spsa_rules_payload",
    "build_spsa_session_context",
    "create_spsa_orchestrator",
    "init_spsa_dashboard_services",
    "materialize_spsa_engine_configs",
    "persist_spsa_game_completion",
    "prepare_spsa_domain_inputs",
    "prepare_spsa_run_directory",
    "serialize_spsa_ltc_config",
]
