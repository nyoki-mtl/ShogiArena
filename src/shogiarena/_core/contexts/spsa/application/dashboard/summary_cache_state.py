"""Incremental cache-state helpers for SPSA summary projections."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from shogiarena._core.shared.kernel.json_coercion import to_json_object

from .summary_accumulator import SummaryAccumulator

module_logger = logging.getLogger(__name__)


@dataclass(slots=True)
class SummaryCacheState:
    """Mutable incremental summary cache state."""

    aggregates: SummaryAccumulator
    events_offset: int = 0
    events_size: int = 0
    events_mtime_ns: int = 0


def create_empty_summary_cache_state() -> SummaryCacheState:
    """Create an empty state with zeroed offsets."""

    return SummaryCacheState(aggregates=SummaryAccumulator())


def _reset_summary_cache_state(state: SummaryCacheState) -> None:
    """Reset incremental tracking after authoritative event replacement."""

    state.aggregates = SummaryAccumulator()
    state.events_offset = 0
    state.events_size = 0
    state.events_mtime_ns = 0


def refresh_summary_cache_state_from_events(
    state: SummaryCacheState,
    *,
    events_path: Path,
    logger: logging.Logger | None = None,
) -> bool:
    """Apply newly appended events.jsonl records onto state."""
    log = logger or module_logger

    if not events_path.exists():
        if state.events_size != 0:
            _reset_summary_cache_state(state)
            return True
        return False

    try:
        stat = events_path.stat()
    except OSError as exc:
        log.debug("Failed to stat SPSA events file %s: %s", events_path, exc)
        return False

    should_reset = False
    if stat.st_size < state.events_offset:
        should_reset = True
    elif stat.st_mtime_ns != state.events_mtime_ns and stat.st_size <= state.events_size:
        should_reset = True

    has_changed = False
    start_offset = 0
    if should_reset:
        _reset_summary_cache_state(state)
        has_changed = True
    else:
        start_offset = state.events_offset

    try:
        new_offset = start_offset
        if stat.st_size > start_offset:
            with events_path.open("rb") as handle:
                handle.seek(start_offset)
                while True:
                    line_start = handle.tell()
                    raw_line = handle.readline()
                    if not raw_line:
                        break
                    if not raw_line.endswith(b"\n"):
                        new_offset = line_start
                        break
                    new_offset = handle.tell()
                    if not raw_line.strip():
                        continue
                    try:
                        payload = json.loads(raw_line.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                        log.debug("Skipping malformed SPSA event line: %s", exc)
                        continue
                    if isinstance(payload, Mapping) and state.aggregates.consume_event(to_json_object(payload)):
                        has_changed = True
    except OSError as exc:
        log.debug("Failed to refresh SPSA summary cache from %s: %s", events_path, exc)
        return False

    state.events_offset = new_offset
    state.events_size = stat.st_size
    state.events_mtime_ns = stat.st_mtime_ns
    return has_changed


__all__ = [
    "create_empty_summary_cache_state",
    "refresh_summary_cache_state_from_events",
]
