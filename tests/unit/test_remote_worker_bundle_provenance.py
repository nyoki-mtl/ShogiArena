from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

import pytest

from shogiarena._core.contexts.game_session.application.remote_worker_bundle import (
    REMOTE_WORKER_BUNDLE_FILENAME,
    prepare_remote_worker_bundle,
    worker_bundle_provenance,
)
from shogiarena._core.contexts.game_session.application.session.run_metadata_persistence_service import (
    RunMetadataPersistenceService,
)
from shogiarena._core.contexts.game_session.ports.worker_deployment import (
    WorkerBundleBuildResult,
    WorkerBundleManifest,
)
from shogiarena._core.contexts.instances.application.instance_config_models import (
    InstanceConfig,
    InstanceType,
)
from shogiarena._core.contexts.instances.application.instance_pool import InstancePool


def _remote_pool() -> InstancePool:
    pool = InstancePool()
    pool.add_instance(InstanceConfig(name="worker", type=InstanceType.SSH, engine_dir="", host="worker", slots=2))
    return pool


def _builder(payload: bytes, *, deployment_id: str = "4" * 64):
    def build(*, output_path: Path) -> WorkerBundleBuildResult:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(payload)
        manifest = WorkerBundleManifest(
            protocol_version="shogiarena.remote-worker.v1",
            package_version="1.2.0",
            wheel_filename="shogiarena.whl",
            wheel_sha256="1" * 64,
            lock_sha256="2" * 64,
            dependencies_sha256="3" * 64,
            target_os="linux",
            architecture="x86_64",
            python_version="3.12.10",
            uv_version="uv 0.11.29",
            source_policy="snapshot",
            deployment_id=deployment_id,
        )
        return WorkerBundleBuildResult(
            bundle_path=output_path,
            bundle_sha256=sha256(payload).hexdigest(),
            manifest=manifest,
        )

    return build


def _snapshot(directory: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(directory)): path.read_bytes() for path in sorted(directory.rglob("*")) if path.is_file()
    }


def test_resume_bundle_mismatch_rejects_without_modifying_run_artifacts(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / REMOTE_WORKER_BUNDLE_FILENAME).write_bytes(b"sealed-bundle")
    (run_dir / "manifest.json").write_text('{"status":"provenance_sealed"}', encoding="utf-8")
    before = _snapshot(run_dir)

    with pytest.raises(ValueError, match="does not match the sealed run bytes"):
        prepare_remote_worker_bundle(
            run_dir=run_dir,
            instance_pool=_remote_pool(),
            is_resume=True,
            builder=_builder(b"changed-bundle"),
        )

    assert _snapshot(run_dir) == before


def test_worker_bundle_is_recorded_in_manifest_and_resume_hash(tmp_path: Path) -> None:
    service = RunMetadataPersistenceService()
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    bundle = prepare_remote_worker_bundle(
        run_dir=run_dir,
        instance_pool=_remote_pool(),
        is_resume=False,
        builder=_builder(b"bundle-v1"),
    )
    assert bundle is not None
    provenance = worker_bundle_provenance(bundle)
    assert provenance is not None
    frozen = {"experiment_name": "remote-run", "engines": []}
    inputs = service.write_inputs_only_manifest(run_dir=run_dir, config_payload=frozen)
    first = service.build_sealed_hashes(
        inputs_config_payload=inputs.config_payload,
        resolved_config_payload={**frozen, "_remote_worker_bundle": provenance},
    )
    changed_bundle = _builder(b"bundle-v2", deployment_id="5" * 64)(output_path=(tmp_path / "changed.zip").resolve())
    changed = service.build_sealed_hashes(
        inputs_config_payload=inputs.config_payload,
        resolved_config_payload={
            **frozen,
            "_remote_worker_bundle": worker_bundle_provenance(changed_bundle),
        },
    )
    assert first.schedule_hash == changed.schedule_hash
    assert first.resume_hash != changed.resume_hash

    service.seal_provenance_manifest(
        run_dir=run_dir,
        inputs_config_payload=inputs.config_payload,
        resolved_config_payload={**frozen, "_remote_worker_bundle": provenance},
    )
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    recorded = manifest["inputs"]["remote_worker_bundle"]
    assert recorded["path"] == REMOTE_WORKER_BUNDLE_FILENAME
    assert recorded["bundle_sha256"] == bundle.bundle_sha256
    assert recorded["manifest"]["deployment_id"] == bundle.manifest.deployment_id
