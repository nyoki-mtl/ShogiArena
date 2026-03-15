"""Shared runtime snapshot and metadata-cache helpers for session runners."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Protocol

from shogiarena._core.shared.kernel.json_types import JsonObject
from shogiarena._core.shared.kernel.snapshots import EngineInfoSnapshots, EngineOptionsSnapshots


class RuntimeSnapshotSourcePort(Protocol):
    """Minimal orchestrator contract for runtime snapshot acquisition."""

    def get_engine_option_snapshots(self) -> EngineOptionsSnapshots: ...

    def get_engine_info_snapshots(self) -> EngineInfoSnapshots: ...


def fetch_runtime_snapshots(
    orchestrator: RuntimeSnapshotSourcePort | None,
    *,
    logger: logging.Logger,
) -> tuple[EngineOptionsSnapshots, EngineInfoSnapshots]:
    """Fetch runtime option/info snapshots with defensive error handling."""

    runtime_options: EngineOptionsSnapshots = {}
    runtime_info: EngineInfoSnapshots = {}
    if orchestrator is None:
        return runtime_options, runtime_info

    try:
        runtime_options = orchestrator.get_engine_option_snapshots()
    except (RuntimeError, AttributeError, TypeError, ValueError) as exc:
        logger.debug("Failed to fetch runtime USI options from orchestrator: %s", exc, exc_info=True)

    try:
        runtime_info = orchestrator.get_engine_info_snapshots()
    except (RuntimeError, AttributeError, TypeError, ValueError) as exc:
        logger.debug("Failed to fetch runtime engine info from orchestrator: %s", exc, exc_info=True)

    return runtime_options, runtime_info


def resolve_engine_metadata_cache(
    *,
    existing_metadata: list[JsonObject] | None,
    existing_runtime_sig: str | None,
    runtime_options: EngineOptionsSnapshots,
    collect_metadata_fn: Callable[[], list[JsonObject]],
) -> tuple[list[JsonObject], str | None]:
    """Update metadata cache only when runtime option signature changes."""

    try:
        runtime_sig = json.dumps(runtime_options, sort_keys=True, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("runtime_options must be JSON-serializable") from exc

    should_refresh = existing_metadata is None or runtime_sig != existing_runtime_sig
    if should_refresh:
        refreshed = collect_metadata_fn()
        return refreshed, runtime_sig

    if existing_metadata is None:
        refreshed = collect_metadata_fn()
        return refreshed, runtime_sig

    return existing_metadata, runtime_sig


__all__ = ["fetch_runtime_snapshots", "resolve_engine_metadata_cache"]
