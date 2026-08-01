"""Remote instance lifecycle helpers extracted from TournamentOrchestrator."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from shogiarena._core.contexts.game_session.adapters.orchestration.resource_control import (
    await_instance_resources as _await_instance_resources_service,
)
from shogiarena._core.contexts.game_session.adapters.orchestration.resource_control import (
    collect_instance_usage as _collect_instance_usage_service,
)
from shogiarena._core.contexts.instances.application.instance_models import InstanceActiveGameSide
from shogiarena._core.contexts.instances.application.instance_pool import ResourceRequest
from shogiarena._core.shared.kernel.time_control import TimeControlLimits


@asynccontextmanager
async def manage_remote_pair_instance_lifecycle(
    orchestrator: Any,
    *,
    game_id: str,
    initial_sfen: str,
    round_index: int | None,
    instance_id: str,
    black_engine_name: str,
    white_engine_name: str,
    black_pool_key: str,
    white_pool_key: str,
    black_item: Any,
    white_item: Any,
    black_limits: TimeControlLimits | None,
    white_limits: TimeControlLimits | None,
) -> AsyncIterator[None]:
    """Reserve resources, mark active-game state, and release state on exit."""

    owner = orchestrator
    pool = owner.instance_pool
    resource_requirements: dict[str, ResourceRequest] = {}
    is_slots_reserved = False
    is_active_recorded = False

    if pool is not None and instance_id:
        configured_usage = _collect_instance_usage_service(owner, pool, black_item, white_item)
        if configured_usage:
            resource_requirements = {
                instance_id: ResourceRequest(
                    slots=sum(request.slots for request in configured_usage.values()),
                    engines=sum(request.engines for request in configured_usage.values()),
                )
            }
            await _await_instance_resources_service(owner, pool, resource_requirements, game_id)
            is_slots_reserved = True

        black_tc_spec = black_limits.to_spec_str() if black_limits is not None else None
        white_tc_spec = white_limits.to_spec_str() if white_limits is not None else None
        recorded_roles = [
            InstanceActiveGameSide(role="black", engine_name=black_engine_name, pool_key=black_pool_key),
            InstanceActiveGameSide(role="white", engine_name=white_engine_name, pool_key=white_pool_key),
        ]
        for role in recorded_roles:
            pool.record_active_game(
                instance_id,
                game_id=game_id,
                black_engine=black_engine_name,
                white_engine=white_engine_name,
                initial_sfen=initial_sfen,
                role=role,
                round_index=round_index,
                time_control_black=black_tc_spec,
                time_control_white=white_tc_spec,
            )
            is_active_recorded = True

    try:
        yield
    finally:
        if pool is not None and instance_id:
            if is_active_recorded:
                pool.clear_active_game(instance_id, game_id)
            if is_slots_reserved and resource_requirements:
                pool.release_resources(resource_requirements)


__all__ = ["manage_remote_pair_instance_lifecycle"]
