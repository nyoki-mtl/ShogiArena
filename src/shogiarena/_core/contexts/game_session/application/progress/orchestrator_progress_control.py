"""Progress/worker assignment helpers for BaseOrchestrator."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from shogiarena._core.contexts.game_session.application.progress.hub import SummaryUpdateCallback
from shogiarena._core.contexts.game_session.application.progress.snapshot_models import WorkerSnapshotModel


def preassign_worker(
    *,
    numeric_game_id: int,
    num_workers: int,
    game_to_worker: dict[int, int],
    worker_busy: set[int],
    logger: logging.Logger,
) -> int | None:
    """Assign a stable worker index for a game if available."""

    if numeric_game_id in game_to_worker:
        return game_to_worker[numeric_game_id]
    for idx in range(num_workers):
        if idx not in worker_busy:
            game_to_worker[numeric_game_id] = idx
            worker_busy.add(idx)
            logger.debug("Pre-assigned worker %s to game %s", idx, numeric_game_id)
            return idx
    logger.debug("All workers busy; deferring assignment for game %s", numeric_game_id)
    return None


def start_progress_consumer(
    orchestrator: Any,
    *,
    num_workers: int,
    progress_queue: asyncio.Queue[tuple[int, int, str | None]],
    game_to_worker: dict[int, int],
    worker_busy: set[int],
    worker_snapshots: dict[int, WorkerSnapshotModel],
    on_summary_update: SummaryUpdateCallback | None = None,
) -> None:
    """Start shared progress consumer via ProgressHub."""

    owner = orchestrator
    owner._progress_hub.update_api_server(owner.api_server)
    owner._progress_hub.start(
        num_workers=num_workers,
        progress_queue=progress_queue,
        game_to_worker=game_to_worker,
        worker_busy=worker_busy,
        worker_snapshots=worker_snapshots,
        on_summary_update=on_summary_update,
    )


__all__ = ["preassign_worker", "start_progress_consumer"]
