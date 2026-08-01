from __future__ import annotations

import json
from pathlib import Path

import pytest

from shogiarena._core.contexts.spsa.adapters.accepted_best import (
    ACCEPTED_BEST_SCHEMA,
    persist_accepted_best,
)
from shogiarena._core.shared.kernel.content_hashing import sha256_file
from shogiarena._core.shared.kernel.run_artifact_hashes import canonical_sha256


def _write_sealed_provenance(run_dir: Path) -> dict[str, object]:
    handshake = {
        "schema_version": "shogiarena.spsa.tunable-handshake.v1",
        "status": "passed",
        "manifest": {"schema_version": "engine.tunables.v1", "engine": "tuned"},
    }
    spsa_dir = run_dir / "spsa"
    spsa_dir.mkdir(parents=True)
    (spsa_dir / "tunable_handshake.json").write_text(
        json.dumps(handshake, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    manifest = {
        "status": "provenance_sealed",
        "hashes": {"resume_hash": "a" * 64},
        "inputs": {
            "spsa_tunable_handshake": {
                "path": "spsa/tunable_handshake.json",
                "sha256": canonical_sha256(handshake),
            }
        },
        "engines": [
            {
                "name": "baseline",
                "artifact": "baseline-artifact",
                "bytes_hash": {
                    "engine_binary_sha256": "b" * 64,
                    "engine_config_sha256": "c" * 64,
                    "path_options": {},
                },
            },
            {
                "name": "tuned",
                "artifact": "tuned-artifact",
                "bytes_hash": {
                    "engine_binary_sha256": "d" * 64,
                    "engine_config_sha256": "e" * 64,
                    "path_options": {"EvalDir": {"sha256": "f" * 64}},
                },
            },
        ],
    }
    (run_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    return handshake


def _ledger_commit(*, acceptance: str, ltc_decision: object = None) -> dict[str, object]:
    return {
        "run_id": "run-1",
        "update_idx": 7,
        "created_at": "2026-07-31T00:00:00+00:00",
        "acceptance": acceptance,
        "parameters": [
            {
                "parameter_id": "p",
                "option_name": "USI_P",
                "value": 2.0,
            }
        ],
        "update_revision": 5,
        "ltc_decision": ltc_decision,
        "commit_id": "1" * 64,
    }


def test_accepted_best_rejects_non_positive_update_without_writing(tmp_path: Path) -> None:
    commit = _ledger_commit(acceptance="without_ltc")
    commit["update_idx"] = 0

    with pytest.raises(ValueError, match="positive"):
        persist_accepted_best(
            run_dir=tmp_path,
            ledger_commit=commit,
            parameter_wire_values={"USI_P": "2.0"},
            baseline_engine_count=1,
            tuned_engine_count=1,
        )

    assert not (tmp_path / "spsa" / "accepted-best.json").exists()


def test_accepted_best_seals_ledger_manifest_parameter_and_engine_provenance(tmp_path: Path) -> None:
    handshake = _write_sealed_provenance(tmp_path)

    path = persist_accepted_best(
        run_dir=tmp_path,
        ledger_commit=_ledger_commit(acceptance="without_ltc"),
        parameter_wire_values={"USI_P": "2.0"},
        baseline_engine_count=1,
        tuned_engine_count=1,
    )

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert path == tmp_path / "spsa" / "accepted-best.json"
    assert payload["schema_version"] == ACCEPTED_BEST_SCHEMA
    assert payload["ledger"] == {"commit_id": "1" * 64, "update_revision": 5}
    assert payload["parameters"] == [
        {
            "parameter_id": "p",
            "option_name": "USI_P",
            "value": 2.0,
            "wire_value": "2.0",
        }
    ]
    assert payload["provenance"]["run_manifest_sha256"] == sha256_file(tmp_path / "manifest.json")
    assert payload["provenance"]["tunable_manifest_sha256"] == canonical_sha256(handshake["manifest"])
    assert payload["provenance"]["baseline_engines"][0]["engine_binary_sha256"] == "b" * 64
    assert payload["provenance"]["tuned_engines"][0]["engine_binary_sha256"] == "d" * 64
    assert not (tmp_path / "spsa" / "best_params").exists()


def test_accepted_best_ltc_pass_requires_and_seals_decision_identity(tmp_path: Path) -> None:
    _write_sealed_provenance(tmp_path)
    decision = {
        "tested_update_idx": 7,
        "baseline_update_idx": 3,
        "decision": "pass",
        "evidence_digest": "9" * 64,
        "revision": 12,
    }

    path = persist_accepted_best(
        run_dir=tmp_path,
        ledger_commit=_ledger_commit(acceptance="ltc_pass", ltc_decision=decision),
        parameter_wire_values={"USI_P": 2},
        baseline_engine_count=1,
        tuned_engine_count=1,
    )

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["acceptance"] == {"kind": "ltc_pass", "ltc_decision": decision}
