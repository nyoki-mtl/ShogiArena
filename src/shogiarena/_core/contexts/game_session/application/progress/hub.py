"""Progress event consumption and broadcast hub for orchestrators."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from shogiarena._core.contexts.game_session.application.progress.consumption import (
    ProgressState,
    consume_progress_loop,
    serialize_public_snapshot,
)
from shogiarena._core.contexts.game_session.application.progress.snapshot_models import (
    WorkerSnapshotModel,
)
from shogiarena._core.contexts.game_session.application.progress.snapshot_payload_builders import (
    to_worker_snapshot_dto,
)
from shogiarena._core.contexts.game_session.ports.session_runner_ports import DashboardServerPort
from shogiarena._core.shared.kernel.live_stream_payloads import LiveStreamDiffPayload

logger = logging.getLogger(__name__)

SummaryUpdateCallback = Callable[[], Awaitable[None]]


class ProgressHub:
    """Consume and broadcast progress events for orchestrators."""

    def __init__(
        self,
        *,
        api_server: DashboardServerPort | None,
        preassign_worker: Callable[[int, int, dict[int, int], set[int]], int | None],
    ) -> None:
        self._api_server = api_server
        self._preassign_worker = preassign_worker
        self._progress_task: asyncio.Task[None] | None = None
        self._summary_update_task: asyncio.Task[None] | None = None
        self._is_summary_update_requested = False

    def update_api_server(self, api_server: DashboardServerPort | None) -> None:
        self._api_server = api_server

    def start(
        self,
        *,
        num_workers: int,
        progress_queue: asyncio.Queue[tuple[int, int, str | None]],
        game_to_worker: dict[int, int],
        worker_busy: set[int],
        worker_snapshots: dict[int, WorkerSnapshotModel],
        on_summary_update: SummaryUpdateCallback | None = None,
    ) -> None:
        """Start consume_progress_loop as a task."""
        state = ProgressState(
            num_workers=num_workers,
            game_to_worker=game_to_worker,
            worker_busy=worker_busy,
            worker_snapshots=worker_snapshots,
        )
        self._progress_task = asyncio.create_task(
            consume_progress_loop(
                progress_queue=progress_queue,
                state=state,
                preassign_worker=self._preassign_worker,
                api_server=self._api_server,
                broadcast_snapshot_and_diff=self._broadcast_snapshot_and_diff,
                on_summary_update=on_summary_update,
                schedule_summary_update=self._schedule_summary_update,
            )
        )

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
        self, worker_idx: int, snapshot: WorkerSnapshotModel, diff_payload: LiveStreamDiffPayload
    ) -> None:
        if not self._api_server:
            return
        dto = to_worker_snapshot_dto(snapshot)
        # `serialize_public_snapshot` は各値に `json_serialize` を掛けるので、
        # その前に `to_json_object` を通すのは同じ木をもう一度歩くだけの冗長走査だった。
        # このメソッドは progress イベントごとに event loop 上で走る。
        cleaned_snapshot = serialize_public_snapshot(dto)
        try:
            self._api_server.set_worker_snapshot(worker_idx, cleaned_snapshot, should_broadcast=False)
            self._api_server.broadcast_worker_update(worker_idx, diff_payload)
        except (OSError, RuntimeError, ValueError) as exc:
            logger.debug("Failed to broadcast worker update for %s: %s", worker_idx, exc, exc_info=True)

    def _schedule_summary_update(self, updater: SummaryUpdateCallback) -> None:
        if self._summary_update_task and not self._summary_update_task.done():
            self._is_summary_update_requested = True
            return
        self._is_summary_update_requested = False
        try:
            self._summary_update_task = asyncio.create_task(self._run_summary_update(updater))
        except RuntimeError:
            logger.debug("Running summary updater synchronously (no event loop)")

            async def _run_once() -> None:
                await updater()

            asyncio.run(_run_once())
            return

        def _cleanup(task: asyncio.Task[None]) -> None:
            self._summary_update_task = None
            exc = task.exception()
            if exc:
                logger.warning("Summary updater task failed", exc_info=exc)
            if self._is_summary_update_requested:
                self._is_summary_update_requested = False
                self._schedule_summary_update(updater)

        self._summary_update_task.add_done_callback(_cleanup)

    async def _run_summary_update(self, updater: SummaryUpdateCallback) -> None:
        try:
            await updater()
        except (RuntimeError, ValueError, OSError) as exc:
            logger.warning("Summary updater failed: %s", exc, exc_info=True)
            raise


__all__ = [
    "DashboardServerPort",
    "ProgressHub",
    "SummaryUpdateCallback",
]
