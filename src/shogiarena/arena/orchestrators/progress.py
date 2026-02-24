"""Progress event handling for orchestrators."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from collections.abc import Callable, Coroutine, Mapping
from dataclasses import dataclass, field
from typing import Protocol, cast

from shogiarena.utils.types.coerce import coerce_int as _coerce_int

from . import base_orchestrator_utils
from .base_orchestrator_utils import WorkerSnapshot, parse_move_progress

logger = logging.getLogger(__name__)


class DashboardServerProtocol(Protocol):
    """Protocol describing the dashboard API server interactions."""

    def set_worker_snapshot(
        self, worker_idx: int, snapshot: Mapping[str, object], *, broadcast: bool = True
    ) -> None: ...

    def broadcast_worker_update(self, worker_idx: int, payload: Mapping[str, object]) -> None: ...

    def assign_worker_snapshot(self, worker_idx: int, snapshot: Mapping[str, object]) -> None: ...

    def broadcast_engine_io(self, worker_idx: int, payload: Mapping[str, object]) -> None: ...

    def clear_engine_logs(self, game_id: str | int) -> None: ...


SummaryUpdateCallback = Callable[[], Coroutine[object, object, None]]


@dataclass
class ProgressState:
    num_workers: int
    game_to_worker: dict[int, int]
    worker_busy: set[int]
    worker_snapshots: dict[int, WorkerSnapshot]
    worker_generation: dict[int, int] = field(default_factory=dict)


class ProgressHub:
    """Consume and broadcast progress events for orchestrators."""

    def __init__(
        self,
        *,
        api_server: DashboardServerProtocol | None,
        preassign_worker: Callable[[int, int, dict[int, int], set[int]], int | None],
    ) -> None:
        self._api_server = api_server
        self._preassign_worker = preassign_worker
        self._progress_task: asyncio.Task[None] | None = None
        self._summary_update_task: asyncio.Task[None] | None = None
        self._summary_update_requested = False

    def update_api_server(self, api_server: DashboardServerProtocol | None) -> None:
        self._api_server = api_server

    def start(
        self,
        *,
        num_workers: int,
        progress_queue: asyncio.Queue[tuple[int, int, str | None]],
        game_to_worker: dict[int, int],
        worker_busy: set[int],
        worker_snapshots: dict[int, WorkerSnapshot],
        on_summary_update: SummaryUpdateCallback | None = None,
    ) -> None:
        """Start consume_progress_loop as a task."""
        state = ProgressState(
            num_workers=num_workers,
            game_to_worker=game_to_worker,
            worker_busy=worker_busy,
            worker_snapshots=worker_snapshots,
        )
        self._progress_task = asyncio.create_task(self.consume_progress_loop(progress_queue, state, on_summary_update))

    async def shutdown(self) -> None:
        """Cancel progress task and await summary updater."""
        pt = self._progress_task
        if pt is not None:
            pt.cancel()
            try:
                await pt
            except asyncio.CancelledError:
                pass

        summary_task = self._summary_update_task
        if summary_task is not None:
            try:
                await asyncio.shield(summary_task)
            except asyncio.CancelledError:
                pass
            except (RuntimeError, ValueError, OSError) as exc:
                logger.debug("Summary updater task encountered an error during shutdown: %s", exc, exc_info=True)

    def _broadcast_snapshot_and_diff(
        self, worker_idx: int, snapshot: WorkerSnapshot, diff_payload: dict[str, object]
    ) -> None:
        if not self._api_server:
            return
        cleaned_snapshot = {k: v for k, v in snapshot.items() if not str(k).startswith("_")}
        try:
            self._api_server.set_worker_snapshot(worker_idx, cleaned_snapshot, broadcast=False)
            self._api_server.broadcast_worker_update(worker_idx, diff_payload)
        except (OSError, RuntimeError, ValueError) as exc:
            logger.debug("Failed to broadcast worker update for %s: %s", worker_idx, exc, exc_info=True)

    async def consume_progress_loop(
        self,
        progress_queue: asyncio.Queue[tuple[int, int, str | None]],
        state: ProgressState,
        on_summary_update: SummaryUpdateCallback | None = None,
    ) -> None:
        """Generic consumer for GameRunner progress events with SSE updates."""
        deferred_by_game: dict[int, deque[tuple[int, str | None]]] = {}
        deferred_game_order: deque[int] = deque()

        async def process_progress_event(game_id_num: int, move_count: int, payload: str | None) -> None:
            if game_id_num not in state.game_to_worker:
                assigned = self._preassign_worker(
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

            if isinstance(payload, str) and payload.startswith("{"):
                try:
                    loaded = json.loads(payload)
                    if not isinstance(loaded, dict):
                        logger.warning(
                            "Dropped non-object progress payload for game %s (worker %s)",
                            game_id_num,
                            worker_idx,
                        )
                        return
                    progress = cast(dict[str, object], loaded)
                except json.JSONDecodeError:
                    logger.warning(
                        "Dropped malformed progress payload for game %s (worker %s)",
                        game_id_num,
                        worker_idx,
                    )
                    return

                typ = progress.get("type")
                incoming_gid = progress.get("game_id")
                snap0 = state.worker_snapshots.get(worker_idx)
                existing_gid = snap0.get("game_id") if snap0 is not None else None
                if incoming_gid and existing_gid and str(incoming_gid) != str(existing_gid):
                    state.worker_generation[worker_idx] = state.worker_generation.get(worker_idx, 0) + 1
                    current_gen = state.worker_generation[worker_idx]
                    try:
                        state.worker_snapshots[worker_idx] = base_orchestrator_utils.make_initial_snapshot(
                            progress, generation=current_gen, name_default=""
                        )
                    except ValueError as exc:
                        logger.warning(
                            "Dropped progress event missing initial_sfen (worker=%s game_id=%s): %s",
                            worker_idx,
                            incoming_gid,
                            exc,
                        )
                        return
                    logger.debug(
                        "Worker %s: game_id changed (%s -> %s); reset snapshot and generation to %s",
                        worker_idx,
                        existing_gid,
                        incoming_gid,
                        current_gen,
                    )
                if typ == "move_progress":
                    diff_payload = self._handle_move_progress(
                        worker_idx=worker_idx,
                        current_gen=current_gen,
                        progress=progress,
                        state=state,
                        game_id_num=game_id_num,
                    )
                    if self._api_server and diff_payload is not None:
                        self._broadcast_snapshot_and_diff(worker_idx, state.worker_snapshots[worker_idx], diff_payload)
                    if progress.get("result_code") is not None:
                        if on_summary_update is not None:
                            self._schedule_summary_update(on_summary_update)
                        state.worker_busy.discard(worker_idx)
                        state.game_to_worker.pop(game_id_num, None)
                        if self._api_server is not None:
                            raw_game_id = progress.get("game_id")
                            clear_target: str | int = raw_game_id if isinstance(raw_game_id, str | int) else game_id_num
                            self._api_server.clear_engine_logs(clear_target)
                elif typ == "clock_start":
                    diff_payload = self._handle_clock_start(
                        worker_idx=worker_idx,
                        current_gen=current_gen,
                        progress=progress,
                        state=state,
                        game_id_num=game_id_num,
                    )
                    if self._api_server and diff_payload is not None:
                        self._broadcast_snapshot_and_diff(worker_idx, state.worker_snapshots[worker_idx], diff_payload)
                elif typ == "clock_increment":
                    diff_payload = self._handle_clock_increment(
                        worker_idx=worker_idx,
                        current_gen=current_gen,
                        progress=progress,
                        state=state,
                        game_id_num=game_id_num,
                    )
                    if self._api_server and diff_payload is not None:
                        self._broadcast_snapshot_and_diff(worker_idx, state.worker_snapshots[worker_idx], diff_payload)
                elif typ == "handshake_log":
                    diff_payload = self._handle_handshake_log(
                        worker_idx=worker_idx,
                        current_gen=current_gen,
                        progress=progress,
                        state=state,
                        game_id_num=game_id_num,
                    )
                    if self._api_server and diff_payload is not None:
                        self._broadcast_snapshot_and_diff(worker_idx, state.worker_snapshots[worker_idx], diff_payload)
                elif typ == "engine_io":
                    diff_payload = self._handle_engine_io(
                        worker_idx=worker_idx,
                        current_gen=current_gen,
                        progress=progress,
                        state=state,
                        game_id_num=game_id_num,
                    )
                    if self._api_server is not None:
                        self._api_server.broadcast_engine_io(worker_idx, dict(progress))
                        if diff_payload is not None and worker_idx in state.worker_snapshots:
                            self._broadcast_snapshot_and_diff(
                                worker_idx, state.worker_snapshots[worker_idx], diff_payload
                            )
                elif typ == "game_assigned":
                    diff_payload = self._handle_game_assigned(
                        worker_idx=worker_idx,
                        current_gen=current_gen,
                        progress=progress,
                        state=state,
                        game_id_num=game_id_num,
                    )
                    if self._api_server and diff_payload is not None:
                        self._broadcast_snapshot_and_diff(worker_idx, state.worker_snapshots[worker_idx], diff_payload)

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
                    assigned = self._preassign_worker(
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

    def _handle_move_progress(
        self,
        worker_idx: int,
        current_gen: int,
        progress: dict[str, object],
        state: ProgressState,
        game_id_num: int,
    ) -> dict[str, object] | None:
        if worker_idx not in state.worker_snapshots:
            try:
                state.worker_snapshots[worker_idx] = base_orchestrator_utils.make_initial_snapshot(
                    progress, generation=current_gen, name_default="Unknown"
                )
            except ValueError as exc:
                logger.warning(
                    "Dropped move_progress missing initial_sfen (worker=%s game_id=%s): %s",
                    worker_idx,
                    progress.get("game_id"),
                    exc,
                )
                return None
        snapshot = state.worker_snapshots[worker_idx]
        if snapshot.get("_generation", 0) != current_gen:
            return None
        parsed = parse_move_progress(progress)
        base_orchestrator_utils.apply_move_progress(snapshot, parsed)
        return base_orchestrator_utils.build_move_diff_payload(parsed, snapshot, fallback_game_id=game_id_num)

    def _handle_clock_start(
        self,
        worker_idx: int,
        current_gen: int,
        progress: dict[str, object],
        state: ProgressState,
        game_id_num: int,
    ) -> dict[str, object] | None:
        if worker_idx not in state.worker_snapshots:
            snap = base_orchestrator_utils.make_initial_snapshot(progress, generation=current_gen, name_default="")
            snap["time_control_black"] = progress.get("time_control_black")
            snap["time_control_white"] = progress.get("time_control_white")
            state.worker_snapshots[worker_idx] = snap
        snapshot = state.worker_snapshots[worker_idx]
        if snapshot.get("_generation", 0) != current_gen:
            return None
        active_raw = progress.get("active")
        snapshot["_clock_active"] = active_raw if isinstance(active_raw, str) else None
        black_remain = _coerce_int(progress.get("black_remain_ms"))
        if black_remain is not None:
            snapshot["_black_remain_ms"] = black_remain
        white_remain = _coerce_int(progress.get("white_remain_ms"))
        if white_remain is not None:
            snapshot["_white_remain_ms"] = white_remain
        started_at = _coerce_int(progress.get("started_at_ms"))
        if started_at is not None:
            snapshot["_clock_started_at_ms"] = started_at
        if progress.get("time_control_black") is not None:
            snapshot["time_control_black"] = progress.get("time_control_black")
        if progress.get("time_control_white") is not None:
            snapshot["time_control_white"] = progress.get("time_control_white")
        return base_orchestrator_utils.build_clock_start_diff_payload(progress, game_id_num)

    def _handle_clock_increment(
        self,
        worker_idx: int,
        current_gen: int,
        progress: dict[str, object],
        state: ProgressState,
        game_id_num: int,
    ) -> dict[str, object] | None:
        snap_opt = state.worker_snapshots.get(worker_idx)
        if not snap_opt:
            state.worker_snapshots[worker_idx] = base_orchestrator_utils.make_initial_snapshot(
                progress, generation=current_gen, name_default=""
            )
            snap_opt = state.worker_snapshots[worker_idx]
        snapshot = snap_opt
        if snapshot.get("_generation", 0) != current_gen:
            return None
        snapshot["_clock_active"] = None
        black_remain = _coerce_int(progress.get("black_remain_ms"))
        if black_remain is not None:
            snapshot["_black_remain_ms"] = black_remain
        white_remain = _coerce_int(progress.get("white_remain_ms"))
        if white_remain is not None:
            snapshot["_white_remain_ms"] = white_remain
        occurred_at = _coerce_int(progress.get("occurred_at_ms"))
        if occurred_at is not None:
            snapshot["_clock_started_at_ms"] = occurred_at
        diff = base_orchestrator_utils.build_clock_increment_diff_payload(progress, game_id_num)
        diff["initial_sfen"] = snapshot.get("initial_sfen")
        diff["black_name"] = snapshot.get("black_name")
        diff["white_name"] = snapshot.get("white_name")
        if "time_control_black" in snapshot:
            diff["time_control_black"] = snapshot.get("time_control_black")
        if "time_control_white" in snapshot:
            diff["time_control_white"] = snapshot.get("time_control_white")
        return diff

    def _handle_game_assigned(
        self,
        worker_idx: int,
        current_gen: int,
        progress: dict[str, object],
        state: ProgressState,
        game_id_num: int,
    ) -> dict[str, object] | None:
        snapshot = state.worker_snapshots.get(worker_idx)
        if not snapshot:
            snapshot = base_orchestrator_utils.make_initial_snapshot(
                progress,
                generation=current_gen,
                name_default="",
            )
            state.worker_snapshots[worker_idx] = snapshot
        if snapshot.get("_generation", 0) != current_gen:
            return None
        same_game = str(snapshot.get("game_id", "")) == str(progress.get("game_id", ""))
        if not same_game:
            snapshot = base_orchestrator_utils.make_initial_snapshot(
                progress,
                generation=current_gen,
                name_default="",
            )
            state.worker_snapshots[worker_idx] = snapshot
        status = base_orchestrator_utils.ensure_engine_status(snapshot)
        now_ms = int(time.time() * 1000)
        for role in ("black", "white"):
            entry = status[role]
            entry_state = entry.get("state")
            entry_tail = entry.get("io_tail")
            should_mark_queued = not same_game
            if not should_mark_queued:
                state_normalized = str(entry_state).strip().lower() if isinstance(entry_state, str) else ""
                tail_is_empty = not isinstance(entry_tail, list) or len(entry_tail) == 0
                should_mark_queued = state_normalized in {"", "waiting_for_usiok", "queued"} and tail_is_empty
            if should_mark_queued:
                # `game_assigned` is emitted before engine acquisition and may
                # wait on shared capacity (slots/max_engines). Reflect that as a
                # queued phase and let handshake I/O transition to USI states.
                entry["state"] = "queued"
                entry["io_tail"] = []
                entry["updated_at_ms"] = now_ms
            else:
                if "state" not in entry or not isinstance(entry["state"], str):
                    entry["state"] = "queued"
                if "io_tail" not in entry or not isinstance(entry["io_tail"], list):
                    entry["io_tail"] = []
                if "updated_at_ms" not in entry:
                    entry["updated_at_ms"] = now_ms
        progress["engine_status"] = status
        tc_black = progress.get("time_control_black")
        tc_white = progress.get("time_control_white")
        if tc_black is not None and tc_white is not None:
            snapshot["time_control_black"] = tc_black
            snapshot["time_control_white"] = tc_white
        if self._api_server is not None:
            try:
                self._api_server.assign_worker_snapshot(worker_idx, snapshot)
            except (OSError, RuntimeError, ValueError) as exc:
                logger.debug("Failed to assign worker snapshot for %s: %s", worker_idx, exc, exc_info=True)
            return None
        return base_orchestrator_utils.build_game_assigned_diff_payload(progress, game_id_num)

    def _handle_handshake_log(
        self,
        worker_idx: int,
        current_gen: int,
        progress: Mapping[str, object],
        state: ProgressState,
        game_id_num: int,
    ) -> dict[str, object] | None:
        snapshot = state.worker_snapshots.get(worker_idx)
        if not snapshot:
            try:
                state.worker_snapshots[worker_idx] = base_orchestrator_utils.make_initial_snapshot(
                    dict(progress), generation=current_gen, name_default=""
                )
            except ValueError as exc:
                logger.warning(
                    "Dropped handshake_log missing initial_sfen (worker=%s game_id=%s): %s",
                    worker_idx,
                    progress.get("game_id"),
                    exc,
                )
                return None
            snapshot = state.worker_snapshots[worker_idx]
        if snapshot.get("_generation", 0) != current_gen:
            return None
        diff = base_orchestrator_utils.apply_handshake_log_payload(snapshot, progress)
        if diff is None:
            return None
        diff.setdefault("game_id", snapshot.get("game_id", progress.get("game_id")))
        if snapshot.get("initial_sfen") is not None:
            diff.setdefault("initial_sfen", snapshot.get("initial_sfen"))
        if snapshot.get("black_name") is not None:
            diff.setdefault("black_name", snapshot.get("black_name"))
        if snapshot.get("white_name") is not None:
            diff.setdefault("white_name", snapshot.get("white_name"))
        return diff

    def _handle_engine_io(
        self,
        worker_idx: int,
        current_gen: int,
        progress: Mapping[str, object],
        state: ProgressState,
        game_id_num: int,
    ) -> dict[str, object] | None:
        snapshot = state.worker_snapshots.get(worker_idx)
        if not snapshot:
            try:
                state.worker_snapshots[worker_idx] = base_orchestrator_utils.make_initial_snapshot(
                    dict(progress), generation=current_gen, name_default=""
                )
            except ValueError as exc:
                logger.warning(
                    "Dropped engine_io missing initial_sfen (worker=%s game_id=%s): %s",
                    worker_idx,
                    progress.get("game_id"),
                    exc,
                )
                return None
            snapshot = state.worker_snapshots[worker_idx]
        else:
            snapshot_gid = str(snapshot.get("game_id", ""))
            progress_gid = str(progress.get("game_id", ""))
            if snapshot_gid and progress_gid and snapshot_gid != progress_gid:
                try:
                    snapshot = base_orchestrator_utils.make_initial_snapshot(
                        dict(progress), generation=current_gen, name_default=""
                    )
                except ValueError as exc:
                    logger.warning(
                        "Dropped engine_io missing initial_sfen (worker=%s game_id=%s): %s",
                        worker_idx,
                        progress.get("game_id"),
                        exc,
                    )
                    return None
                state.worker_snapshots[worker_idx] = snapshot
        if snapshot.get("_generation", 0) != current_gen:
            return None
        diff = base_orchestrator_utils.apply_handshake_log_payload(snapshot, progress)
        if diff is None:
            return None
        diff.setdefault("game_id", snapshot.get("game_id", progress.get("game_id")))
        if snapshot.get("initial_sfen") is not None:
            diff.setdefault("initial_sfen", snapshot.get("initial_sfen"))
        if snapshot.get("black_name") is not None:
            diff.setdefault("black_name", snapshot.get("black_name"))
        if snapshot.get("white_name") is not None:
            diff.setdefault("white_name", snapshot.get("white_name"))
        return diff

    def _schedule_summary_update(self, updater: SummaryUpdateCallback) -> None:
        if self._summary_update_task and not self._summary_update_task.done():
            self._summary_update_requested = True
            return
        self._summary_update_requested = False
        try:
            self._summary_update_task = asyncio.create_task(self._run_summary_update(updater))
        except RuntimeError:
            logger.debug("Running summary updater synchronously (no event loop)")
            asyncio.run(updater())
            return

        def _cleanup(task: asyncio.Task[None]) -> None:
            self._summary_update_task = None
            exc = task.exception()
            if exc:
                logger.debug("Summary updater task failed", exc_info=exc)
            if self._summary_update_requested:
                self._summary_update_requested = False
                self._schedule_summary_update(updater)

        self._summary_update_task.add_done_callback(_cleanup)

    async def _run_summary_update(self, updater: SummaryUpdateCallback) -> None:
        try:
            await updater()
        except (RuntimeError, ValueError, OSError) as exc:
            logger.debug("Summary updater failed: %s", exc, exc_info=True)
