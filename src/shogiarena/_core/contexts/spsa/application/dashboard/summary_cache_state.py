"""Incremental cache-state helpers for SPSA summary projections."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from shogiarena._core.shared.kernel.json_coercion import to_json_object
from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_optional_text

from .summary_accumulator import SummaryAccumulator
from .summary_cache_payload import SummaryCachePayload

module_logger = logging.getLogger(__name__)


@dataclass(slots=True)
class SummaryCacheState:
    """Mutable incremental summary cache state."""

    aggregates: SummaryAccumulator
    session_uuid: str | None = None
    events_offset: int = 0
    events_size: int = 0
    events_mtime_ns: int = 0


def create_empty_summary_cache_state() -> SummaryCacheState:
    """Create an empty state with zeroed offsets."""

    return SummaryCacheState(aggregates=SummaryAccumulator())


def _reset_summary_cache_state(state: SummaryCacheState, *, session_uuid: str | None) -> None:
    """Reset incremental tracking while preserving the active session."""

    state.aggregates = SummaryAccumulator()
    state.session_uuid = session_uuid
    state.events_offset = 0
    state.events_size = 0
    state.events_mtime_ns = 0


def load_summary_cache_state(cache_path: Path, *, cache_version: int) -> SummaryCacheState | None:
    """Load cache state snapshot from disk."""

    if not cache_path.exists():
        return None
    try:
        raw = json.loads(cache_path.read_text(encoding="utf-8"))
        payload = SummaryCachePayload.model_validate(raw)
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        module_logger.debug("Failed to load SPSA summary cache state from %s: %s", cache_path, exc)
        return None
    if payload.version != cache_version:
        return None

    aggregates_raw = payload.aggregates
    if isinstance(aggregates_raw, Mapping):
        aggregates = SummaryAccumulator.from_dict(to_json_object(aggregates_raw))
    else:
        aggregates = SummaryAccumulator()
    return SummaryCacheState(
        aggregates=aggregates,
        session_uuid=coerce_optional_text(payload.session_uuid),
        events_offset=payload.events_offset,
        events_size=payload.events_size,
        events_mtime_ns=payload.events_mtime_ns,
    )


def persist_summary_cache_state(cache_path: Path, *, cache_version: int, state: SummaryCacheState) -> bool:
    """Persist current state snapshot to disk."""

    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        aggregates_payload: dict[str, object] = {str(key): value for key, value in state.aggregates.to_dict().items()}
        snapshot = SummaryCachePayload(
            version=cache_version,
            session_uuid=state.session_uuid,
            events_offset=state.events_offset,
            events_size=state.events_size,
            events_mtime_ns=state.events_mtime_ns,
            aggregates=aggregates_payload,
        )
        cache_path.write_text(json.dumps(snapshot.model_dump(mode="python")), encoding="utf-8")
    except OSError as exc:
        module_logger.debug("Failed to persist SPSA summary cache state to %s: %s", cache_path, exc)
        return False
    return True


def refresh_summary_cache_state_from_events(
    state: SummaryCacheState,
    *,
    events_path: Path,
    session_uuid: str | None,
    logger: logging.Logger | None = None,
) -> bool:
    """Apply newly appended events.jsonl records onto state."""
    log = logger or module_logger

    if not events_path.exists():
        if state.events_size != 0:
            _reset_summary_cache_state(state, session_uuid=session_uuid)
            return True
        return False

    try:
        stat = events_path.stat()
    except OSError as exc:
        log.debug("Failed to stat SPSA events file %s: %s", events_path, exc)
        return False

    should_reset = False
    if session_uuid != state.session_uuid:
        should_reset = True
    elif stat.st_size < state.events_offset:
        should_reset = True
    elif stat.st_mtime_ns != state.events_mtime_ns and stat.st_size <= state.events_size:
        should_reset = True

    has_changed = False
    start_offset = 0
    if should_reset:
        _reset_summary_cache_state(state, session_uuid=session_uuid)
        has_changed = True
    else:
        start_offset = state.events_offset

    try:
        new_offset = start_offset
        if stat.st_size > start_offset:
            with events_path.open("rb") as handle:
                handle.seek(start_offset)
                for raw_line in handle:
                    if not raw_line.strip():
                        continue
                    try:
                        payload = json.loads(raw_line.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                        log.debug("Skipping malformed SPSA event line: %s", exc)
                        continue
                    if isinstance(payload, Mapping) and state.aggregates.consume_event(to_json_object(payload)):
                        has_changed = True
                new_offset = handle.tell()
    except OSError as exc:
        log.debug("Failed to refresh SPSA summary cache from %s: %s", events_path, exc)
        return False

    state.events_offset = new_offset
    state.events_size = stat.st_size
    state.events_mtime_ns = stat.st_mtime_ns
    state.session_uuid = session_uuid
    return has_changed


__all__ = [
    "create_empty_summary_cache_state",
    "load_summary_cache_state",
    "persist_summary_cache_state",
    "refresh_summary_cache_state_from_events",
]
