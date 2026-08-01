from __future__ import annotations

import json
from pathlib import Path

from shogiarena._core.contexts.dashboard.adapters.spsa.summary_service import SpsaSummaryService
from shogiarena._core.contexts.dashboard.application.spsa.data_store import SpsaStore
from shogiarena._core.shared.kernel.run_artifact_hashes import canonical_sha256


def test_summary_merges_ledger_and_sealed_artifact_status(tmp_path: Path) -> None:
    spsa_dir = tmp_path / "spsa"
    spsa_dir.mkdir()
    (spsa_dir / "meta.json").write_text(
        json.dumps({"session_uuid": "session-2", "experiment_name": "run-title", "num_updates": 3}),
        encoding="utf-8",
    )
    fixed = {
        "status": "passed",
        "engines": [{"runtime_evidence": {"scope": "remote_runtime", "status": "covered_by_local_source"}}],
    }
    tunable = {"status": "passed", "runtime_scope": "remote_runtime", "engine_name": "engine-a"}
    (spsa_dir / "fixed_option_preflight.json").write_text(json.dumps(fixed), encoding="utf-8")
    (spsa_dir / "tunable_handshake.json").write_text(json.dumps(tunable), encoding="utf-8")
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "status": "provenance_sealed",
                "hashes": {"resume_hash": "resume-sealed"},
                "inputs": {
                    "spsa_tunable_handshake": {"sha256": canonical_sha256(tunable)},
                    "spsa_fixed_option_preflight": {"sha256": canonical_sha256(fixed)},
                },
                "engines": [
                    {
                        "name": "engine-a",
                        "bytes_hash": {"engine_binary_sha256": "b" * 64},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    ledger_status = {
        "run_id": "run-1",
        "completion": {"status": "running", "last_committed_update": 1, "pending_stage": "LTC_RUNNING"},
        "ledger": {"schema_version": "ledger-v1", "revision": 7},
    }
    service = SpsaSummaryService(
        SpsaStore(run_dir=tmp_path),
        ledger_summary_loader=lambda: {},
        operational_status_loader=lambda: ledger_status,
    )

    operational = service.compute_summary()["operational_status"]

    assert operational["run_id"] == "run-1"
    assert operational["session_id"] == "session-2"
    assert operational["manifest"] == {
        "status": "provenance_sealed",
        "schema_version": 2,
        "resume_hash": "resume-sealed",
    }
    assert operational["fixed_option_preflight"] == {
        "status": "passed",
        "evidence_scopes": ["remote_runtime"],
    }
    assert operational["tunable_manifest"]["engine_digests"] == {"engine-a": "b" * 64}  # type: ignore[index]
    assert operational["artifact_health"] == {
        "status": "healthy",
        "revision": 7,
        "fixed_option_preflight": "verified",
        "tunable_handshake": "verified",
    }

    (spsa_dir / "tunable_handshake.json").write_text(
        json.dumps({**tunable, "engine_name": "tampered"}), encoding="utf-8"
    )
    degraded = service.compute_summary()["operational_status"]
    assert degraded["artifact_health"] == {
        "status": "degraded",
        "revision": 7,
        "fixed_option_preflight": "verified",
        "tunable_handshake": "mismatch",
    }
