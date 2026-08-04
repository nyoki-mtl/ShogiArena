from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path

import pytest

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


def test_artifact_integrity_follows_same_size_rewrites(tmp_path: Path) -> None:
    """artifact_health は integrity evidence なので、キャッシュが改竄を隠してはいけない。

    mtime と size を鍵にすると、同じ tick に同じサイズで書き換えられた場合に
    改竄を見逃す。ここでは mtime を意図的に据え置き、サイズも変えずに書き換える。
    """

    spsa_dir = tmp_path / "spsa"
    spsa_dir.mkdir()
    (spsa_dir / "meta.json").write_text(json.dumps({"session_uuid": "s"}), encoding="utf-8")
    tunable = {"status": "passed", "runtime_scope": "remote_runtime", "engine_name": "engine-a"}
    tunable_path = spsa_dir / "tunable_handshake.json"
    tunable_path.write_text(json.dumps(tunable), encoding="utf-8")
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "status": "provenance_sealed",
                "hashes": {"resume_hash": "r"},
                "inputs": {"spsa_tunable_handshake": {"sha256": canonical_sha256(tunable)}},
            }
        ),
        encoding="utf-8",
    )
    service = SpsaSummaryService(
        SpsaStore(run_dir=tmp_path),
        ledger_summary_loader=lambda: {},
        operational_status_loader=lambda: {"ledger": {"revision": 1}},
    )

    first = service.compute_summary()["operational_status"]
    assert first["artifact_health"]["tunable_handshake"] == "verified"  # type: ignore[index]

    original_stat = tunable_path.stat()
    # "engine-a" と "tampered" は同じ長さなので size は変わらない。
    tunable_path.write_text(
        json.dumps({**tunable, "engine_name": "tampered"}),
        encoding="utf-8",
    )
    assert tunable_path.stat().st_size == original_stat.st_size
    os.utime(tunable_path, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))

    tampered = service.compute_summary()["operational_status"]
    assert tampered["artifact_health"]["tunable_handshake"] == "mismatch"  # type: ignore[index]


def test_each_artifact_is_read_once_per_summary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """status と canonical digest は同一のバイト列から計算されなければならない。

    以前は `_load_object` と `_artifact_canonical_digest` が同じファイルを別々に読んでいた。
    2 回の読み取りの間に書き換えが起きると、1 回目のバイト列から作った artifact の
    canonical digest が 2 回目のバイト列の content key で digest キャッシュに入る。
    以後そのファイルが 2 回目の内容で安定している限り、キャッシュが当たり続け、
    **改竄後の内容が `verified` として報告され続ける**。

    読み取り回数を artifact ごとに 1 回へ固定して、この窓を閉じたことを表明する。
    """

    spsa_dir = tmp_path / "spsa"
    spsa_dir.mkdir()
    (spsa_dir / "meta.json").write_text(json.dumps({"session_uuid": "s"}), encoding="utf-8")
    fixed = {"status": "passed", "engines": []}
    tunable = {"status": "passed", "runtime_scope": "remote_runtime", "engine_name": "engine-a"}
    (spsa_dir / "fixed_option_preflight.json").write_text(json.dumps(fixed), encoding="utf-8")
    (spsa_dir / "tunable_handshake.json").write_text(json.dumps(tunable), encoding="utf-8")
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "status": "provenance_sealed",
                "hashes": {"resume_hash": "r"},
                "inputs": {
                    "spsa_tunable_handshake": {"sha256": canonical_sha256(tunable)},
                    "spsa_fixed_option_preflight": {"sha256": canonical_sha256(fixed)},
                },
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "completion_status.json").write_text(json.dumps({"status": "running"}), encoding="utf-8")
    service = SpsaSummaryService(
        SpsaStore(run_dir=tmp_path),
        ledger_summary_loader=lambda: {},
        operational_status_loader=lambda: {"ledger": {"revision": 1}},
    )

    reads: Counter[str] = Counter()
    original_read_bytes = Path.read_bytes

    def counting_read_bytes(self: Path) -> bytes:
        reads[self.name] += 1
        return original_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", counting_read_bytes)

    # 1 回目はどのキャッシュも空なので、最も読み取りが多くなる経路を通る。
    health = service.compute_summary()["operational_status"]["artifact_health"]  # type: ignore[index]
    assert health["tunable_handshake"] == "verified"  # type: ignore[index]
    assert health["fixed_option_preflight"] == "verified"  # type: ignore[index]

    assert reads == Counter(
        {
            "manifest.json": 1,
            "fixed_option_preflight.json": 1,
            "tunable_handshake.json": 1,
            "completion_status.json": 1,
        }
    )
