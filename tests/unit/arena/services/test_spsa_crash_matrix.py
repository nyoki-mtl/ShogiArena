from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

_WORKER = Path(__file__).parents[3] / "helpers" / "spsa_crash_worker.py"
_PRODUCTION_RECOVERY_WORKER = Path(__file__).parents[3] / "helpers" / "spsa_production_recovery_worker.py"
_CRASH_EXIT = 87


def _run_worker(
    run_dir: Path,
    *,
    decision: str,
    crash_point: str | None = None,
) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, str(_WORKER), str(run_dir), "--decision", decision]
    if crash_point is not None:
        command.extend(["--crash-point", crash_point])
    return subprocess.run(command, check=False, capture_output=True, text=True)


def _snapshot(run_dir: Path) -> dict[str, object]:
    return json.loads((run_dir / "snapshot.json").read_text(encoding="utf-8"))


def _run_dispatch_worker(
    run_dir: Path,
    *,
    crash_point: str | None = None,
) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, str(_WORKER), str(run_dir), "--dispatch-only"]
    if crash_point is not None:
        command.extend(["--crash-point", crash_point])
    return subprocess.run(command, check=False, capture_output=True, text=True)


def _run_production_recovery_worker(
    run_dir: Path,
    action: str,
    *,
    terminal: bool = False,
) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, str(_PRODUCTION_RECOVERY_WORKER), str(run_dir), action]
    if terminal:
        command.append("--terminal")
    return subprocess.run(command, check=False, capture_output=True, text=True, encoding="utf-8", errors="replace")


@pytest.mark.parametrize(
    ("crash_point", "decision"),
    [
        ("game_db_commit", "pass"),
        ("ledger_observation_commit", "pass"),
        ("pairs_complete", "pass"),
        ("candidate_saved", "pass"),
        ("ltc_pending", "pass"),
        ("ltc_result_persisted", "pass"),
        ("accepted_before_commit", "pass"),
        ("accepted_after_commit", "pass"),
        ("reverted_before_commit", "fail"),
        ("reverted_after_commit", "fail"),
        ("result_persisted", "pass"),
        ("terminal_before_commit", "pass"),
        ("terminal_after_commit", "pass"),
        ("terminal_artifact_published", "pass"),
        ("completion_artifact_published", "pass"),
        ("completed_flag_published", "pass"),
    ],
)
def test_process_crash_resume_converges_to_uninterrupted_authority(
    tmp_path: Path,
    crash_point: str,
    decision: str,
) -> None:
    canonical_dir = tmp_path / "canonical"
    canonical = _run_worker(canonical_dir, decision=decision)
    assert canonical.returncode == 0, canonical.stderr

    crash_dir = tmp_path / crash_point
    crashed = _run_worker(crash_dir, decision=decision, crash_point=crash_point)
    assert crashed.returncode == _CRASH_EXIT, crashed.stderr
    resumed = _run_worker(crash_dir, decision=decision)
    assert resumed.returncode == 0, resumed.stderr

    assert _snapshot(crash_dir) == _snapshot(canonical_dir)


def test_dispatch_invalidation_crash_resumes_before_starting_work(tmp_path: Path) -> None:
    canonical_dir = tmp_path / "dispatch-canonical"
    canonical = _run_dispatch_worker(canonical_dir)
    assert canonical.returncode == 0, canonical.stderr

    crash_dir = tmp_path / "dispatch-crash"
    crashed = _run_dispatch_worker(
        crash_dir,
        crash_point="dispatch_after_artifact_invalidation",
    )
    assert crashed.returncode == _CRASH_EXIT, crashed.stderr
    assert not (crash_dir / "dispatch.started").exists()
    resumed = _run_dispatch_worker(crash_dir)
    assert resumed.returncode == 0, resumed.stderr

    expected = json.loads((canonical_dir / "dispatch-snapshot.json").read_text(encoding="utf-8"))
    actual = json.loads((crash_dir / "dispatch-snapshot.json").read_text(encoding="utf-8"))
    assert actual == expected


@pytest.mark.parametrize("terminal", [False, True])
def test_production_prepare_recovers_finalize_only_crash(
    tmp_path: Path,
    *,
    terminal: bool,
) -> None:
    run_dir = tmp_path / ("ledger-terminal" if terminal else "final-update")
    crashed = _run_production_recovery_worker(run_dir, "setup", terminal=terminal)
    assert crashed.returncode == _CRASH_EXIT, crashed.stderr
    assert not (run_dir / "result.json").exists()
    assert not (run_dir / "completion_status.json").exists()
    assert not (run_dir / "completed.flag").exists()

    recovered = _run_production_recovery_worker(run_dir, "recover")
    assert recovered.returncode == 0, recovered.stderr
    snapshot = json.loads((run_dir / "recovery-snapshot.json").read_text(encoding="utf-8"))
    assert snapshot == {
        "result": True,
        "terminal": True,
        "completion": True,
        "completed_flag": True,
        "accepted_best": True,
    }
    accepted_best = json.loads((run_dir / "spsa" / "accepted-best.json").read_text(encoding="utf-8"))
    assert accepted_best["update_idx"] == 1
    baseline_digest = accepted_best["provenance"]["baseline_engines"][0]["engine_binary_sha256"]
    assert len(baseline_digest) == 64
    assert accepted_best["provenance"]["tuned_engines"][0]["engine_binary_sha256"] == baseline_digest


def test_production_prepare_recovers_archive_publish_before_manifest_seal_crash(tmp_path: Path) -> None:
    run_dir = tmp_path / "archive-publish"
    crashed = _run_production_recovery_worker(run_dir, "archive-publish-crash")
    assert crashed.returncode == _CRASH_EXIT, crashed.stderr
    assert (run_dir / "inputs" / "engine_configs").is_dir()
    assert not (run_dir / "state.json").exists()
    assert not (run_dir / "spsa" / "ledger.sqlite3").exists()

    recovered = _run_production_recovery_worker(run_dir, "archive-publish-recover")
    assert recovered.returncode == 0, recovered.stderr
    snapshot = json.loads((run_dir / "archive-recovery-snapshot.json").read_text(encoding="utf-8"))
    assert snapshot == {
        "archive": True,
        "state": True,
        "ledger": True,
    }
