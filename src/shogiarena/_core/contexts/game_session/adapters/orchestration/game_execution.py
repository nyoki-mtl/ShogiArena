"""Game execution helper extracted from BaseOrchestrator."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, cast

import rsshogi.record
from rsshogi.types import Color

from shogiarena._core.contexts.game_session.adapters.orchestration.config_builders import (
    build_usi_option_layers,
)
from shogiarena._core.contexts.game_session.adapters.orchestration.game_execution_manifest import (
    persist_game_execution_manifest,
)
from shogiarena._core.contexts.game_session.adapters.orchestration.game_execution_materializer import (
    apply_engine_variant,
    materialize_engine_config,
    materialize_opening_sfen,
    resolve_effective_handshake_timeout,
)
from shogiarena._core.contexts.game_session.adapters.orchestration.game_execution_output import (
    build_game_execution_result,
)
from shogiarena._core.contexts.game_session.adapters.orchestration.game_execution_spec_resolver import (
    EngineSpecProvenance,
)
from shogiarena._core.contexts.game_session.adapters.orchestration.game_runner_factory import (
    build_game_runner_execution_policy,
)
from shogiarena._core.contexts.game_session.adapters.orchestration.local_game_execution_contract import (
    resolve_local_game_execution,
)
from shogiarena._core.contexts.game_session.adapters.orchestration.participation_records import (
    attach_participation_metadata as _attach_participation_metadata_service,
)
from shogiarena._core.contexts.game_session.adapters.orchestration.participation_records import (
    collect_participation_records_local as _collect_participation_records_local_service,
)
from shogiarena._core.contexts.game_session.adapters.orchestration.resource_control import (
    await_instance_resources as _await_instance_resources_service,
)
from shogiarena._core.contexts.game_session.adapters.orchestration.resource_control import (
    collect_instance_usage as _collect_instance_usage_service,
)
from shogiarena._core.contexts.game_session.adapters.orchestration.usi_transcript import (
    DEFAULT_TRANSCRIPT_MAX_BYTES,
    GameUsiTranscriptContext,
    TranscriptDetail,
)
from shogiarena._core.contexts.game_session.application.session.run_failure_record_service import (
    RunFailureRecordService,
)
from shogiarena._core.contexts.game_session.domain.failure_records import coerce_failure_phase
from shogiarena._core.contexts.game_session.ports.game_execution_spec import EngineExecutionSpec
from shogiarena._core.contexts.game_session.ports.session_runner_ports import BeforeGameHookPort, BeforeGameHookRequest
from shogiarena._core.contexts.instances.application.instance_models import InstanceActiveGameSide
from shogiarena._core.contexts.instances.application.instance_pool import ResourceRequest
from shogiarena._core.contexts.match.application.engine_participant import EngineParticipant
from shogiarena._core.platform.engine_runtime.usi_engine_session import AsyncUsiEngine
from shogiarena._core.platform.engine_runtime.usi_engine_session_models import UsiEngineStartError
from shogiarena._core.shared.kernel.game_results import game_result_name
from shogiarena._core.shared.kernel.json_types import JsonObject
from shogiarena._core.shared.kernel.run_artifact_hashes import canonical_sha256
from shogiarena._core.shared.kernel.schedule_metadata import attach_schedule_metadata
from shogiarena._core.shared.kernel.time_control import TimeControlLimits

from .config_engine import EngineConfig

logger = logging.getLogger(__name__)


class _EngineItemPort(Protocol):
    pool_key: str
    config_path: Path
    extra_options: JsonObject | None
    instance_override: str | None


class _TimeControlLimitsPort(Protocol):
    def to_spec_str(self) -> str: ...


class _GameSpecPort(Protocol):
    black_item: _EngineItemPort
    white_item: _EngineItemPort
    initial_sfen: str
    game_id: str
    black_limits: _TimeControlLimitsPort | None
    white_limits: _TimeControlLimitsPort | None
    black_variant_options: JsonObject | None
    white_variant_options: JsonObject | None
    black_variant_id: str | None
    white_variant_id: str | None
    clear_hash_before_game: bool
    after_variant_setoption: str
    before_game_hook: BeforeGameHookPort | None
    game_round: int | None
    schedule_metadata: JsonObject | None
    on_game_start: Callable[[], Awaitable[None]] | None


@dataclass(slots=True)
class _ResourceContext:
    resource_requirements: dict[str, ResourceRequest]
    is_slots_reserved: bool
    instance_role_map: dict[str, list[InstanceActiveGameSide]]
    black_engine_spec: EngineConfig | None
    white_engine_spec: EngineConfig | None


def _empty_resource_context() -> _ResourceContext:
    return _ResourceContext(
        resource_requirements={},
        is_slots_reserved=False,
        instance_role_map={},
        black_engine_spec=None,
        white_engine_spec=None,
    )


async def execute_game(orchestrator: Any, spec: Any) -> rsshogi.record.Record:
    """Acquire engines, run one game, and release resources."""

    owner = orchestrator
    game_spec = cast(_GameSpecPort, spec)
    ep = owner.engine_pool
    assert ep is not None, "EnginePool not initialized"

    resource_context = _empty_resource_context()
    black_engine: AsyncUsiEngine | None = None
    white_engine: AsyncUsiEngine | None = None
    game_runner = None
    black_contract_digest: str | None = None
    white_contract_digest: str | None = None

    try:
        resource_context = await _prepare_resource_context(owner, game_spec)
        black_limits = _require_time_control(game_spec.black_limits, side="black")
        white_limits = _require_time_control(game_spec.white_limits, side="white")
        black_config = resource_context.black_engine_spec
        white_config = resource_context.white_engine_spec
        if black_config is None or white_config is None:
            raise ValueError("Local GameExecutionSpec requires both resolved engine configs")
        black_layers = build_usi_option_layers(owner.extra_options, black_config)
        white_layers = build_usi_option_layers(owner.extra_options, white_config)
        resolved_execution = resolve_local_game_execution(
            run_id=owner.session_context.run_id,
            game_id=game_spec.game_id,
            initial_sfen=game_spec.initial_sfen,
            black_name=black_config.name or game_spec.black_item.pool_key,
            white_name=white_config.name or game_spec.white_item.pool_key,
            black_config_path=Path(game_spec.black_item.config_path),
            white_config_path=Path(game_spec.white_item.config_path),
            black_artifact_overlay_options=black_layers.artifact_overlay,
            white_artifact_overlay_options=white_layers.artifact_overlay,
            black_arena_options=black_layers.arena,
            white_arena_options=white_layers.arena,
            black_overlay_options=black_layers.declared_overlays,
            white_overlay_options=white_layers.declared_overlays,
            black_inline_options=black_layers.inline,
            white_inline_options=white_layers.inline,
            black_variant_options=game_spec.black_variant_options,
            white_variant_options=game_spec.white_variant_options,
            black_variant_id=game_spec.black_variant_id,
            white_variant_id=game_spec.white_variant_id,
            clear_hash_before_game=game_spec.clear_hash_before_game,
            after_variant_setoption=game_spec.after_variant_setoption,
            black_path_option_names=tuple(black_config.path_options),
            white_path_option_names=tuple(white_config.path_options),
            black_go_options={str(key): value for key, value in black_config.go_options.items()},
            white_go_options={str(key): value for key, value in white_config.go_options.items()},
            black_handshake_timeout_s=_effective_handshake_timeout(owner, black_config),
            white_handshake_timeout_s=_effective_handshake_timeout(owner, white_config),
            black_limits=black_limits,
            white_limits=white_limits,
            rules=owner.config.rules,
            engine_lifecycle=owner._engine_lifecycle,
            timeout_reclassification_enabled=owner._timeout_reclassification_enabled,
        )
        persist_game_execution_manifest(
            run_dir=owner.run_dir,
            game_id=game_spec.game_id,
            payload=resolved_execution.manifest_payload(),
        )
        black_secret_values = _local_secret_values(resolved_execution.spec.black_engine)
        white_secret_values = _local_secret_values(resolved_execution.spec.white_engine)
        black_mapping = materialize_engine_config(
            resolved_execution.spec.black_engine,
            execution_root=owner.run_dir,
            artifact_paths=_artifact_paths(
                resolved_execution.spec.black_engine,
                resolved_execution.engine_provenance[0],
            ),
            secret_values=black_secret_values,
        )
        white_mapping = materialize_engine_config(
            resolved_execution.spec.white_engine,
            execution_root=owner.run_dir,
            artifact_paths=_artifact_paths(
                resolved_execution.spec.white_engine,
                resolved_execution.engine_provenance[1],
            ),
            secret_values=white_secret_values,
        )
        black_contract_digest = _engine_pool_contract_digest(black_mapping)
        white_contract_digest = _engine_pool_contract_digest(white_mapping)
        Path(str(black_mapping["working_directory"])).mkdir(parents=True, exist_ok=True)
        Path(str(white_mapping["working_directory"])).mkdir(parents=True, exist_ok=True)
        black_engine, white_engine = await ep.acquire_pair_from_mappings(
            (
                game_spec.black_item.pool_key,
                black_mapping,
                black_contract_digest,
                game_spec.black_item.instance_override,
                black_config.cpu_affinity,
                resolved_execution.spec.black_engine.process.handshake_timeout_ms / 1000.0,
            ),
            (
                game_spec.white_item.pool_key,
                white_mapping,
                white_contract_digest,
                game_spec.white_item.instance_override,
                white_config.cpu_affinity,
                resolved_execution.spec.white_engine.process.handshake_timeout_ms / 1000.0,
            ),
        )
        await apply_engine_variant(black_engine, resolved_execution.spec.black_engine)
        await apply_engine_variant(white_engine, resolved_execution.spec.white_engine)
        policy = build_game_runner_execution_policy(
            resolved_execution.spec,
            progress_queue=owner.progress_sink,
            runtime_watchdog=owner._runtime_watchdog,
        )
        game_runner = policy.runner
        game_runner.set_engine_options_callback(owner._handle_engine_options)
        if owner.api_server is not None:
            game_runner.set_engine_io_wanted(owner.api_server.has_engine_io_subscribers)
        owner._active_game_runners.add(game_runner)
        started_at = datetime.now(UTC)
        game_info = await _run_game_with_engines(
            owner,
            game_spec,
            game_runner=game_runner,
            black_engine=black_engine,
            white_engine=white_engine,
            black_engine_spec=resource_context.black_engine_spec,
            white_engine_spec=resource_context.white_engine_spec,
            initial_sfen=materialize_opening_sfen(resolved_execution.spec.opening),
        )
        result_envelope = build_game_execution_result(
            resolved_execution.spec,
            classification=game_result_name(game_info.result),
            started_at=started_at,
            finished_at=datetime.now(UTC),
            engine_info={
                "black": black_engine.engine_info,
                "white": white_engine.engine_info,
            },
        )
        game_info.set_metadata_attribute("game_execution_result", result_envelope.model_dump_json())
        return game_info
    except asyncio.CancelledError as exc:
        _record_run_failure(owner, game_spec, exc, fallback_phase="user_interruption")
        raise
    except (TimeoutError, OSError, RuntimeError, ValueError) as exc:
        _record_run_failure(
            owner,
            game_spec,
            exc,
            fallback_phase=_classify_execution_failure(exc),
        )
        raise
    finally:
        if game_runner is not None:
            owner._active_game_runners.discard(game_runner)
        # An exception may already be propagating from the body (including
        # CancelledError). Capture it before cleanup so a cleanup failure does
        # not mask the original error / break cancellation propagation.
        pending_exc = sys.exc_info()[1]
        try:
            await _cleanup_execution(
                owner,
                game_spec,
                engine_pool=ep,
                black_engine=black_engine,
                white_engine=white_engine,
                resource_context=resource_context,
                black_contract_digest=black_contract_digest,
                white_contract_digest=white_contract_digest,
            )
        except (TimeoutError, OSError, RuntimeError, ValueError) as exc:
            _record_run_failure(owner, game_spec, exc, fallback_phase="shutdown")
            if pending_exc is None:
                raise
            logger.warning(
                "Cleanup failed for game %s but preserving original %s: %s",
                game_spec.game_id,
                type(pending_exc).__name__,
                exc,
                exc_info=True,
            )


def _artifact_paths(engine_spec: EngineExecutionSpec, provenance: EngineSpecProvenance) -> dict[str, Path]:
    paths = {
        engine_spec.process.artifact.logical_id: Path(provenance.engine_source_path),
    }
    for resource, source in zip(engine_spec.usi.path_resources, provenance.path_sources, strict=True):
        paths[resource.artifact.logical_id] = Path(source)
    return paths


def _local_secret_values(engine_spec: EngineExecutionSpec) -> dict[str, str]:
    references = set(engine_spec.process.secret_environment_refs.values())
    missing = sorted(reference for reference in references if reference not in os.environ)
    if missing:
        raise ValueError(f"required engine secret references are unavailable: {', '.join(missing)}")
    return {reference: os.environ[reference] for reference in references}


def _engine_pool_contract_digest(
    config_mapping: Mapping[str, object],
) -> str:
    """Process startup contractをreuse keyへ反映する。値自体は保存・出力しない。"""

    return canonical_sha256(config_mapping)


def _require_time_control(value: object, *, side: str) -> TimeControlLimits:
    if not isinstance(value, TimeControlLimits):
        raise ValueError(f"Local GameExecutionSpec requires {side} time control")
    return value


def _effective_handshake_timeout(owner: Any, config: EngineConfig) -> float:
    return resolve_effective_handshake_timeout(
        config.handshake_timeout,
        getattr(owner, "_default_engine_handshake_timeout", None),
    )


def _record_run_failure(owner: Any, game_spec: _GameSpecPort, exc: BaseException, *, fallback_phase: str) -> None:
    run_dir = getattr(owner, "run_dir", None)
    if not isinstance(run_dir, Path):
        logger.debug(
            "Skipping run failure record for game %s because owner %s has no Path run_dir",
            game_spec.game_id,
            type(owner).__name__,
        )
        return
    record = RunFailureRecordService.build_record_from_exception(
        exc=exc,
        game_id=game_spec.game_id,
        scheduled_black_engine=game_spec.black_item.pool_key,
        scheduled_white_engine=game_spec.white_item.pool_key,
        fallback_phase=coerce_failure_phase(fallback_phase),
        log_artifact_path=str(run_dir / "failures" / "run_failures.json"),
    )
    try:
        RunFailureRecordService().append_failure(run_dir=run_dir, record=record)
    except (OSError, RuntimeError, ValueError) as record_exc:
        # Persisting the diagnostic must never mask the original failure that we
        # are in the middle of propagating.
        logger.warning(
            "Failed to persist run failure record for game %s: %s",
            game_spec.game_id,
            record_exc,
            exc_info=True,
        )


def _classify_execution_failure(exc: BaseException) -> str:
    if isinstance(exc, UsiEngineStartError):
        return exc.failure_phase
    if isinstance(exc, TimeoutError):
        return "think"
    if isinstance(exc, ValueError):
        return "move_validation"
    return "think"


async def _prepare_resource_context(owner: Any, game_spec: _GameSpecPort) -> _ResourceContext:
    pool = owner.instance_pool
    resource_context = _empty_resource_context()

    if pool is None:
        return resource_context

    try:
        resource_context.resource_requirements = _collect_instance_usage_service(
            owner,
            pool,
            game_spec.black_item,
            game_spec.white_item,
        )
        if resource_context.resource_requirements:
            await _await_instance_resources_service(
                owner,
                pool,
                resource_context.resource_requirements,
                game_spec.game_id,
            )
            resource_context.is_slots_reserved = True

        engine_configs = owner.engine_configs
        if not isinstance(engine_configs, Mapping):
            raise AttributeError("engine_configs not initialized on orchestrator")

        resource_context.black_engine_spec, resource_context.white_engine_spec = _resolve_engine_specs(
            engine_configs,
            game_spec,
        )
        black_instance_id = _resolve_instance_id(game_spec.black_item, resource_context.black_engine_spec)
        white_instance_id = _resolve_instance_id(game_spec.white_item, resource_context.white_engine_spec)

        _ensure_instance_registered(pool, black_instance_id)
        _ensure_instance_registered(pool, white_instance_id)

        resource_context.instance_role_map = _build_instance_role_map(
            game_spec,
            resource_context.black_engine_spec,
            resource_context.white_engine_spec,
            black_instance_id=black_instance_id,
            white_instance_id=white_instance_id,
        )
        _record_active_games(
            pool,
            game_spec,
            resource_context.instance_role_map,
            resource_context.black_engine_spec,
            resource_context.white_engine_spec,
        )
    except BaseException:
        try:
            _release_resource_context(pool, game_spec, resource_context)
        except (KeyError, RuntimeError, ValueError) as rollback_exc:
            logger.warning(
                "Resource rollback failed for game %s during setup: %s",
                game_spec.game_id,
                rollback_exc,
                exc_info=True,
            )
        raise

    return resource_context


def _resolve_engine_specs(
    engine_configs: Mapping[str, object], game_spec: _GameSpecPort
) -> tuple[EngineConfig, EngineConfig]:
    black_key = game_spec.black_item.pool_key.split("#", 1)[0]
    white_key = game_spec.white_item.pool_key.split("#", 1)[0]
    if black_key not in engine_configs or white_key not in engine_configs:
        raise KeyError("Engine config missing while recording active games")
    black_spec = engine_configs[black_key]
    white_spec = engine_configs[white_key]
    if not isinstance(black_spec, EngineConfig) or not isinstance(white_spec, EngineConfig):
        raise TypeError("engine_configs entries must be EngineConfig")
    return black_spec, white_spec


def _resolve_instance_id(item: _EngineItemPort, engine_spec: EngineConfig | None) -> str:
    return item.instance_override or (engine_spec.instance_id if engine_spec is not None else None) or "local"


def _ensure_instance_registered(pool: Any, instance_id: str) -> None:
    if pool.get_instance(instance_id) is not None:
        return
    if instance_id == "local":
        pool.ensure_local_instance()
        return
    raise KeyError(f"instance '{instance_id}' is not registered in the instance pool")


def _build_instance_role_map(
    game_spec: _GameSpecPort,
    black_engine_spec: EngineConfig | None,
    white_engine_spec: EngineConfig | None,
    *,
    black_instance_id: str,
    white_instance_id: str,
) -> dict[str, list[InstanceActiveGameSide]]:
    instance_role_map: dict[str, list[InstanceActiveGameSide]] = {}
    black_engine_name = black_engine_spec.name if black_engine_spec is not None else None
    white_engine_name = white_engine_spec.name if white_engine_spec is not None else None
    instance_role_map.setdefault(black_instance_id, []).append(
        InstanceActiveGameSide(
            role="black",
            engine_name=black_engine_name or game_spec.black_item.pool_key,
            pool_key=game_spec.black_item.pool_key,
        )
    )
    instance_role_map.setdefault(white_instance_id, []).append(
        InstanceActiveGameSide(
            role="white",
            engine_name=white_engine_name or game_spec.white_item.pool_key,
            pool_key=game_spec.white_item.pool_key,
        )
    )
    return instance_role_map


def _record_active_games(
    pool: Any,
    game_spec: _GameSpecPort,
    instance_role_map: dict[str, list[InstanceActiveGameSide]],
    black_engine_spec: EngineConfig | None,
    white_engine_spec: EngineConfig | None,
) -> None:
    black_tc_spec = game_spec.black_limits.to_spec_str() if game_spec.black_limits is not None else None
    white_tc_spec = game_spec.white_limits.to_spec_str() if game_spec.white_limits is not None else None

    black_engine_name = (
        black_engine_spec.name or game_spec.black_item.pool_key if black_engine_spec else game_spec.black_item.pool_key
    )
    white_engine_name = (
        white_engine_spec.name or game_spec.white_item.pool_key if white_engine_spec else game_spec.white_item.pool_key
    )

    for inst_id, roles in instance_role_map.items():
        for role in roles:
            pool.record_active_game(
                inst_id,
                game_id=game_spec.game_id,
                black_engine=black_engine_name,
                white_engine=white_engine_name,
                initial_sfen=game_spec.initial_sfen,
                role=role,
                round_index=game_spec.game_round,
                time_control_black=black_tc_spec,
                time_control_white=white_tc_spec,
            )


async def _run_game_with_engines(
    owner: Any,
    game_spec: _GameSpecPort,
    *,
    game_runner: Any,
    black_engine: AsyncUsiEngine,
    white_engine: AsyncUsiEngine,
    black_engine_spec: EngineConfig | None,
    white_engine_spec: EngineConfig | None,
    initial_sfen: str,
) -> rsshogi.record.Record:
    black_pool_key = game_spec.black_item.pool_key
    white_pool_key = game_spec.white_item.pool_key

    name_overrides: dict[str, str] = {}
    if game_spec.before_game_hook is not None:
        engines_by_key: dict[str, AsyncUsiEngine] = {
            black_pool_key: black_engine,
            white_pool_key: white_engine,
        }
        hook_result = await game_spec.before_game_hook.run(BeforeGameHookRequest(engines_by_pool_key=engines_by_key))
        if hook_result is not None:
            name_overrides = hook_result.display_name_overrides

    black_override = name_overrides.get(black_pool_key)
    if not black_override:
        black_override = _pick_display_name(black_engine_spec, black_pool_key, black_engine)

    white_override = name_overrides.get(white_pool_key)
    if not white_override:
        white_override = _pick_display_name(white_engine_spec, white_pool_key, white_engine)

    black_participant = EngineParticipant(black_engine, name_override=black_override, role=Color.BLACK)
    white_participant = EngineParticipant(white_engine, name_override=white_override, role=Color.WHITE)

    started_at = datetime.now(UTC)
    if game_spec.on_game_start is not None:
        await game_spec.on_game_start()

    transcript_context = _build_transcript_context(
        owner,
        game_spec,
        black_engine=black_engine,
        white_engine=white_engine,
        black_name=black_participant.name,
        white_name=white_participant.name,
    )

    if transcript_context is None:
        game_info = await game_runner.run_game(
            black_participant,
            white_participant,
            initial_sfen,
            game_spec.game_id,
            black_time_control_limits=game_spec.black_limits,
            white_time_control_limits=game_spec.white_limits,
        )
    else:
        async with transcript_context:
            game_info = await game_runner.run_game(
                black_participant,
                white_participant,
                initial_sfen,
                game_spec.game_id,
                black_time_control_limits=game_spec.black_limits,
                white_time_control_limits=game_spec.white_limits,
            )

    completed_at = datetime.now(UTC)
    participation_records = _collect_participation_records_local_service(
        owner,
        black_engine=black_engine,
        white_engine=white_engine,
        black_participant=black_participant,
        white_participant=white_participant,
        black_spec=black_engine_spec,
        white_spec=white_engine_spec,
        black_pool_key=game_spec.black_item.pool_key,
        white_pool_key=game_spec.white_item.pool_key,
        started_at=started_at,
        completed_at=completed_at,
    )
    _attach_participation_metadata_service(
        game_record=game_info,
        participation_records=participation_records,
    )
    attach_schedule_metadata(
        game_record=game_info,
        schedule_metadata=getattr(game_spec, "schedule_metadata", None),
    )
    return game_info


def _build_transcript_context(
    owner: Any,
    game_spec: _GameSpecPort,
    *,
    black_engine: AsyncUsiEngine,
    white_engine: AsyncUsiEngine,
    black_name: str,
    white_name: str,
) -> GameUsiTranscriptContext | None:
    config = getattr(owner, "config", None)
    logging_config = getattr(config, "logging", None)
    is_enabled = bool(getattr(logging_config, "is_usi_transcript_enabled", False))
    if not is_enabled:
        return None
    detail_raw = getattr(logging_config, "usi_transcript_detail", "commands")
    detail: TranscriptDetail = "commands_and_info" if detail_raw == "commands_and_info" else "commands"
    max_bytes = int(getattr(logging_config, "usi_transcript_max_bytes", DEFAULT_TRANSCRIPT_MAX_BYTES))
    run_dir = getattr(owner, "run_dir", None)
    if not isinstance(run_dir, Path):
        raise RuntimeError("USI transcript mode requires a Path run_dir")
    return GameUsiTranscriptContext(
        run_dir=run_dir,
        game_id=game_spec.game_id,
        initial_sfen=game_spec.initial_sfen,
        black_engine=black_engine,
        white_engine=white_engine,
        black_name=black_name,
        white_name=white_name,
        detail=detail,
        max_bytes=max_bytes,
    )


def _pick_display_name(spec_model: EngineConfig | None, pool_key: str, engine: AsyncUsiEngine) -> str:
    spec_name = spec_model.name if spec_model is not None else None
    if isinstance(spec_name, str) and spec_name:
        return spec_name
    if "#" in pool_key:
        return pool_key.split("#", 1)[0]
    return engine.name


async def _cleanup_execution(
    owner: Any,
    game_spec: _GameSpecPort,
    *,
    engine_pool: Any,
    black_engine: AsyncUsiEngine | None,
    white_engine: AsyncUsiEngine | None,
    resource_context: _ResourceContext,
    black_contract_digest: str | None,
    white_contract_digest: str | None,
) -> None:
    if black_engine is not None and white_engine is not None:
        try:
            await engine_pool.release(
                game_spec.white_item.pool_key,
                white_engine,
                game_spec.white_item.instance_override,
                contract_digest=white_contract_digest,
            )
        finally:
            await engine_pool.release(
                game_spec.black_item.pool_key,
                black_engine,
                game_spec.black_item.instance_override,
                contract_digest=black_contract_digest,
            )

    pool = owner.instance_pool
    if pool is None:
        return
    _release_resource_context(pool, game_spec, resource_context)


def _release_resource_context(pool: Any, game_spec: _GameSpecPort, resource_context: _ResourceContext) -> None:
    if resource_context.is_slots_reserved and resource_context.resource_requirements:
        pool.release_resources(resource_context.resource_requirements)
        resource_context.is_slots_reserved = False
    if resource_context.instance_role_map:
        for inst_id in resource_context.instance_role_map:
            pool.clear_active_game(inst_id, game_spec.game_id)
        resource_context.instance_role_map = {}


__all__ = ["execute_game"]
