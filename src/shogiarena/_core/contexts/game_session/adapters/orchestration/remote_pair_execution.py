"""Shared remote pair execution helper extracted from orchestrators."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import rsshogi.record

from shogiarena._core.contexts.game_session.adapters.orchestration.game_execution_manifest import (
    ensure_remote_logical_job_key,
    persist_remote_game_assignment,
)
from shogiarena._core.contexts.game_session.adapters.orchestration.game_execution_materializer import (
    materialize_opening_sfen,
)
from shogiarena._core.contexts.game_session.adapters.orchestration.remote_executor import (
    RemoteExecutor,
)
from shogiarena._core.contexts.game_session.application.orchestration.remote_move_aggregation import (
    extract_final_game_result,
    update_remote_move_aggregates,
)
from shogiarena._core.contexts.game_session.application.progress.payload_parser import enqueue_progress_event
from shogiarena._core.contexts.game_session.ports.game_execution_spec import (
    GAME_RESULT_SCHEMA_VERSION,
    GameExecutionResult,
    GameExecutionSpec,
)
from shogiarena._core.platform.engine_provisioning.remote_game_record_builder import build_remote_game_info
from shogiarena._core.shared.kernel.game_results import game_result_name
from shogiarena._core.shared.kernel.json_types import JsonObject
from shogiarena._core.shared.kernel.time_control import TimeControlLimits


@dataclass(frozen=True)
class RemotePairGameExecutionResult:
    """Result bundle returned from one remote pair execution."""

    game_info: rsshogi.record.Record
    started_at: datetime
    completed_at: datetime
    endpoint_identity: str
    deployment_id: str
    job_id: str
    attempt_id: str
    worker_result: GameExecutionResult


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

    def persist_assignment(assignment: JsonObject) -> None:
        persist_remote_game_assignment(
            run_dir=orchestrator.run_dir,
            game_id=game_id,
            assignment=assignment,
        )

    logical_job_key = ensure_remote_logical_job_key(
        run_dir=orchestrator.run_dir,
        game_id=game_id,
    )
    outcome = await executor.run_remote_pair(
        remote_root=remote_root,
        spec=prepared_spec,
        logical_job_key=logical_job_key,
        on_event=on_event,
        on_assignment=persist_assignment,
    )
    events = outcome.events

    final_result = extract_final_game_result(events)
    sealed_spec = GameExecutionSpec.model_validate(prepared_spec)
    result_envelope = _extract_result_envelope(events)
    if result_envelope.execution_digest != sealed_spec.execution_digest:
        raise RuntimeError("Remote result execution_digest does not match dispatched GameExecutionSpec")
    if result_envelope.game_id != game_id:
        raise RuntimeError("Remote result game_id does not match dispatched game")
    if result_envelope.classification != game_result_name(final_result):
        raise RuntimeError("Remote result classification does not match terminal move_progress")
    missing_provenance = sorted(set(sealed_spec.output.required_provenance) - set(result_envelope.provenance))
    if missing_provenance:
        raise RuntimeError(f"Remote result is missing required provenance: {', '.join(missing_provenance)}")
    game_info = build_remote_game_info(
        start_sfen=materialize_opening_sfen(sealed_spec.opening),
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
    return RemotePairGameExecutionResult(
        game_info=game_info,
        started_at=outcome.coordinator_started_at,
        completed_at=outcome.coordinator_completed_at,
        endpoint_identity=outcome.endpoint_identity,
        deployment_id=outcome.deployment_id,
        job_id=outcome.identity.job_id,
        attempt_id=outcome.identity.attempt_id,
        worker_result=outcome.worker_result,
    )


def _extract_result_envelope(events: list[JsonObject]) -> GameExecutionResult:
    payload = next(
        (event for event in reversed(events) if event.get("schema_version") == GAME_RESULT_SCHEMA_VERSION),
        None,
    )
    if payload is None:
        raise RuntimeError("Remote game did not produce a typed GameExecutionResult")
    return GameExecutionResult.model_validate(payload)


__all__ = ["execute_remote_pair_game"]
