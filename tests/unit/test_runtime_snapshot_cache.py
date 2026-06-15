from __future__ import annotations

import pytest

from shogiarena._core.contexts.game_session.adapters.engine.runtime_snapshot_cache import (
    resolve_engine_metadata_cache,
)
from shogiarena._core.shared.kernel.json_types import JsonObject
from shogiarena._core.shared.kernel.snapshots import EngineInfoSnapshots, EngineOptionsSnapshots


def test_engine_metadata_cache_refreshes_when_runtime_info_changes() -> None:
    calls: list[str] = []
    runtime_options: EngineOptionsSnapshots = {"engine": {"Hash": 16}}
    runtime_info: EngineInfoSnapshots = {"engine": {"id name": "Engine A"}}

    metadata, runtime_sig = resolve_engine_metadata_cache(
        existing_metadata=None,
        existing_runtime_sig=None,
        runtime_options=runtime_options,
        runtime_info=runtime_info,
        collect_metadata_fn=lambda: _collect_metadata(calls, "initial"),
    )

    cached, refreshed_sig = resolve_engine_metadata_cache(
        existing_metadata=metadata,
        existing_runtime_sig=runtime_sig,
        runtime_options=runtime_options,
        runtime_info={"engine": {"id name": "Engine B"}},
        collect_metadata_fn=lambda: _collect_metadata(calls, "refreshed"),
    )

    assert cached == [{"name": "refreshed"}]
    assert refreshed_sig != runtime_sig
    assert calls == ["initial", "refreshed"]


def test_engine_metadata_cache_reuses_metadata_when_runtime_snapshots_match() -> None:
    calls: list[str] = []
    runtime_options: EngineOptionsSnapshots = {"engine": {"Threads": 1}}
    runtime_info: EngineInfoSnapshots = {"engine": {"id author": "tester"}}

    metadata, runtime_sig = resolve_engine_metadata_cache(
        existing_metadata=None,
        existing_runtime_sig=None,
        runtime_options=runtime_options,
        runtime_info=runtime_info,
        collect_metadata_fn=lambda: _collect_metadata(calls, "initial"),
    )

    cached, refreshed_sig = resolve_engine_metadata_cache(
        existing_metadata=metadata,
        existing_runtime_sig=runtime_sig,
        runtime_options=runtime_options,
        runtime_info=runtime_info,
        collect_metadata_fn=lambda: _collect_metadata(calls, "unused"),
    )

    assert cached == metadata
    assert refreshed_sig == runtime_sig
    assert calls == ["initial"]


def test_engine_metadata_cache_rejects_non_json_runtime_snapshots() -> None:
    with pytest.raises(ValueError, match="runtime snapshots"):
        resolve_engine_metadata_cache(
            existing_metadata=None,
            existing_runtime_sig=None,
            runtime_options={"engine": {"bad": object()}},
            runtime_info={},
            collect_metadata_fn=lambda: [],
        )


def _collect_metadata(calls: list[str], name: str) -> list[JsonObject]:
    calls.append(name)
    return [{"name": name}]
