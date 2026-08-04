"""Session lifecycle helpers for SPSA runner."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import shutil
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import yaml

from shogiarena._core.contexts.game_session.adapters.context_factory import SessionContextFactory
from shogiarena._core.contexts.game_session.adapters.orchestration.config_engine import EngineConfig
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
from shogiarena._core.contexts.game_session.application.session.run_metadata_persistence_service import (
    RunMetadataPersistenceService,
)
from shogiarena._core.contexts.game_session.ports.run_storage import RunStoragePort
from shogiarena._core.contexts.game_session.ports.session_context import SessionContext
from shogiarena._core.contexts.game_session.ports.worker_deployment import WorkerBundleBuildResult
from shogiarena._core.contexts.instances.application.instance_pool import InstancePool
from shogiarena._core.contexts.instances.ports.engine_factory import EngineFactoryService
from shogiarena._core.contexts.spsa.adapters.derived_json_scheduler import SpsaDerivedJsonScheduler
from shogiarena._core.contexts.spsa.adapters.fixed_option_preflight import (
    load_fixed_option_preflight_status,
    run_yaneuraou_fixed_option_preflight,
)
from shogiarena._core.contexts.spsa.adapters.ledger_observations import (
    build_ledger_observation,
)
from shogiarena._core.contexts.spsa.adapters.orchestrator import SpsaOrchestrator
from shogiarena._core.contexts.spsa.application.session_state_io import load_or_init_spsa_run_state
from shogiarena._core.contexts.spsa.application.space_spec import (
    SpsaSpaceSpec,
    load_spsa_space_spec,
    persist_normalized_space,
)
from shogiarena._core.contexts.spsa.domain.spsa_models import ParamEntry, SpsaAlgorithmConfig
from shogiarena._core.contexts.spsa.ports.dashboard_factory import DashboardSpsaServicesFactory
from shogiarena._core.contexts.spsa.ports.ledger_ports import (
    SpsaLedgerRuntimePort,
    SpsaObservationLedgerPort,
)
from shogiarena._core.contexts.spsa.ports.spsa_store_port import (
    SpsaAnalysisPort,
    SpsaGameListingPort,
    SpsaStorePort,
    SpsaSummaryRefreshPort,
    SpsaSummaryServicePort,
    SpsaUpdateQueryPort,
)
from shogiarena._core.platform.engine_runtime.usi_config import UsiEngineConfig
from shogiarena._core.platform.settings import project_dirs
from shogiarena._core.shared.kernel.atomic_json import write_json_atomic
from shogiarena._core.shared.kernel.database_types import SpsaGameDatabaseRecord
from shogiarena._core.shared.kernel.exceptions import ContractParseError
from shogiarena._core.shared.kernel.game_results import GameResult
from shogiarena._core.shared.kernel.json_coercion import to_json_object
from shogiarena._core.shared.kernel.json_types import JsonObject
from shogiarena._core.shared.kernel.participation_records import extract_participation as _extract_participation
from shogiarena._core.shared.kernel.path_resources import BOOK_DISABLED_VALUES, resolve_path_resources
from shogiarena._core.shared.kernel.run_paths import timestamp_slug
from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_int
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
    cleanup_run_dir: Callable[..., None],
) -> tuple[Path, JsonObject]:
    resolved_run_dir = run_dir or (project_dirs.output_dir / "spsa" / "exp" / timestamp_slug())
    if (
        not should_skip_resume
        and (resolved_run_dir / "state.json").exists()
        and not (resolved_run_dir / "spsa" / "ledger.sqlite3").is_file()
    ):
        raise ValueError(
            "Legacy JSON-only SPSA run has no spsa/ledger.sqlite3; "
            "existing artifacts were not migrated. Use --no-resume to start a fresh run."
        )
    if not should_skip_resume and (resolved_run_dir / "spsa" / "ledger.sqlite3").is_file():
        return resolved_run_dir, dict(config_payload)
    resolved_run_dir.mkdir(parents=True, exist_ok=True)
    if should_skip_resume:
        cleanup_run_dir(
            resolved_run_dir,
            files=[
                "game.db",
                "state.json",
                "manifest.json",
                "remote-worker-bundle.zip",
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
    published_run_dir: Path | None = None,
) -> None:
    """Materialize SPSA baseline/tuned engine configs before manifest sealing."""

    output_dir = run_dir / "inputs" / "engine_configs"
    published_output_dir = (published_run_dir or run_dir) / "inputs" / "engine_configs"
    processed_engines: set[int] = set()
    for index, engine in enumerate([*config.baseline, *config.tuned]):
        if id(engine) in processed_engines:
            continue
        processed_engines.add(id(engine))
        resolve_engine_config_entry(
            engine,
            output_dir=output_dir,
            extra_options=None,
            artifact_resolver=artifact_resolver,
        )
        if engine.engine_path is None:
            continue
        engine_path = Path(engine.engine_path)
        if engine_path.suffix.lower() not in {".yaml", ".yml"}:
            continue
        try:
            engine_path.relative_to(output_dir)
        except ValueError:
            output_dir.mkdir(parents=True, exist_ok=True)
            archived_path = output_dir / f"external_{index:03d}{engine_path.suffix.lower()}"
        else:
            archived_path = engine_path
        _archive_spsa_engine_runtime(
            source_config_path=engine_path,
            archived_config_path=archived_path,
            engine=engine,
            index=index,
            output_dir=output_dir,
            published_output_dir=published_output_dir,
        )
        engine.engine_path = archived_path


def _archive_spsa_engine_runtime(
    *,
    source_config_path: Path,
    archived_config_path: Path,
    engine: EngineConfig,
    index: int,
    output_dir: Path,
    published_output_dir: Path,
) -> None:
    raw = yaml.safe_load(source_config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise TypeError(f"Engine config must contain a mapping: {source_config_path}")
    runtime_config = UsiEngineConfig.from_file(source_config_path)
    if runtime_config.engine_path is None:
        raise ValueError(f"Engine config has no resolved engine binary: {source_config_path}")
    source_binary = Path(runtime_config.engine_path)
    actual_resource_root = output_dir / "resources" / f"engine_{index:03d}"
    published_resource_root = published_output_dir / "resources" / f"engine_{index:03d}"
    source_working_dir = (
        Path(runtime_config.working_directory) if runtime_config.working_directory else source_binary.parent
    )
    resolved_working_dir = source_working_dir.resolve()
    actual_overlaps = actual_resource_root.resolve().is_relative_to(resolved_working_dir)
    published_overlaps = published_resource_root.resolve().is_relative_to(resolved_working_dir)
    if actual_overlaps or published_overlaps:
        raise ValueError(
            "SPSA engine working_directory must not contain the run archive; use an engine-scoped working directory"
        )
    published_working_dir = _archive_spsa_resource(
        source=source_working_dir,
        actual_root=actual_resource_root / "runtime",
        published_root=published_resource_root / "runtime",
    )
    try:
        binary_relative_path = source_binary.relative_to(source_working_dir)
    except ValueError:
        published_binary = _archive_spsa_resource(
            source=source_binary,
            actual_root=actual_resource_root / "binary",
            published_root=published_resource_root / "binary",
        )
    else:
        published_binary = published_working_dir / binary_relative_path
    raw["engine_path"] = str(published_binary)
    raw["working_directory"] = str(published_working_dir)

    raw_options = raw.get("options")
    physical_options = dict(raw_options) if isinstance(raw_options, dict) else {}
    merged_options = dict(runtime_config.options)
    merged_options.update(engine.options)
    engine.options = dict(merged_options)
    engine.options_overlays = []
    engine.artifact = None
    resources = resolve_path_resources(
        merged_options,
        engine_dir=source_working_dir,
        extra_scalar_keys=engine.path_options,
    )
    for resource_index, resource in enumerate(resources):
        if any(str(value).strip().lower() in BOOK_DISABLED_VALUES for value in resource.original_values.values()):
            continue
        published_resource = _archive_spsa_resource(
            source=Path(resource.resolved_path),
            actual_root=actual_resource_root / f"path_{resource_index:03d}",
            published_root=published_resource_root / f"path_{resource_index:03d}",
        )
        if resource.kind == "composite" and len(resource.option_keys) > 1:
            for key in resource.option_keys[:-1]:
                physical_options[key] = str(published_resource.parent)
                if key in engine.options:
                    engine.options[key] = str(published_resource.parent)
            file_key = resource.option_keys[-1]
            physical_options[file_key] = published_resource.name
            if file_key in engine.options:
                engine.options[file_key] = published_resource.name
        else:
            key = resource.option_keys[-1]
            physical_options[key] = str(published_resource)
            if key in engine.options:
                engine.options[key] = str(published_resource)
    if physical_options:
        raw["options"] = physical_options
    archived_config_path.parent.mkdir(parents=True, exist_ok=True)
    archived_config_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")


def _archive_spsa_resource(*, source: Path, actual_root: Path, published_root: Path) -> Path:
    if not source.exists():
        raise FileNotFoundError(f"SPSA engine runtime resource does not exist: {source}")
    digest = _sha256_spsa_resource(source)
    if source.is_file():
        relative = Path(f"{digest}{source.suffix.lower()}")
        actual_target = actual_root / relative
        actual_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, actual_target)
    else:
        relative = Path(digest)
        actual_target = actual_root / relative
        shutil.copytree(source, actual_target, dirs_exist_ok=True)
    return published_root / relative


def _sha256_spsa_resource(path: Path) -> str:
    digest = hashlib.sha256()
    if path.is_file():
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    for child in sorted(item for item in path.rglob("*") if item.is_file()):
        digest.update(child.relative_to(path).as_posix().encode("utf-8"))
        with child.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


def prepare_spsa_domain_inputs(
    *,
    config: SpsaRunConfig,
    run_dir: Path,
    schedule_hash: str | None,
    resume_hash: str | None,
    authority_completed_updates: int | None = None,
    authority_theta: dict[str, float] | None = None,
    is_finalization_replay: bool = False,
    space: SpsaSpaceSpec | None = None,
    fixed_option_preflight_completed: bool = False,
) -> tuple[list[ParamEntry], list[str], list[int]]:
    state_path = run_dir / "state.json"
    ledger_path = run_dir / "spsa" / "ledger.sqlite3"
    if state_path.exists() and not ledger_path.is_file():
        raise ValueError(
            "Legacy JSON-only SPSA run has no spsa/ledger.sqlite3; "
            "existing artifacts were not migrated. Use --no-resume to start a fresh run."
        )
    space = space or load_spsa_space_spec(config.space_path)
    persist_normalized_space(run_dir, space)
    params = space.to_param_entries()
    if not params:
        raise ValueError("SPSA space spec has no parameters")
    if not fixed_option_preflight_completed:
        run_yaneuraou_fixed_option_preflight(
            engines=config.tuned,
            target_option_names=(entry.engine_option_name for entry in params if not entry.is_not_used),
            run_dir=run_dir,
            output_dir=project_dirs.output_dir,
            engine_dir=project_dirs.engine_dir,
        )
    is_resume = ledger_path.is_file()
    if is_resume:
        if authority_completed_updates is None or authority_theta is None:
            raise ValueError("SPSA resume requires validated ledger authority")
        completed_updates = authority_completed_updates
        if completed_updates >= config.num_updates and not is_finalization_replay:
            raise ValueError("SPSA run is already finished; use --no-resume to start a fresh run")
        _apply_restored_theta(params=params, theta=authority_theta)
        now = datetime.now(UTC).isoformat()
        write_json_atomic(
            state_path,
            {
                "type": "spsa",
                "created_at": now,
                "updated_at": now,
                "is_finished": False,
                "completed_updates": completed_updates,
                "total_updates": int(config.num_updates),
                "schedule_hash": schedule_hash,
                "resume_hash": resume_hash,
            },
        )
    else:
        try:
            run_state = load_or_init_spsa_run_state(
                state_path,
                total_updates=config.num_updates,
                schedule_hash=schedule_hash,
                resume_hash=resume_hash,
            )
        except (OSError, json.JSONDecodeError, ContractParseError, TypeError, ValueError) as exc:
            raise ValueError(f"Invalid SPSA state.json: {exc}") from exc

        completed_updates = coerce_int(run_state.get("completed_updates"))
        if completed_updates is None:
            raise ValueError("Invalid SPSA state.json: completed_updates must be an integer")

    _write_spsa_schedule_contract(run_dir=run_dir, config=config, params=params)
    if not is_resume or authority_theta is not None:
        _write_spsa_current_artifact(run_dir=run_dir, update_idx=0, pair_index_end=0, params=params)
        if completed_updates > 0:
            _write_spsa_current_artifact(
                run_dir=run_dir,
                update_idx=completed_updates,
                pair_index_end=completed_updates * int(config.pairs_per_update),
                params=params,
            )
    with open(config.start_sfens_path, encoding="utf-8") as handle:
        sfens = [line.rstrip() for line in handle if line.strip()]
    if not sfens:
        raise RuntimeError("No SFENs available")
    n = config.num_updates
    if n <= 0:
        raise ValueError("SPSA requires a positive 'num_updates'")
    update_items = [] if is_finalization_replay else list(range(completed_updates + 1, n + 1))
    return params, sfens, update_items


def _apply_restored_theta(*, params: list[ParamEntry], theta: dict[str, float]) -> None:
    active_params = {entry.name: entry for entry in params if not entry.is_not_used}
    if set(theta) != set(active_params):
        raise ValueError("SPSA ledger theta parameter set does not match the sealed space")
    for name, value in theta.items():
        entry = active_params[name]
        restored = float(value)
        if not math.isfinite(restored) or restored < float(entry.min) or restored > float(entry.max):
            raise ValueError(f"SPSA ledger theta.{name} is outside its valid bounds")
    for name, value in theta.items():
        active_params[name].value = float(value)


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
    observation_ledger: SpsaObservationLedgerPort | None = None,
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

    participation = _extract_participation(game_info)
    if not participation:
        raise ValueError("SPSA terminal game is missing required participation identity")
    game_db_id = db_service.append_record_with_participation(
        game_info,
        participation=participation,
    )
    if observation_ledger is not None:
        observation_ledger.insert_committed(
            build_ledger_observation(
                SpsaGameDatabaseRecord(
                    game_db_id=game_db_id,
                    game_id=str(game_info.game_name),
                    result=result,
                    participation_extras=tuple(
                        to_json_object(record.extra) for record in participation if record.extra is not None
                    ),
                )
            )
        )
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
    run_dir: Path,
) -> JsonObject | None:
    if summary_service is None:
        return None
    # ここは run 実行中のコアレス済み refresh 経路（対局の進捗ごと、worker thread 上）。
    # snapshot を持つサービスなら、ここで計算し直して `/summary` が読む snapshot を更新する。
    # そうでなければ従来どおり直接計算する。
    compute = (
        summary_service.refresh_summary
        if isinstance(summary_service, SpsaSummaryRefreshPort)
        else summary_service.compute_summary
    )
    raw_summary_payload = await asyncio.to_thread(compute)
    if not isinstance(raw_summary_payload, dict):
        return None
    summary_payload = {str(key): json_serialize(value) for key, value in raw_summary_payload.items()}
    summary_payload.setdefault("tournament_type", "spsa")
    summary_payload.setdefault("mode", "spsa")
    summary_payload["timestamp"] = datetime.now(tz=UTC).isoformat()
    summary_payload["engines_meta"] = engine_metadata
    summary_payload["preflight_status"] = load_fixed_option_preflight_status(run_dir)
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
    ledger_runtime: SpsaLedgerRuntimePort,
    derived_json_scheduler: SpsaDerivedJsonScheduler | None = None,
    remote_worker_bundle: WorkerBundleBuildResult | None = None,
) -> SpsaOrchestrator:
    return SpsaOrchestrator(
        config=config,
        session=session_context,
        hooks=hooks,
        db_service=db_service,
        engine_factory_service=engine_factory_service,
        summary_updater=summary_updater,
        api_server=api_server,
        ledger_runtime=ledger_runtime,
        derived_json_scheduler=derived_json_scheduler,
        remote_worker_bundle=remote_worker_bundle,
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
