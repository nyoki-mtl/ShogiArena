"""SPSA run-state I/O helpers.

Provides load/init logic for ``run_state.json`` used by the SPSA runner.
Moved from the interfaces layer so adapters and interfaces can both reach it
without introducing reverse dependencies.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

from shogiarena._core.shared.kernel.boundary_parsers.runner_state_payloads.parsers import (
    parse_spsa_run_state_boundary,
)
from shogiarena._core.shared.kernel.json_types import JsonObject
from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_int


def load_or_init_spsa_run_state(
    state_path: Path,
    *,
    total_updates: int,
    now: str | None = None,
) -> JsonObject:
    """Load and normalize SPSA run_state.json.

    If the file is absent, initialize a fresh state.
    Always updates ``type``, ``updated_at``, ``is_finished``, and ``total_updates``
    fields before persisting the normalized payload.
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
    else:
        raw = json.loads(state_path.read_text(encoding="utf-8"))
        if not isinstance(raw, Mapping):
            raise TypeError("run_state.json must be an object")
        state = parse_spsa_run_state_boundary(raw, path=str(state_path))

    normalized_time = now if now is not None else datetime.now(UTC).isoformat()
    state["type"] = "spsa"
    state["updated_at"] = normalized_time
    state["is_finished"] = False
    state["completed_updates"] = coerce_int(state.get("completed_updates")) or 0
    state["total_updates"] = int(total_updates)

    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return state


__all__ = ["load_or_init_spsa_run_state"]
