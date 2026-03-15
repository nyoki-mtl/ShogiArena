from __future__ import annotations

import json

from shogiarena._core.contexts.spsa.application.dashboard.summary_accumulator import SummaryAccumulator
from shogiarena._core.contexts.spsa.application.dashboard.summary_cache_state import (
    SummaryCacheState,
    create_empty_summary_cache_state,
    load_summary_cache_state,
    persist_summary_cache_state,
)


def test_summary_cache_state_roundtrip(tmp_path) -> None:
    cache_path = tmp_path / "summary-cache.json"
    state = SummaryCacheState(
        aggregates=SummaryAccumulator.from_dict(
            {
                "wins": 3,
                "losses": 1,
                "updates_seen": [1, 2],
                "step_history": [0.1, 0.2],
            }
        ),
        session_uuid="session-1",
        events_offset=10,
        events_size=20,
        events_mtime_ns=30,
    )

    assert persist_summary_cache_state(cache_path, cache_version=4, state=state) is True

    loaded = load_summary_cache_state(cache_path, cache_version=4)

    assert loaded is not None
    assert loaded.session_uuid == "session-1"
    assert loaded.events_offset == 10
    assert loaded.events_size == 20
    assert loaded.events_mtime_ns == 30
    assert loaded.aggregates.wins == 3
    assert loaded.aggregates.losses == 1
    assert loaded.aggregates.updates_seen == {1, 2}
    assert list(loaded.aggregates.step_history) == [0.1, 0.2]


def test_load_summary_cache_state_returns_none_for_version_mismatch(tmp_path) -> None:
    cache_path = tmp_path / "summary-cache.json"
    cache_path.write_text(json.dumps({"version": 1}), encoding="utf-8")

    assert load_summary_cache_state(cache_path, cache_version=2) is None


def test_load_summary_cache_state_returns_none_for_malformed_json(tmp_path) -> None:
    cache_path = tmp_path / "summary-cache.json"
    cache_path.write_text("{bad", encoding="utf-8")

    assert load_summary_cache_state(cache_path, cache_version=1) is None


def test_load_summary_cache_state_defaults_missing_aggregates(tmp_path) -> None:
    cache_path = tmp_path / "summary-cache.json"
    cache_path.write_text(json.dumps({"version": 1, "session_uuid": "abc"}), encoding="utf-8")

    loaded = load_summary_cache_state(cache_path, cache_version=1)

    assert loaded is not None
    assert loaded.session_uuid == "abc"
    assert loaded.aggregates.to_dict() == create_empty_summary_cache_state().aggregates.to_dict()
