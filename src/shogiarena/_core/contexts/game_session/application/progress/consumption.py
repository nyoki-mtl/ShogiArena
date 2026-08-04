"""Progress event consumption loop and public handler API."""

from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from shogiarena._core.contexts.game_session.application.progress.consumption_event_handlers import (
    handle_clock_increment,
    handle_clock_start,
    handle_engine_io,
    handle_engine_state,
    handle_game_assigned,
    handle_handshake_log,
    handle_move_progress,
    sync_worker_generation_for_incoming_event,
)
from shogiarena._core.contexts.game_session.application.progress.events import parse_progress_event
from shogiarena._core.contexts.game_session.application.progress.snapshot_models import WorkerSnapshotModel
from shogiarena._core.contexts.game_session.application.progress.snapshot_normalizer import (
    is_str_object_mapping as _snapshot_is_str_object_mapping,
)
from shogiarena._core.shared.kernel.json_coercion import to_json_object
from shogiarena._core.shared.kernel.json_types import JsonObject, JsonValue
from shogiarena._core.shared.kernel.live_stream_payloads import LiveStreamDiffPayload
from shogiarena._core.shared.kernel.serialization import json_serialize

logger = logging.getLogger(__name__)

SummaryUpdateCallback = Callable[[], Awaitable[None]]
PreassignWorkerCallback = Callable[[int, int, dict[int, int], set[int]], int | None]
BroadcastSnapshotAndDiffCallback = Callable[[int, WorkerSnapshotModel, LiveStreamDiffPayload], None]
ScheduleSummaryUpdateCallback = Callable[[SummaryUpdateCallback], None]

_RECENT_COMPLETED_GAME_IDS_LIMIT = 4096


@runtime_checkable
class ProgressApiServerPort(Protocol):
    def assign_worker_snapshot(self, worker_idx: int, snapshot: Mapping[str, JsonValue]) -> None: ...

    def broadcast_engine_io(self, worker_idx: int, payload: Mapping[str, JsonValue]) -> None: ...

    def broadcast_engine_io_batch(self, worker_idx: int, payload: Mapping[str, JsonValue]) -> None: ...

    def clear_engine_logs(self, game_id: str | int) -> None: ...


@dataclass
class ProgressState:
    num_workers: int
    game_to_worker: dict[int, int]
    worker_busy: set[int]
    worker_snapshots: dict[int, WorkerSnapshotModel]
    worker_generation: dict[int, int] = field(default_factory=dict)


async def consume_progress_loop(
    *,
    progress_queue: asyncio.Queue[tuple[int, int, str | None]],
    state: ProgressState,
    preassign_worker: PreassignWorkerCallback,
    api_server: ProgressApiServerPort | None,
    broadcast_snapshot_and_diff: BroadcastSnapshotAndDiffCallback,
    on_summary_update: SummaryUpdateCallback | None = None,
    schedule_summary_update: ScheduleSummaryUpdateCallback | None = None,
) -> None:
    """Consume GameRunner progress events and push worker snapshot diffs."""

    deferred_by_game: dict[int, deque[tuple[int, str | None]]] = {}
    deferred_game_order: deque[int] = deque()
    recent_completed_game_ids: deque[int] = deque()
    recent_completed_game_id_set: set[int] = set()

    def mark_game_completed(game_id_num: int) -> None:
        if game_id_num in recent_completed_game_id_set:
            return
        recent_completed_game_ids.append(game_id_num)
        recent_completed_game_id_set.add(game_id_num)
        while len(recent_completed_game_ids) > _RECENT_COMPLETED_GAME_IDS_LIMIT:
            evicted_game_id = recent_completed_game_ids.popleft()
            recent_completed_game_id_set.discard(evicted_game_id)

    def clear_recent_completed_if_present(game_id_num: int) -> None:
        if game_id_num not in recent_completed_game_id_set:
            return
        recent_completed_game_id_set.discard(game_id_num)

    def event_has_initial_sfen(progress: Mapping[str, object]) -> bool:
        initial_sfen = progress.get("initial_sfen")
        return isinstance(initial_sfen, str) and bool(initial_sfen.strip())

    def can_bootstrap_unassigned_game(progress: Mapping[str, object]) -> bool:
        event_type = progress.get("type")
        if event_type == "game_assigned":
            return True
        return event_has_initial_sfen(progress)

    async def process_progress_event(game_id_num: int, move_count: int, payload: str | None) -> None:
        if not isinstance(payload, str) or not payload.startswith("{"):
            return

        mapped_worker_idx: int | str = state.game_to_worker.get(game_id_num, "unassigned")

        try:
            loaded = json.loads(payload)
            if not _snapshot_is_str_object_mapping(loaded):
                logger.warning(
                    "Dropped non-object progress payload for game %s (worker %s)",
                    game_id_num,
                    mapped_worker_idx,
                )
                return
        except json.JSONDecodeError:
            logger.warning(
                "Dropped malformed progress payload for game %s (worker %s)",
                game_id_num,
                mapped_worker_idx,
            )
            return

        try:
            progress = parse_progress_event(loaded)
        except (ValueError, TypeError) as exc:
            logger.warning(
                "Dropped invalid progress payload for game %s (worker %s): %s",
                game_id_num,
                mapped_worker_idx,
                exc,
            )
            return

        if game_id_num not in state.game_to_worker and game_id_num in recent_completed_game_id_set:
            if progress["type"] != "game_assigned":
                logger.debug(
                    "Dropped late %s event for completed game %s",
                    progress["type"],
                    game_id_num,
                )
                return
            clear_recent_completed_if_present(game_id_num)

        if game_id_num not in state.game_to_worker:
            if not can_bootstrap_unassigned_game(progress):
                logger.debug(
                    "Dropped unassigned %s event without initial snapshot data for game %s",
                    progress["type"],
                    game_id_num,
                )
                return
            assigned = preassign_worker(
                game_id_num,
                state.num_workers,
                state.game_to_worker,
                state.worker_busy,
            )
            if assigned is None:
                pending = deferred_by_game.get(game_id_num)
                if pending is None:
                    pending = deque()
                    deferred_by_game[game_id_num] = pending
                    deferred_game_order.append(game_id_num)
                pending.append((move_count, payload))
                if len(pending) > 4096:
                    raise RuntimeError(
                        f"Deferred progress buffer overflow for game={game_id_num} (len={len(pending)}). "
                        "Too many concurrent games or progress consumer stalled."
                    )
                return
            state.worker_generation[assigned] = state.worker_generation.get(assigned, 0) + 1
            logger.debug(
                "Assigned worker %s to game %s (generation %s)",
                assigned,
                game_id_num,
                state.worker_generation[assigned],
            )

        worker_idx = state.game_to_worker[game_id_num]
        if worker_idx not in state.worker_snapshots:
            state.worker_generation[worker_idx] = state.worker_generation.get(worker_idx, 0) + 1
        current_gen = state.worker_generation.get(worker_idx, 0)

        snapshot = state.worker_snapshots.get(worker_idx)
        incoming_game_id = progress.get("game_id")
        if (
            isinstance(snapshot, WorkerSnapshotModel)
            and isinstance(snapshot.game_id, str)
            and snapshot.game_id
            and isinstance(incoming_game_id, str)
            and incoming_game_id
            and incoming_game_id != snapshot.game_id
            and not event_has_initial_sfen(progress)
        ):
            logger.debug(
                "Dropped %s for worker=%s because game changed (%s -> %s) and event has no initial_sfen",
                progress["type"],
                worker_idx,
                snapshot.game_id,
                incoming_game_id,
            )
            return

        try:
            current_gen = sync_worker_generation_for_incoming_event(
                worker_idx=worker_idx,
                progress=progress,
                state=state,
            )
        except RuntimeError as exc:
            logger.warning(
                "Dropped progress payload after generation sync failure for game %s (worker %s): %s",
                game_id_num,
                worker_idx,
                exc,
            )
            return

        if progress["type"] == "move_progress":
            diff_payload = handle_move_progress(
                worker_idx=worker_idx,
                current_gen=current_gen,
                progress=progress,
                state=state,
                game_id_num=game_id_num,
            )
            if api_server and diff_payload is not None:
                broadcast_snapshot_and_diff(worker_idx, state.worker_snapshots[worker_idx], diff_payload)
            if progress.get("game_result") is not None:
                if on_summary_update is not None:
                    if schedule_summary_update is not None:
                        schedule_summary_update(on_summary_update)
                    else:
                        await on_summary_update()
                mark_game_completed(game_id_num)
                state.worker_busy.discard(worker_idx)
                state.game_to_worker.pop(game_id_num, None)
                if api_server is not None:
                    clear_target: str | int = (
                        progress["game_id"] if progress.get("game_id") is not None else game_id_num
                    )
                    api_server.clear_engine_logs(clear_target)
            return

        if progress["type"] == "clock_start":
            diff_payload = handle_clock_start(
                worker_idx=worker_idx,
                current_gen=current_gen,
                progress=progress,
                state=state,
                game_id_num=game_id_num,
            )
            if api_server and diff_payload is not None:
                broadcast_snapshot_and_diff(worker_idx, state.worker_snapshots[worker_idx], diff_payload)
            return

        if progress["type"] == "clock_increment":
            diff_payload = handle_clock_increment(
                worker_idx=worker_idx,
                current_gen=current_gen,
                progress=progress,
                state=state,
                game_id_num=game_id_num,
            )
            if api_server and diff_payload is not None:
                broadcast_snapshot_and_diff(worker_idx, state.worker_snapshots[worker_idx], diff_payload)
            return

        if progress["type"] == "handshake_log":
            diff_payload = handle_handshake_log(
                worker_idx=worker_idx,
                current_gen=current_gen,
                progress=progress,
                state=state,
                game_id_num=game_id_num,
            )
            if api_server and diff_payload is not None:
                broadcast_snapshot_and_diff(worker_idx, state.worker_snapshots[worker_idx], diff_payload)
            return

        if progress["type"] == "engine_io":
            diff_payload = handle_engine_io(
                worker_idx=worker_idx,
                current_gen=current_gen,
                progress=progress,
                state=state,
                game_id_num=game_id_num,
            )
            if api_server is not None:
                engine_io_payload = to_json_object(dict(progress.items()))
                api_server.broadcast_engine_io(
                    worker_idx,
                    engine_io_payload,
                )
                if diff_payload is not None and worker_idx in state.worker_snapshots:
                    broadcast_snapshot_and_diff(worker_idx, state.worker_snapshots[worker_idx], diff_payload)
            return

        if progress["type"] == "engine_io_batch":
            # Raw transcript only: the engine_status badge is driven by ``engine_state`` events, so a
            # batch just appends to the ring and publishes to raw-I/O subscribers (no snapshot touch).
            if api_server is not None:
                batch_payload = to_json_object(dict(progress.items()))
                api_server.broadcast_engine_io_batch(worker_idx, batch_payload)
            return

        if progress["type"] == "engine_state":
            diff_payload = handle_engine_state(
                worker_idx=worker_idx,
                current_gen=current_gen,
                progress=progress,
                state=state,
                game_id_num=game_id_num,
            )
            if api_server and diff_payload is not None:
                broadcast_snapshot_and_diff(worker_idx, state.worker_snapshots[worker_idx], diff_payload)
            return

        diff_payload = handle_game_assigned(
            worker_idx=worker_idx,
            current_gen=current_gen,
            progress=progress,
            state=state,
            game_id_num=game_id_num,
            api_server=api_server,
        )
        if api_server and diff_payload is not None:
            broadcast_snapshot_and_diff(worker_idx, state.worker_snapshots[worker_idx], diff_payload)

    async def flush_deferred_if_possible() -> None:
        while deferred_game_order:
            if len(state.worker_busy) >= state.num_workers:
                return
            game_id_num = deferred_game_order.popleft()
            pending = deferred_by_game.get(game_id_num)
            if not pending:
                deferred_by_game.pop(game_id_num, None)
                continue
            if game_id_num not in state.game_to_worker:
                assigned = preassign_worker(
                    game_id_num,
                    state.num_workers,
                    state.game_to_worker,
                    state.worker_busy,
                )
                if assigned is None:
                    deferred_game_order.appendleft(game_id_num)
                    return
                state.worker_generation[assigned] = state.worker_generation.get(assigned, 0) + 1
            while pending:
                move_count, payload = pending.popleft()
                await process_progress_event(game_id_num, move_count, payload)
            deferred_by_game.pop(game_id_num, None)

    while True:
        game_id_num, move_count, payload = await progress_queue.get()
        await process_progress_event(game_id_num, move_count, payload)
        await flush_deferred_if_possible()


def serialize_public_snapshot(dto: Mapping[str, object]) -> JsonObject:
    """Serialize snapshot DTO for dashboard broadcast payloads.

    値の型は `object` で受ける。`json_serialize` が任意のオブジェクトを受け取る以上、
    呼び出し元に「先に JSON 化してから渡す」ことを要求する理由はない。
    実際その要求のせいで、呼び出し元が同じ木を 2 回走査していた。
    """

    return {str(key): json_serialize(value) for key, value in dto.items() if not str(key).startswith("_")}


__all__ = [
    "ProgressApiServerPort",
    "ProgressState",
    "consume_progress_loop",
    "serialize_public_snapshot",
]
