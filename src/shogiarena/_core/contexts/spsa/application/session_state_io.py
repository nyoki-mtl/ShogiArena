"""SPSA run-state I/O helpers.

Provides load/init logic for ``state.json`` used by the SPSA runner.
Moved from the interfaces layer so adapters and interfaces can both reach it
without introducing reverse dependencies.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

from shogiarena._core.shared.kernel.atomic_json import write_json_atomic
from shogiarena._core.shared.kernel.boundary_parsers.runner_state_payloads.parsers import (
    parse_spsa_run_state_boundary,
)
from shogiarena._core.shared.kernel.json_types import JsonObject


def load_or_init_spsa_run_state(
    state_path: Path,
    *,
    total_updates: int,
    schedule_hash: str | None = None,
    resume_hash: str | None = None,
    now: str | None = None,
) -> JsonObject:
    """Load an SPSA run state, or initialize it when no state exists.

    Existing state is validated without rewriting it.  In particular, resume
    metadata must never be normalized to the current configuration: doing so
    would turn a mismatched or corrupt run into an apparently valid one.
    """

    if not state_path.exists():
        current_time = now if now is not None else datetime.now(UTC).isoformat()
        state: JsonObject = {
            "type": "spsa",
            "created_at": current_time,
            "updated_at": current_time,
            "is_finished": False,
            "completed_updates": 0,
            "total_updates": int(total_updates),
        }
        if schedule_hash is not None:
            state["schedule_hash"] = schedule_hash
        if resume_hash is not None:
            state["resume_hash"] = resume_hash
        write_json_atomic(state_path, state)
        return state

    raw = json.loads(state_path.read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping):
        raise TypeError("state.json must be an object")
    _validate_existing_spsa_state(
        raw,
        total_updates=total_updates,
        schedule_hash=schedule_hash,
        resume_hash=resume_hash,
    )
    return parse_spsa_run_state_boundary(raw, path=str(state_path))


def _validate_existing_spsa_state(
    state: Mapping[str, object],
    *,
    total_updates: int,
    schedule_hash: str | None,
    resume_hash: str | None,
) -> None:
    """Validate fields that define whether an SPSA state is resumable."""

    if state.get("type") != "spsa":
        raise ValueError("state.json type must be 'spsa'")

    completed = state.get("completed_updates")
    saved_total = state.get("total_updates")
    if isinstance(completed, bool) or not isinstance(completed, int) or completed < 0:
        raise ValueError("state.json completed_updates must be a non-negative integer")
    if isinstance(saved_total, bool) or not isinstance(saved_total, int) or saved_total <= 0:
        raise ValueError("state.json total_updates must be a positive integer")
    if saved_total != int(total_updates):
        raise ValueError("state.json total_updates does not match the current SPSA run")
    if completed > saved_total:
        raise ValueError("state.json completed_updates exceeds total_updates")

    is_finished = state.get("is_finished")
    if not isinstance(is_finished, bool):
        raise ValueError("state.json is_finished must be a boolean")
    if is_finished != (completed >= saved_total):
        raise ValueError("state.json is_finished disagrees with completed_updates")

    if schedule_hash is not None and state.get("schedule_hash") != schedule_hash:
        raise ValueError("state.json schedule_hash does not match the sealed run manifest")
    if resume_hash is not None and state.get("resume_hash") != resume_hash:
        raise ValueError("state.json resume_hash does not match the sealed run manifest")


__all__ = ["load_or_init_spsa_run_state"]
