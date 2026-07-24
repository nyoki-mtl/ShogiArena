"""Shared remote pair execution helper extracted from orchestrators."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import rsshogi.record

from shogiarena._core.contexts.game_session.adapters.orchestration.remote_executor import RemoteExecutor
from shogiarena._core.contexts.game_session.application.orchestration.remote_move_aggregation import (
    extract_final_game_result,
    update_remote_move_aggregates,
)
from shogiarena._core.contexts.game_session.application.progress.payload_parser import enqueue_progress_event
from shogiarena._core.platform.engine_provisioning.remote_game_record_builder import build_remote_game_info
from shogiarena._core.shared.kernel.json_types import JsonObject
from shogiarena._core.shared.kernel.time_control import TimeControlLimits


@dataclass(frozen=True)
class RemotePairGameExecutionResult:
    """Result bundle returned from one remote pair execution."""

    game_info: rsshogi.record.Record
    started_at: datetime
    completed_at: datetime


async def execute_remote_pair_game(
    orchestrator: Any,
    *,
    executor: RemoteExecutor,
    remote_root: str,
    prepared_spec: JsonObject,
    game_id: str,
    start_sfen: str,
    black_name: str,
    white_name: str,
    black_limits: TimeControlLimits,
    white_limits: TimeControlLimits,
    should_allow_default_str: bool = False,
) -> RemotePairGameExecutionResult:
    """Execute remote pair runner, stream progress events, and build a game record."""

    owner = orchestrator
    progress_q = owner.progress_sink
    last_ply_seen = 0
    agg_moves: list[str] = []
    agg_move_times: list[int | None] = []
    agg_wall_times: list[int | None] = []
    agg_engine_wall_times: list[int | None] = []
    agg_nodes: list[int | None] = []
    agg_depth: list[int | None] = []
    agg_seldepth: list[int | None] = []
    agg_evals: list[int | None] = []

    def on_event(ev: JsonObject) -> None:
        nonlocal last_ply_seen
        last_ply_seen = update_remote_move_aggregates(
            ev,
            last_ply_seen=last_ply_seen,
            moves=agg_moves,
            evals=agg_evals,
            nodes=agg_nodes,
            depth=agg_depth,
            seldepth=agg_seldepth,
            move_times=agg_move_times,
            wall_times=agg_wall_times,
            engine_wall_times=agg_engine_wall_times,
        )
        enqueue_progress_event(
            progress_q,
            game_id,
            ev,
            fallback_move_count=last_ply_seen,
            should_allow_default_str=should_allow_default_str,
        )

    started_at = datetime.now(UTC)
    events = await executor.run_remote_pair(
        remote_root=remote_root,
        spec=prepared_spec,
        on_event=on_event,
    )

    final_result = extract_final_game_result(events)
    game_info = build_remote_game_info(
        start_sfen=start_sfen,
        moves=agg_moves,
        game_result=final_result,
        game_id=game_id,
        black_name=black_name,
        white_name=white_name,
        black_limits=black_limits,
        white_limits=white_limits,
        move_times=agg_move_times,
        wall_times=agg_wall_times,
        engine_wall_times=agg_engine_wall_times,
        nodes=agg_nodes,
        depth=agg_depth,
        seldepth=agg_seldepth,
        evals=agg_evals,
    )
    completed_at = datetime.now(UTC)
    return RemotePairGameExecutionResult(
        game_info=game_info,
        started_at=started_at,
        completed_at=completed_at,
    )


__all__ = ["execute_remote_pair_game"]
