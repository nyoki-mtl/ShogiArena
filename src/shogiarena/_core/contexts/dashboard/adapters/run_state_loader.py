"""Shared helpers for loading dashboard run state snapshots."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from pathlib import Path

from shogiarena._core.shared.kernel.json_coercion import to_json_object
from shogiarena._core.shared.kernel.json_types import JsonObject


def load_run_state_mapping(run_dir: Path, *, logger: logging.Logger | None = None) -> JsonObject:
    """Load ``run_state.json`` from ``run_dir`` as a JSON object mapping."""

    run_state_path = run_dir / "run_state.json"
    if not run_state_path.exists():
        return {}

    try:
        loaded = json.loads(run_state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        if logger is not None:
            logger.debug("Failed to read run_state.json: %s", exc, exc_info=True)
        return {}

    if not isinstance(loaded, Mapping):
        return {}

    return to_json_object(loaded)


__all__ = ["load_run_state_mapping"]
