from __future__ import annotations

import json
from pathlib import Path

import pytest

from shogiarena._core.contexts.game_session.application.session.run_metadata_persistence_service import (
    RunManifestSealError,
    RunMetadataPersistenceService,
)


def _write_two_phase_manifest(
    service: RunMetadataPersistenceService,
    *,
    run_dir: Path,
    config_payload: dict[str, object],
) -> None:
    inputs = service.write_inputs_only_manifest(run_dir=run_dir, config_payload=config_payload)
    service.seal_provenance_manifest(
        run_dir=run_dir,
        inputs_config_payload=inputs.config_payload,
        resolved_config_payload=config_payload,
    )


def test_two_phase_manifest_creates_expected_files(tmp_path: Path) -> None:
    service = RunMetadataPersistenceService()
    run_dir = tmp_path / "run-1"
    run_dir.mkdir(parents=True)

    _write_two_phase_manifest(
        service,
        run_dir=run_dir,
        config_payload={"experiment_name": "exp-a", "games": 10},
    )

    resolved_path = run_dir / "inputs" / "config_resolved.yaml"
    manifest_path = run_dir / "manifest.json"
    assert resolved_path.exists()
    assert manifest_path.exists()
    assert not (run_dir / "run_metadata.json").exists()
    assert not (run_dir / "run_manifest.json").exists()
    assert "experiment_name: exp-a" in resolved_path.read_text(encoding="utf-8")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 2
    assert manifest["status"] == "provenance_sealed"
    assert "shogiarena_version" in manifest
    assert manifest["run_dir"] == str(run_dir)
    assert "orchestrator" in manifest["environment"]
    assert manifest["state"]["path"] == "state.json"
    assert manifest["database"]["path"] == "game.db"
    assert manifest["inputs"]["config_resolved"] == "inputs/config_resolved.yaml"
    assert manifest["hashes"]["config_fingerprint"]
    assert manifest["hashes"]["schedule_hash"]


def test_write_inputs_only_manifest_does_not_overwrite_existing_files(tmp_path: Path) -> None:
    service = RunMetadataPersistenceService()
    run_dir = tmp_path / "run-2"
    run_dir.mkdir(parents=True)

    resolved_path = run_dir / "inputs" / "config_resolved.yaml"
    manifest_path = run_dir / "manifest.json"
    resolved_path.parent.mkdir(parents=True)
    resolved_path.write_text("existing: true\n", encoding="utf-8")
    manifest_path.write_text('{"shogiarena_version":"manifest-fixed"}\n', encoding="utf-8")

    service.write_inputs_only_manifest(
        run_dir=run_dir,
        config_payload={"experiment_name": "exp-b"},
    )

    assert resolved_path.read_text(encoding="utf-8") == "existing: true\n"
    assert manifest_path.read_text(encoding="utf-8") == '{"shogiarena_version":"manifest-fixed"}\n'


def test_write_run_manifest_hashes_engine_binary(tmp_path: Path) -> None:
    service = RunMetadataPersistenceService()
    run_dir = tmp_path / "run-3"
    run_dir.mkdir(parents=True)
    engine_path = tmp_path / "engine.bin"
    engine_path.write_bytes(b"engine-binary")

    _write_two_phase_manifest(
        service,
        run_dir=run_dir,
        config_payload={
            "tournament": {"scheduler": "round_robin", "games_per_pair": 2, "num_parallel": 1, "seed": 42},
            "rules": {"repetition_occurrences_to_draw": 4},
            "engines": [
                {
                    "name": "engine-a",
                    "engine_path": str(engine_path),
                    "options": {"USI_Hash": "256", "EvalDir": str(tmp_path)},
                    "path_options": ["EvalDir"],
                },
                {"name": "engine-b", "engine_path": str(tmp_path / "missing-engine")},
            ],
        },
    )

    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["tournament"]["total_scheduled_games"] == 2
    engines = {entry["name"]: entry for entry in manifest["engines"]}
    assert engines["engine-a"]["resolved_paths"]["engine_path"] == str(engine_path)
    assert engines["engine-a"]["resolved_paths"]["working_directory"] == str(tmp_path)
    assert engines["engine-a"]["bytes_hash"]["engine_binary_sha256"]
    assert engines["engine-a"]["effective_options"] == {"USI_Hash": "256"}
    assert engines["engine-a"]["resolved_paths"]["path_options"] == {"EvalDir": str(tmp_path)}
    assert engines["engine-a"]["bytes_hash"]["path_options"]["EvalDir"]
    assert engines["engine-b"]["bytes_hash"]["engine_binary_sha256"] is None
    assert manifest["hashes"]["provenance_hash"]


def test_write_inputs_only_manifest_persists_pre_seal_contract(tmp_path: Path) -> None:
    service = RunMetadataPersistenceService()
    run_dir = tmp_path / "run-4"
    run_dir.mkdir(parents=True)

    result = service.write_inputs_only_manifest(
        run_dir=run_dir,
        config_payload={"experiment_name": "exp-pre", "engines": [{"name": "a", "engine_path": "/missing"}]},
    )

    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert result.is_written is True
    assert manifest["status"] == "inputs_only"
    assert manifest["inputs_hash"] == result.hashes.config_fingerprint
    assert manifest["hashes"]["provenance_hash"] is None
    assert manifest["hashes"]["resume_hash"] is None
    assert manifest["engines"] == []


def test_seal_provenance_manifest_keeps_frozen_inputs_after_artifact_resolution(tmp_path: Path) -> None:
    service = RunMetadataPersistenceService()
    run_dir = tmp_path / "run-5"
    run_dir.mkdir(parents=True)
    engine_path = tmp_path / "engine.bin"
    engine_path.write_bytes(b"engine")
    frozen = {"experiment_name": "exp-artifact", "engines": [{"name": "engine-a", "artifact": "repo/abcdef"}]}
    resolved = {
        "experiment_name": "exp-artifact",
        "engines": [{"name": "engine-a", "artifact": "repo/abcdef", "engine_path": str(engine_path)}],
    }

    inputs = service.write_inputs_only_manifest(run_dir=run_dir, config_payload=frozen)
    sealed = service.seal_provenance_manifest(
        run_dir=run_dir,
        inputs_config_payload=inputs.config_payload,
        resolved_config_payload=resolved,
    )

    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "provenance_sealed"
    assert manifest["inputs_hash"] == inputs.hashes.config_fingerprint
    assert manifest["hashes"]["resume_hash"] == sealed.hashes.resume_hash
    assert manifest["engines"][0]["resolved_paths"]["engine_path"] == str(engine_path)


def test_seal_provenance_manifest_links_spsa_handshake_evidence(tmp_path: Path) -> None:
    service = RunMetadataPersistenceService()
    run_dir = tmp_path / "run-spsa"
    run_dir.mkdir(parents=True)
    frozen = {"experiment_name": "spsa", "baseline": [], "tuned": [], "num_updates": 1}
    normalized_space = {
        "schema_version": "shogiarena.spsa.space.v1",
        "target": {"protocol": "usi_options"},
        "parameters": [],
    }
    handshake = {
        "schema_version": "shogiarena.spsa.tunable-handshake.v1",
        "status": "passed",
        "normalized_space": normalized_space,
    }
    fixed_option_preflight = {
        "schema_version": "shogiarena.spsa.fixed_option_preflight.v2",
        "status": "passed",
    }
    (run_dir / "spsa").mkdir()
    (run_dir / "spsa" / "space.normalized.json").write_text(
        json.dumps(normalized_space),
        encoding="utf-8",
    )
    inputs = service.write_inputs_only_manifest(run_dir=run_dir, config_payload=frozen)

    service.seal_provenance_manifest(
        run_dir=run_dir,
        inputs_config_payload=inputs.config_payload,
        resolved_config_payload={
            **frozen,
            "_spsa_tunable_handshake": handshake,
            "_spsa_fixed_option_preflight": fixed_option_preflight,
        },
    )

    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["inputs"]["spsa_tunable_handshake"]["path"] == "spsa/tunable_handshake.json"
    assert len(manifest["inputs"]["spsa_tunable_handshake"]["sha256"]) == 64
    assert manifest["inputs"]["spsa_normalized_space"]["path"] == "spsa/space.normalized.json"
    assert len(manifest["inputs"]["spsa_normalized_space"]["sha256"]) == 64
    assert manifest["inputs"]["spsa_fixed_option_preflight"]["path"] == "spsa/fixed_option_preflight.json"
    assert len(manifest["inputs"]["spsa_fixed_option_preflight"]["sha256"]) == 64

    (run_dir / "spsa" / "space.normalized.json").write_text("{}", encoding="utf-8")
    with pytest.raises(RunManifestSealError, match="normalized space artifact digest mismatch"):
        service.seal_provenance_manifest(
            run_dir=run_dir,
            inputs_config_payload=inputs.config_payload,
            resolved_config_payload={
                **frozen,
                "_spsa_tunable_handshake": handshake,
                "_spsa_fixed_option_preflight": fixed_option_preflight,
            },
        )


def test_seal_provenance_manifest_rejects_inputs_hash_mismatch(tmp_path: Path) -> None:
    service = RunMetadataPersistenceService()
    run_dir = tmp_path / "run-6"
    run_dir.mkdir(parents=True)
    inputs = service.write_inputs_only_manifest(run_dir=run_dir, config_payload={"experiment_name": "exp-a"})

    try:
        service.seal_provenance_manifest(
            run_dir=run_dir,
            inputs_config_payload={**inputs.config_payload, "experiment_name": "exp-b"},
            resolved_config_payload={"experiment_name": "exp-b"},
        )
    except RunManifestSealError as exc:
        assert "inputs_hash" in str(exc)
    else:  # pragma: no cover - defensive
        raise AssertionError("seal should fail when frozen inputs do not match phase 1")


def test_seal_provenance_manifest_rejects_existing_resume_hash_mismatch(tmp_path: Path) -> None:
    service = RunMetadataPersistenceService()
    run_dir = tmp_path / "run-7"
    run_dir.mkdir(parents=True)
    engine_path = tmp_path / "engine.bin"
    engine_path.write_bytes(b"before")
    payload = {"engines": [{"name": "engine-a", "engine_path": str(engine_path)}]}
    inputs = service.write_inputs_only_manifest(run_dir=run_dir, config_payload=payload)
    service.seal_provenance_manifest(
        run_dir=run_dir,
        inputs_config_payload=inputs.config_payload,
        resolved_config_payload=payload,
    )

    engine_path.write_bytes(b"after")

    try:
        service.seal_provenance_manifest(
            run_dir=run_dir,
            inputs_config_payload=inputs.config_payload,
            resolved_config_payload=payload,
        )
    except RunManifestSealError as exc:
        assert "resume_hash" in str(exc)
    else:  # pragma: no cover - defensive
        raise AssertionError("seal should fail when existing sealed provenance changes")


def test_seal_provenance_manifest_wraps_corrupt_manifest(tmp_path: Path) -> None:
    service = RunMetadataPersistenceService()
    run_dir = tmp_path / "run-8"
    run_dir.mkdir(parents=True)
    (run_dir / "manifest.json").write_text("{broken", encoding="utf-8")

    try:
        service.seal_provenance_manifest(
            run_dir=run_dir,
            inputs_config_payload={"experiment_name": "exp"},
            resolved_config_payload={"experiment_name": "exp"},
        )
    except RunManifestSealError as exc:
        assert "failed to read manifest.json" in str(exc)
    else:  # pragma: no cover - defensive
        raise AssertionError("seal should wrap corrupt manifest read failures")
