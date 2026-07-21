"""Remote pair-game execution helper for the tournament orchestrator."""

from __future__ import annotations

import logging
from typing import Any

import rsshogi

from shogiarena._core.contexts.game_session.adapters.orchestration.config_engine import EngineConfig
from shogiarena._core.contexts.game_session.adapters.orchestration.contracts_base_orchestrator import BaseOrchestrator
from shogiarena._core.contexts.game_session.adapters.orchestration.participation_records import (
    attach_participation_metadata as _attach_participation_metadata_service,
)
from shogiarena._core.contexts.game_session.adapters.orchestration.participation_records import (
    collect_participation_records_remote_pair as _collect_participation_records_remote_pair_service,
)
from shogiarena._core.contexts.game_session.adapters.orchestration.remote_control import (
    prepare_remote_game_spec as _prepare_remote_game_spec_service,
)
from shogiarena._core.contexts.game_session.adapters.orchestration.remote_lifecycle import (
    manage_remote_pair_instance_lifecycle as _manage_remote_pair_instance_lifecycle_service,
)
from shogiarena._core.contexts.game_session.adapters.orchestration.remote_options import (
    build_remote_pair_option_context,
)
from shogiarena._core.contexts.game_session.adapters.orchestration.remote_pair_execution import (
    execute_remote_pair_game as _execute_remote_pair_game_service,
)
from shogiarena._core.contexts.instances.application.instance_models import Instance
from shogiarena._core.contexts.instances.ports.orchestrator_primitives import (
    max_plies_from_rules,
)
from shogiarena._core.contexts.tournament.domain.tournament_models import GameSpec
from shogiarena._core.shared.kernel.json_coercion import coerce_json_object_serialized
from shogiarena._core.shared.kernel.schedule_metadata import attach_schedule_metadata
from shogiarena._core.shared.kernel.time_control import TimeControlLimits

logger = logging.getLogger(__name__)


async def run_remote_tournament_game(
    *,
    orchestrator: Any,
    game_spec: GameSpec,
    black_limits: TimeControlLimits,
    white_limits: TimeControlLimits,
    remote_instance: Instance,
    black_item: BaseOrchestrator.EngineGameSpec,
    white_item: BaseOrchestrator.EngineGameSpec,
    engine_configs: dict[str, EngineConfig],
    extra_options: Any,
    rules: Any,
) -> rsshogi.record.Record:
    """Run one scheduled game with both engines on the same SSH instance."""
    instance_id = remote_instance.name

    black_name = str(game_spec.black_engine)
    white_name = str(game_spec.white_engine)
    option_context = build_remote_pair_option_context(
        engine_configs=engine_configs,
        extra_options=extra_options,
        black_engine_name=black_name,
        white_engine_name=white_name,
    )
    black_cfg_spec = option_context.black_spec
    white_cfg_spec = option_context.white_spec

    max_plies = max_plies_from_rules(rules)
    prepared_spec = None
    remote_result = None

    logger.debug("[%s] start game %s: %s vs %s", remote_instance.name, game_spec.game_id, black_name, white_name)

    async with _manage_remote_pair_instance_lifecycle_service(
        orchestrator,
        game_id=game_spec.game_id,
        initial_sfen=game_spec.initial_sfen,
        round_index=game_spec.round_num,
        instance_id=instance_id,
        black_engine_name=black_name,
        white_engine_name=white_name,
        black_pool_key=black_item.pool_key,
        white_pool_key=white_item.pool_key,
        black_item=black_item,
        white_item=white_item,
        black_limits=black_limits,
        white_limits=white_limits,
    ):
        executor, remote_root, prepared_spec = await _prepare_remote_game_spec_service(
            orchestrator,
            remote_instance=remote_instance,
            black_config_path=black_item.config_path,
            white_config_path=white_item.config_path,
            black_options=option_context.black_options,
            white_options=option_context.white_options,
            start_sfen=game_spec.initial_sfen,
            game_id=game_spec.game_id,
            black_name=black_name,
            white_name=white_name,
            black_limits=black_limits,
            white_limits=white_limits,
            max_plies=max_plies,
            engine_factory_service=orchestrator._engine_factory_service,
        )

        remote_result = await _execute_remote_pair_game_service(
            orchestrator,
            executor=executor,
            remote_root=remote_root,
            prepared_spec=prepared_spec,
            game_id=game_spec.game_id,
            start_sfen=game_spec.initial_sfen,
            black_name=black_name,
            white_name=white_name,
            black_limits=black_limits,
            white_limits=white_limits,
            should_allow_default_str=True,
        )

    if prepared_spec is None:
        raise RuntimeError("Remote game spec was not prepared")
    if remote_result is None:
        raise RuntimeError("Remote pair game execution result is missing")

    game_info = remote_result.game_info
    participation_records = _collect_participation_records_remote_pair_service(
        orchestrator,
        spec_payload=prepared_spec if isinstance(prepared_spec, dict) else None,
        black_engine_name=black_name,
        white_engine_name=white_name,
        black_spec=black_cfg_spec,
        white_spec=white_cfg_spec,
        black_pool_key=black_item.pool_key,
        white_pool_key=white_item.pool_key,
        instance_id=instance_id,
        started_at=remote_result.started_at,
        completed_at=remote_result.completed_at,
    )
    _attach_participation_metadata_service(
        game_record=game_info,
        participation_records=participation_records,
    )
    attach_schedule_metadata(
        game_record=game_info,
        schedule_metadata=coerce_json_object_serialized(
            game_spec.to_schedule_metadata(),
            field_name="arena_schedule",
        ),
    )

    logger.debug(
        "[%s] end game %s: result=%s",
        remote_instance.name,
        game_spec.game_id,
        game_info.result.value,
    )
    return game_info
