"""Follow a directory of CSA bridge event logs and keep the folded state current.

The file work is pushed onto a worker thread. This watcher shares an event loop
with the dashboard API server, and a poll that reads megabytes of JSONL inline
would show up as dashboard latency (``agent-docs/rules/event-loop-cost-budget.md``).
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from shogiarena._core.contexts.csa_watch.domain.event_records import InvalidLine, ParsedRecord, parse_event_record
from shogiarena._core.contexts.csa_watch.domain.fold import fold
from shogiarena._core.contexts.csa_watch.domain.run_state import RunState
from shogiarena._core.contexts.csa_watch.ports.log_source_ports import (
    CsaLogCursorPort,
    CsaLogSourcePort,
    DiscoveredRun,
    LogChunk,
)

logger = logging.getLogger(__name__)

DEFAULT_POLL_INTERVAL_SECONDS = 1.0


@dataclass(frozen=True)
class RunView:
    """Folded state of one run plus the reading conditions it was produced under."""

    worker_idx: int
    state: RunState
    path: Path
    has_partial_line: bool = False
    applied_records: int = 0
    stream_generation: int = 0
    restarted: bool = False


def _decode_line(line: str) -> ParsedRecord:
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return InvalidLine(reason="line is not valid JSON")
    if not isinstance(payload, dict):
        return InvalidLine(reason="line is not a JSON object")
    return parse_event_record(payload)


def apply_chunk(state: RunState, chunk: LogChunk) -> tuple[RunState, int]:
    """Fold every complete line of a chunk. Pure, so it is safe on any thread."""
    applied = 0
    for line in chunk.lines:
        state = fold(state, _decode_line(line))
        applied += 1
    return state, applied


@dataclass
class _TrackedRun:
    cursor: CsaLogCursorPort
    state: RunState
    path: Path
    worker_idx: int
    has_partial_line: bool = False
    stream_generation: int = 0


class CsaRunWatcher:
    """Discovers runs, tails them, and exposes the folded state of each."""

    def __init__(self, source: CsaLogSourcePort) -> None:
        self._source = source
        self._runs: dict[str, _TrackedRun] = {}
        self._next_worker_idx = 0

    @property
    def run_count(self) -> int:
        return len(self._runs)

    def views(self) -> tuple[RunView, ...]:
        return tuple(
            sorted(
                (
                    RunView(
                        worker_idx=tracked.worker_idx,
                        state=tracked.state,
                        path=tracked.path,
                        has_partial_line=tracked.has_partial_line,
                        stream_generation=tracked.stream_generation,
                    )
                    for tracked in self._runs.values()
                ),
                key=lambda view: view.worker_idx,
            )
        )

    def view_for(self, run_id: str) -> RunView | None:
        tracked = self._runs.get(run_id)
        if tracked is None:
            return None
        return RunView(
            worker_idx=tracked.worker_idx,
            state=tracked.state,
            path=tracked.path,
            has_partial_line=tracked.has_partial_line,
            stream_generation=tracked.stream_generation,
        )

    async def refresh(self) -> tuple[RunView, ...]:
        """Pick up new runs and new records. Returns only the runs that moved."""
        discovered = await asyncio.to_thread(self._source.discover_runs)
        self._register(discovered)

        changed: list[RunView] = []
        for tracked in list(self._runs.values()):
            chunk = await asyncio.to_thread(tracked.cursor.read_new_lines)
            restarted = chunk.restarted
            if chunk.restarted:
                # The file we were reading is gone; what sits under that name now
                # is a different byte stream. Folding the new lines onto the old
                # state would splice two bridge sessions into one run — their
                # games, alerts, ponder tallies and liveness all mixed — so start
                # from nothing, exactly as if this run had just been discovered.
                logger.warning("csa run %s restarted its log; rebuilding state", tracked.state.run_id)
                tracked.state = RunState(run_id=tracked.state.run_id)
                tracked.stream_generation += 1
            state, applied = await asyncio.to_thread(apply_chunk, tracked.state, chunk)
            partial_changed = tracked.has_partial_line != chunk.has_partial_line
            tracked.state = state
            tracked.has_partial_line = chunk.has_partial_line
            if restarted or applied or partial_changed:
                changed.append(
                    RunView(
                        worker_idx=tracked.worker_idx,
                        state=state,
                        path=tracked.path,
                        has_partial_line=chunk.has_partial_line,
                        applied_records=applied,
                        stream_generation=tracked.stream_generation,
                        restarted=restarted,
                    )
                )
        changed.sort(key=lambda view: view.worker_idx)
        return tuple(changed)

    def _register(self, discovered: Sequence[DiscoveredRun]) -> None:
        """Give each run a worker index once, and never move it afterwards.

        The initial scan is ordered newest run first, so the run being played gets
        worker 0. Runs found later append at the end: reordering live workers would
        make the dashboard swap two games under the viewer.
        """
        fresh = [run for run in discovered if run.run_id not in self._runs]
        for run in fresh:
            self._runs[run.run_id] = _TrackedRun(
                cursor=self._source.open_cursor(run),
                state=RunState(run_id=run.run_id),
                path=run.path,
                worker_idx=self._next_worker_idx,
            )
            self._next_worker_idx += 1
            logger.info("watching csa run %s (worker %s)", run.run_id, self._runs[run.run_id].worker_idx)

    def close(self) -> None:
        for tracked in self._runs.values():
            tracked.cursor.close()
        self._runs.clear()


async def read_run_once(source: CsaLogSourcePort, run: DiscoveredRun) -> RunView:
    """Fold one run to the end of the file, without following it."""
    cursor = source.open_cursor(run)
    try:
        state = RunState(run_id=run.run_id)
        has_partial = False
        while True:
            chunk = await asyncio.to_thread(cursor.read_new_lines)
            has_partial = chunk.has_partial_line
            if not chunk.lines:
                break
            state, _ = await asyncio.to_thread(apply_chunk, state, chunk)
        return RunView(worker_idx=0, state=state, path=run.path, has_partial_line=has_partial)
    finally:
        cursor.close()


__all__ = [
    "DEFAULT_POLL_INTERVAL_SECONDS",
    "CsaRunWatcher",
    "RunView",
    "apply_chunk",
    "read_run_once",
]
