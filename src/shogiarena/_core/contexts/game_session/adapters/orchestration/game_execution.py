"""Game execution helper extracted from BaseOrchestrator."""

from __future__ import annotations

import asyncio
import logging
import sys
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, cast

import rshogi.record
from rshogi.types import Color

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
    GameUsiTranscriptContext,
    TranscriptDetail,
)
from shogiarena._core.contexts.game_session.application.session.run_failure_record_service import (
    RunFailureRecordService,
)
from shogiarena._core.contexts.game_session.domain.failure_records import coerce_failure_phase
from shogiarena._core.contexts.game_session.ports.session_runner_ports import BeforeGameHookPort, BeforeGameHookRequest
from shogiarena._core.contexts.instances.application.instance_models import InstanceActiveGameSide
from shogiarena._core.contexts.instances.application.instance_pool import ResourceRequest
from shogiarena._core.contexts.match.application.engine_participant import EngineParticipant
from shogiarena._core.platform.engine_runtime.usi_engine_session import AsyncUsiEngine
from shogiarena._core.platform.engine_runtime.usi_engine_session_models import UsiEngineStartError
from shogiarena._core.shared.kernel.json_types import JsonObject

from .config_engine import EngineConfig

logger = logging.getLogger(__name__)


class _EngineItemPort(Protocol):
    pool_key: str
    config_path: object
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
    before_game_hook: BeforeGameHookPort | None
    game_round: int | None
    on_game_start: Callable[[], Awaitable[None]] | None


@dataclass(slots=True)
class _ResourceContext:
    resource_requirements: dict[str, ResourceRequest]
    is_slots_reserved: bool
    instance_role_map: dict[str, list[InstanceActiveGameSide]]
    black_engine_spec: EngineConfig | None
    white_engine_spec: EngineConfig | None


async def execute_game(orchestrator: Any, spec: Any) -> rshogi.record.GameRecord:
    """Acquire engines, run one game, and release resources."""

    owner = orchestrator
    game_spec = cast(_GameSpecPort, spec)
    ep = owner.engine_pool
    assert ep is not None, "EnginePool not initialized"
    gr = owner.game_runner
    assert gr is not None, "GameRunner not initialized"

    resource_context = await _prepare_resource_context(owner, game_spec)

    b_tuple = _build_engine_tuple(game_spec.black_item)
    w_tuple = _build_engine_tuple(game_spec.white_item)

    black_engine: AsyncUsiEngine | None = None
    white_engine: AsyncUsiEngine | None = None

    try:
        black_engine, white_engine = await ep.acquire_pair_sorted(b_tuple, w_tuple)
        return await _run_game_with_engines(
            owner,
            game_spec,
            game_runner=gr,
            black_engine=black_engine,
            white_engine=white_engine,
            black_engine_spec=resource_context.black_engine_spec,
            white_engine_spec=resource_context.white_engine_spec,
        )
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


def _build_engine_tuple(item: _EngineItemPort) -> tuple[str, object, object, str | None]:
    return (
        item.pool_key,
        item.config_path,
        item.extra_options,
        item.instance_override,
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
    resource_requirements: dict[str, ResourceRequest] = {}
    is_slots_reserved = False
    instance_role_map: dict[str, list[InstanceActiveGameSide]] = {}
    black_engine_spec: EngineConfig | None = None
    white_engine_spec: EngineConfig | None = None

    if pool is None:
        return _ResourceContext(
            resource_requirements=resource_requirements,
            is_slots_reserved=is_slots_reserved,
            instance_role_map=instance_role_map,
            black_engine_spec=black_engine_spec,
            white_engine_spec=white_engine_spec,
        )

    resource_requirements = _collect_instance_usage_service(owner, pool, game_spec.black_item, game_spec.white_item)
    if resource_requirements:
        await _await_instance_resources_service(owner, pool, resource_requirements, game_spec.game_id)
        is_slots_reserved = True

    engine_configs = owner.engine_configs
    if not isinstance(engine_configs, Mapping):
        raise AttributeError("engine_configs not initialized on orchestrator")

    black_engine_spec, white_engine_spec = _resolve_engine_specs(engine_configs, game_spec)
    black_instance_id = _resolve_instance_id(game_spec.black_item, black_engine_spec)
    white_instance_id = _resolve_instance_id(game_spec.white_item, white_engine_spec)

    _ensure_instance_registered(pool, black_instance_id)
    _ensure_instance_registered(pool, white_instance_id)

    instance_role_map = _build_instance_role_map(
        game_spec,
        black_engine_spec,
        white_engine_spec,
        black_instance_id=black_instance_id,
        white_instance_id=white_instance_id,
    )
    _record_active_games(pool, game_spec, instance_role_map, black_engine_spec, white_engine_spec)

    return _ResourceContext(
        resource_requirements=resource_requirements,
        is_slots_reserved=is_slots_reserved,
        instance_role_map=instance_role_map,
        black_engine_spec=black_engine_spec,
        white_engine_spec=white_engine_spec,
    )


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
) -> rshogi.record.GameRecord:
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
            game_spec.initial_sfen,
            game_spec.game_id,
            black_time_control_limits=game_spec.black_limits,
            white_time_control_limits=game_spec.white_limits,
        )
    else:
        async with transcript_context:
            game_info = await game_runner.run_game(
                black_participant,
                white_participant,
                game_spec.initial_sfen,
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
) -> None:
    if black_engine is not None and white_engine is not None:
        try:
            await engine_pool.release(
                game_spec.white_item.pool_key,
                white_engine,
                game_spec.white_item.instance_override,
            )
        finally:
            await engine_pool.release(
                game_spec.black_item.pool_key,
                black_engine,
                game_spec.black_item.instance_override,
            )

    pool = owner.instance_pool
    if pool is None:
        return
    if resource_context.is_slots_reserved and resource_context.resource_requirements:
        pool.release_resources(resource_context.resource_requirements)
    if resource_context.instance_role_map:
        for inst_id in resource_context.instance_role_map:
            pool.clear_active_game(inst_id, game_spec.game_id)


__all__ = ["execute_game"]
