from __future__ import annotations

import json
from pathlib import Path

from shogiarena._core.contexts.game_session.application.session.run_failure_record_service import (
    RunFailureRecordService,
)
from shogiarena._core.contexts.game_session.domain.failure_records import RunFailureRecord


def test_append_failure_writes_snapshot_jsonl_and_startup_diagnostic(tmp_path: Path) -> None:
    service = RunFailureRecordService()
    run_dir = tmp_path / "run"
    record = RunFailureRecord(
        game_id="g1",
        scheduled_black_engine="engine-a",
        scheduled_white_engine="engine-b",
        failure_phase="engine_start",
        exception_class="UsiEngineStartError",
        short_message="failed",
        engine="engine-a",
        diagnostic={"executable": "/tmp/engine-a"},
    )

    service.append_failure(run_dir=run_dir, record=record)

    failure_dir = run_dir / "failures"
    snapshot = json.loads((failure_dir / "run_failures.json").read_text(encoding="utf-8"))
    assert snapshot["schema_version"] == 1
    assert len(snapshot["failures"]) == 1
    assert snapshot["failures"][0]["failure_phase"] == "engine_start"
    assert (failure_dir / "run_failures.jsonl").read_text(encoding="utf-8").strip()

    startup = json.loads((failure_dir / "engine_startup_failure.json").read_text(encoding="utf-8"))
    assert startup["failures"][0]["diagnostic"]["executable"] == "/tmp/engine-a"


def test_failure_counts_by_phase() -> None:
    counts = RunFailureRecordService.failure_counts_by_phase(
        [
            {"failure_phase": "engine_start"},
            {"failure_phase": "engine_start"},
            {"failure_phase": "think"},
            {"failure_phase": "bogus"},
        ]
    )

    assert counts == {"engine_start": 2, "think": 1, "unknown": 1}
