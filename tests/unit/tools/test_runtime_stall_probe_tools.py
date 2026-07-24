from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[3]


def _load_tool(name: str) -> ModuleType:
    path = ROOT / "tools" / f"{name}.py"
    spec = spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_dummy_engine_parses_startpos_and_sfen() -> None:
    dummy = _load_tool("runtime_stall_dummy_usi")
    start_move = dummy._choose_move("position startpos")
    assert start_move
    board = dummy._board_from_position(f"position startpos moves {start_move}")
    sfen = board.to_sfen()
    assert dummy._choose_move(f"position sfen {sfen}")


def test_dummy_engine_rejects_invalid_position() -> None:
    dummy = _load_tool("runtime_stall_dummy_usi")
    with pytest.raises(ValueError, match="unsupported position"):
        dummy._board_from_position("position kiwi")


def test_analyzer_correlates_severe_lag() -> None:
    analyzer = _load_tool("analyze_runtime_stall_probe")
    events = [
        {"event": "process_sample", "mono_ns": 1_000_000_000, "queues": {"progress": 10}, "rss_bytes": 100},
        {
            "event": "completion_lock_hold",
            "mono_ns": 1_100_000_000,
            "duration_ms": 120.0,
            "game_id": "g1",
        },
        {"event": "gc_pause", "mono_ns": 1_200_000_000, "duration_ms": 3.0, "generation": 2},
        {"event": "loop_lag", "mono_ns": 1_250_000_000, "duration_ms": 700.0},
        {"event": "thread_overshoot", "mono_ns": 1_260_000_000, "duration_ms": 650.0},
        {
            "event": "completion_total",
            "mono_ns": 1_300_000_000,
            "duration_ms": 130.0,
            "completed_games": 1,
        },
    ]
    summary = analyzer.analyze(events, severe_lag_ms=500.0, window_ms=500.0)
    assert summary["severe_lag_count"] == 1
    assert summary["completed_games"] == 1
    assert summary["queue_max"] == {"progress": 10}
    assert summary["gc_pause_by_generation"]["2"]["max_ms"] == 3.0
    nearby_types = {item["event"] for item in summary["severe_lag_neighborhoods"][0]["nearby"]}
    assert {"completion_lock_hold", "gc_pause", "thread_overshoot", "completion_total"} <= nearby_types


def test_percentile_interpolates() -> None:
    analyzer = _load_tool("analyze_runtime_stall_probe")
    assert analyzer._percentile([0.0, 10.0], 0.5) == 5.0
    assert analyzer._percentile([], 0.5) is None
