from __future__ import annotations

import json
from pathlib import Path

import pytest

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
    assert not (failure_dir / "run_failures.json").exists()
    assert len(service.load_failures(run_dir)) == 1
    service.materialize_snapshots(run_dir)
    snapshot = json.loads((failure_dir / "run_failures.json").read_text(encoding="utf-8"))
    assert snapshot["schema_version"] == 1
    assert len(snapshot["failures"]) == 1
    assert snapshot["failures"][0]["failure_phase"] == "engine_start"
    assert (failure_dir / "run_failures.jsonl").read_text(encoding="utf-8").strip()

    startup = json.loads((failure_dir / "engine_startup_failure.json").read_text(encoding="utf-8"))
    assert startup["failures"][0]["diagnostic"]["executable"] == "/tmp/engine-a"

    service.append_failure(run_dir=run_dir, record=record)
    assert len(service.load_failures(run_dir)) == 2
    service.materialize_snapshots(run_dir)
    refreshed = json.loads((failure_dir / "run_failures.json").read_text(encoding="utf-8"))
    assert len(refreshed["failures"]) == 2


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


def test_snapshot_only_legacy_run_is_migrated_before_append(tmp_path: Path) -> None:
    run_dir = tmp_path / "legacy"
    failure_dir = run_dir / "failures"
    failure_dir.mkdir(parents=True)
    legacy = {"schema_version": 1, "failures": [{"game_id": "old", "failure_phase": "think"}]}
    (failure_dir / "run_failures.json").write_text(json.dumps(legacy), encoding="utf-8")
    service = RunFailureRecordService()
    service.append_failure(
        run_dir=run_dir,
        record=RunFailureRecord(
            game_id="new",
            scheduled_black_engine="a",
            scheduled_white_engine="b",
            failure_phase="think",
            exception_class="RuntimeError",
            short_message="failed",
        ),
    )
    assert [record["game_id"] for record in service.load_failures(run_dir)] == ["old", "new"]
    service.materialize_snapshots(run_dir)
    snapshot = json.loads((failure_dir / "run_failures.json").read_text(encoding="utf-8"))
    assert len(snapshot["failures"]) == 2


def test_failed_legacy_migration_keeps_complete_snapshot_for_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir = tmp_path / "legacy"
    failure_dir = run_dir / "failures"
    failure_dir.mkdir(parents=True)
    snapshot_path = failure_dir / "run_failures.json"
    snapshot_path.write_text(
        json.dumps({"schema_version": 1, "failures": [{"game_id": "old", "failure_phase": "think"}]}),
        encoding="utf-8",
    )
    jsonl_path = failure_dir / "run_failures.jsonl"
    service = RunFailureRecordService()
    record = RunFailureRecord(
        game_id="new",
        scheduled_black_engine="a",
        scheduled_white_engine="b",
        failure_phase="think",
        exception_class="RuntimeError",
        short_message="failed",
    )

    with monkeypatch.context() as patch:

        def fail_replace(_self: Path, _target: Path) -> Path:
            raise OSError("migration interrupted")

        patch.setattr(Path, "replace", fail_replace)
        service.append_failure(run_dir=run_dir, record=record)

    assert not jsonl_path.exists()
    assert [item["game_id"] for item in service.load_failures(run_dir)] == ["old"]
    service.append_failure(run_dir=run_dir, record=record)
    assert [item["game_id"] for item in service.load_failures(run_dir)] == ["old", "new"]


def test_append_discards_torn_final_jsonl_line(tmp_path: Path) -> None:
    service = RunFailureRecordService()
    run_dir = tmp_path / "run"
    record = RunFailureRecord(
        game_id="new",
        scheduled_black_engine="a",
        scheduled_white_engine="b",
        failure_phase="think",
        exception_class="RuntimeError",
        short_message="failed",
    )
    service.append_failure(run_dir=run_dir, record=record)
    jsonl_path = run_dir / "failures" / "run_failures.jsonl"
    with jsonl_path.open("ab") as handle:
        handle.write(b'{"incomplete":')

    service.append_failure(run_dir=run_dir, record=record)

    lines = jsonl_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert all(json.loads(line)["game_id"] == "new" for line in lines)


def test_reader_keeps_complete_records_before_torn_utf8_tail(tmp_path: Path) -> None:
    service = RunFailureRecordService()
    run_dir = tmp_path / "run"
    service.append_failure(
        run_dir=run_dir,
        record=RunFailureRecord(
            game_id="g1",
            scheduled_black_engine="a",
            scheduled_white_engine="b",
            failure_phase="think",
            exception_class="RuntimeError",
            short_message="失敗",
        ),
    )
    jsonl_path = run_dir / "failures" / "run_failures.jsonl"
    with jsonl_path.open("ab") as handle:
        handle.write(b'{"short_message":"\xe5')

    assert [record["game_id"] for record in service.load_failures(run_dir)] == ["g1"]
    service.materialize_snapshots(run_dir)
    snapshot = json.loads((run_dir / "failures" / "run_failures.json").read_text(encoding="utf-8"))
    assert [record["game_id"] for record in snapshot["failures"]] == ["g1"]
