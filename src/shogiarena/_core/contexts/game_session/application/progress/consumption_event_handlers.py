"""Progress event handlers used by the consumption loop."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from shogiarena._core.contexts.game_session.application.progress.events import (
    ClockIncrementEvent,
    ClockStartEvent,
    EngineIoBatchEvent,
    EngineIoEvent,
    EngineStateEvent,
    GameAssignedEvent,
    HandshakeLogEvent,
    MoveProgressEvent,
)
from shogiarena._core.contexts.game_session.application.progress.model_mutations import (
    apply_clock_increment_model,
    apply_clock_start_model,
    apply_game_assigned_model,
    apply_move_progress_model,
    ensure_engine_status_model,
    update_engine_status_model_entry,
)
from shogiarena._core.contexts.game_session.application.progress.snapshot_models import (
    WorkerSnapshotModel,
    snapshot_from_progress_event,
    to_worker_snapshot_model,
)
from shogiarena._core.contexts.game_session.application.progress.snapshot_normalizer import (
    normalize_role as _snapshot_normalize_role,
)
from shogiarena._core.contexts.game_session.application.progress.snapshot_payload_builders import (
    to_game_assigned_payload,
    to_worker_snapshot_dto,
    to_ws_moves_diff,
    to_ws_state_diff,
)
from shogiarena._core.shared.kernel.json_coercion import to_json_object
from shogiarena._core.shared.kernel.live_stream_payloads import LiveStreamDiffPayload
from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_int, coerce_str

if TYPE_CHECKING:
    from shogiarena._core.contexts.game_session.application.progress.consumption import (
        ProgressApiServerPort,
        ProgressState,
    )

logger = logging.getLogger(__name__)

ProgressEvent = (
    MoveProgressEvent
    | ClockStartEvent
    | ClockIncrementEvent
    | GameAssignedEvent
    | HandshakeLogEvent
    | EngineIoEvent
    | EngineIoBatchEvent
    | EngineStateEvent
)


def _normalized_engine_role(raw_role: object) -> str | None:
    return _snapshot_normalize_role(coerce_str(raw_role))


def _engine_state_before_update(snapshot: WorkerSnapshotModel, normalized_role: str | None) -> str | None:
    if normalized_role is None:
        return None
    status = ensure_engine_status_model(snapshot)
    return status[normalized_role].state


def _should_emit_engine_status_diff(
    snapshot: WorkerSnapshotModel,
    *,
    normalized_role: str | None,
    previous_state: str | None,
    incoming_state: str | None,
) -> bool:
    if normalized_role is None or incoming_state is None:
        return False
    status = ensure_engine_status_model(snapshot)
    return status[normalized_role].state != previous_state


def _initialize_snapshot(
    state: ProgressState,
    *,
    worker_idx: int,
    current_gen: int,
    progress: ProgressEvent,
    event_name: str,
    game_id: int,
    default_name: str,
) -> WorkerSnapshotModel | None:
    snapshot = to_worker_snapshot_model(state.worker_snapshots.get(worker_idx))
    if snapshot is None:
        try:
            snapshot = snapshot_from_progress_event(progress, generation=current_gen, name_default=default_name)
        except (ValueError, TypeError, RuntimeError) as exc:
            logger.warning(
                "Dropped %s missing initial_sfen (worker=%s game_id=%s): %s",
                event_name,
                worker_idx,
                progress.get("game_id"),
                exc,
            )
            return None
        state.worker_snapshots[worker_idx] = snapshot
    else:
        state.worker_snapshots[worker_idx] = snapshot
    if snapshot.generation is not None and snapshot.generation != current_gen:
        logger.debug(
            "Skipped stale %s event for worker=%s game_id=%s (snapshot generation=%s expected=%s)",
            event_name,
            worker_idx,
            game_id,
            snapshot.generation,
            current_gen,
        )
        return None
    return snapshot


def handle_move_progress(
    *,
    worker_idx: int,
    current_gen: int,
    progress: MoveProgressEvent,
    state: ProgressState,
    game_id_num: int,
) -> LiveStreamDiffPayload | None:
    snapshot = _initialize_snapshot(
        state,
        worker_idx=worker_idx,
        current_gen=current_gen,
        progress=progress,
        event_name="move_progress",
        game_id=game_id_num,
        default_name="Unknown",
    )
    if snapshot is None:
        return None
    apply_move_progress_model(snapshot, progress)
    return to_ws_state_diff(progress, snapshot)


def handle_clock_start(
    *,
    worker_idx: int,
    current_gen: int,
    progress: ClockStartEvent,
    state: ProgressState,
    game_id_num: int,
) -> LiveStreamDiffPayload | None:
    snapshot = _initialize_snapshot(
        state,
        worker_idx=worker_idx,
        current_gen=current_gen,
        progress=progress,
        event_name="clock_start",
        game_id=game_id_num,
        default_name="",
    )
    if snapshot is None:
        return None
    apply_clock_start_model(snapshot, progress)
    return to_ws_state_diff(progress, snapshot)


def handle_clock_increment(
    *,
    worker_idx: int,
    current_gen: int,
    progress: ClockIncrementEvent,
    state: ProgressState,
    game_id_num: int,
) -> LiveStreamDiffPayload | None:
    snapshot = _initialize_snapshot(
        state,
        worker_idx=worker_idx,
        current_gen=current_gen,
        progress=progress,
        event_name="clock_increment",
        game_id=game_id_num,
        default_name="",
    )
    if snapshot is None:
        return None
    apply_clock_increment_model(snapshot, progress)
    return to_ws_state_diff(progress, snapshot)


def handle_game_assigned(
    *,
    worker_idx: int,
    current_gen: int,
    progress: GameAssignedEvent,
    state: ProgressState,
    game_id_num: int,
    api_server: ProgressApiServerPort | None,
) -> LiveStreamDiffPayload | None:
    snapshot = _initialize_snapshot(
        state,
        worker_idx=worker_idx,
        current_gen=current_gen,
        progress=progress,
        event_name="game_assigned",
        game_id=game_id_num,
        default_name="",
    )
    if snapshot is None:
        return None
    apply_game_assigned_model(snapshot, progress, generation=current_gen)

    if api_server is not None:
        try:
            snapshot_payload = to_json_object(to_worker_snapshot_dto(snapshot))
            api_server.assign_worker_snapshot(worker_idx, snapshot_payload)
        except (OSError, RuntimeError, ValueError) as exc:
            logger.debug("Failed to assign worker snapshot for %s: %s", worker_idx, exc, exc_info=True)
        return None

    return to_game_assigned_payload(progress, snapshot)


def handle_handshake_log(
    *,
    worker_idx: int,
    current_gen: int,
    progress: HandshakeLogEvent,
    state: ProgressState,
    game_id_num: int,
) -> LiveStreamDiffPayload | None:
    snapshot = _initialize_snapshot(
        state,
        worker_idx=worker_idx,
        current_gen=current_gen,
        progress=progress,
        event_name="handshake_log",
        game_id=game_id_num,
        default_name="",
    )
    if snapshot is None:
        return None
    normalized_role = _normalized_engine_role(progress.get("role"))
    previous_state = _engine_state_before_update(snapshot, normalized_role)
    incoming_state = coerce_str(progress.get("state"))
    updated = update_engine_status_model_entry(
        snapshot,
        role=progress.get("role"),
        direction=progress.get("direction"),
        line=progress.get("line"),
        state=progress.get("state"),
        timestamp=coerce_int(progress.get("ts")),
    )
    if not updated:
        return None
    if not _should_emit_engine_status_diff(
        snapshot,
        normalized_role=normalized_role,
        previous_state=previous_state,
        incoming_state=incoming_state,
    ):
        return None
    return to_ws_moves_diff(progress, snapshot)


def handle_engine_io(
    *,
    worker_idx: int,
    current_gen: int,
    progress: EngineIoEvent,
    state: ProgressState,
    game_id_num: int,
) -> LiveStreamDiffPayload | None:
    snapshot = _initialize_snapshot(
        state,
        worker_idx=worker_idx,
        current_gen=current_gen,
        progress=progress,
        event_name="engine_io",
        game_id=game_id_num,
        default_name="",
    )
    if snapshot is None:
        return None
    if progress.get("game_id") is not None:
        snapshot_gid = snapshot.game_id or ""
        progress_gid = coerce_str(progress.get("game_id")) or ""
        if snapshot_gid and progress_gid and snapshot_gid != progress_gid:
            try:
                snapshot = snapshot_from_progress_event(progress, generation=current_gen, name_default="")
            except (ValueError, TypeError, RuntimeError) as exc:
                logger.warning(
                    "Dropped engine_io missing initial_sfen (worker=%s game_id=%s): %s",
                    worker_idx,
                    progress.get("game_id"),
                    exc,
                )
                return None
            state.worker_snapshots[worker_idx] = snapshot
    normalized_role = _normalized_engine_role(progress.get("role"))
    previous_state = _engine_state_before_update(snapshot, normalized_role)
    incoming_state = coerce_str(progress.get("state"))
    updated = update_engine_status_model_entry(
        snapshot,
        role=progress.get("role"),
        direction=progress.get("direction"),
        line=progress.get("line"),
        state=progress.get("state"),
        timestamp=coerce_int(progress.get("ts")),
    )
    if not updated:
        return None
    if not _should_emit_engine_status_diff(
        snapshot,
        normalized_role=normalized_role,
        previous_state=previous_state,
        incoming_state=incoming_state,
    ):
        return None
    return to_ws_moves_diff(progress, snapshot)


def handle_engine_state(
    *,
    worker_idx: int,
    current_gen: int,
    progress: EngineStateEvent,
    state: ProgressState,
    game_id_num: int,
) -> LiveStreamDiffPayload | None:
    """engine lifecycle 由来の ``engine_state`` で engine_status バッジを更新する（0046）。

    raw I/O 行から独立した per-transition イベントを唯一のバッジ更新源とする。line を持たないため
    io_tail は変化させず、状態遷移があった時だけ ``engine_status.diff`` を返す。
    """
    snapshot = _initialize_snapshot(
        state,
        worker_idx=worker_idx,
        current_gen=current_gen,
        progress=progress,
        event_name="engine_state",
        game_id=game_id_num,
        default_name="",
    )
    if snapshot is None:
        return None
    if progress.get("game_id") is not None:
        snapshot_gid = snapshot.game_id or ""
        progress_gid = coerce_str(progress.get("game_id")) or ""
        if snapshot_gid and progress_gid and snapshot_gid != progress_gid:
            try:
                snapshot = snapshot_from_progress_event(progress, generation=current_gen, name_default="")
            except (ValueError, TypeError, RuntimeError) as exc:
                logger.warning(
                    "Dropped engine_state missing initial_sfen (worker=%s game_id=%s): %s",
                    worker_idx,
                    progress.get("game_id"),
                    exc,
                )
                return None
            state.worker_snapshots[worker_idx] = snapshot
    role = progress["role"]
    normalized_role = _normalized_engine_role(role)
    previous_state = _engine_state_before_update(snapshot, normalized_role)
    incoming_state = coerce_str(progress.get("state"))
    # Only lifecycle-significant commands (e.g. ``usinewgame``) ride along here; raw ``info`` lines do
    # not, so the io_tail stays small while the card UI keeps its kickoff signal.
    updated = update_engine_status_model_entry(
        snapshot,
        role=role,
        direction=progress.get("direction"),
        line=progress.get("line"),
        state=progress.get("state"),
        timestamp=coerce_int(progress.get("ts")),
    )
    if not updated:
        return None
    if not _should_emit_engine_status_diff(
        snapshot,
        normalized_role=normalized_role,
        previous_state=previous_state,
        incoming_state=incoming_state,
    ):
        return None
    diff_event: EngineIoEvent = {"type": "engine_io", "role": role}
    game_id_value = coerce_str(progress.get("game_id"))
    if game_id_value is not None:
        diff_event["game_id"] = game_id_value
    if incoming_state is not None:
        diff_event["state"] = incoming_state
    return to_ws_moves_diff(diff_event, snapshot)


def sync_worker_generation_for_incoming_event(
    *,
    worker_idx: int,
    progress: ProgressEvent,
    state: ProgressState,
) -> int:
    incoming_gid = progress.get("game_id")
    snap0 = state.worker_snapshots.get(worker_idx)
    existing_gid = snap0.game_id if isinstance(snap0, WorkerSnapshotModel) else None
    if incoming_gid and existing_gid and str(incoming_gid) != str(existing_gid):
        current_gen = state.worker_generation.get(worker_idx, 0) + 1
        state.worker_generation[worker_idx] = current_gen
        try:
            state.worker_snapshots[worker_idx] = snapshot_from_progress_event(
                progress,
                generation=current_gen,
                name_default="",
            )
        except (ValueError, RuntimeError, TypeError) as exc:
            logger.warning(
                "Dropped progress event missing initial_sfen (worker=%s game_id=%s): %s",
                worker_idx,
                incoming_gid,
                exc,
            )
            raise RuntimeError("invalid progress payload") from exc
        logger.debug(
            "Worker %s: game_id changed (%s -> %s); reset snapshot and generation to %s",
            worker_idx,
            existing_gid,
            incoming_gid,
            current_gen,
        )
        return current_gen
    return state.worker_generation.get(worker_idx, 0)


__all__ = [
    "handle_clock_increment",
    "handle_clock_start",
    "handle_engine_io",
    "handle_engine_state",
    "handle_game_assigned",
    "handle_handshake_log",
    "handle_move_progress",
    "sync_worker_generation_for_incoming_event",
]
