"""Run-scoped remote worker bundle preparation and provenance."""

from __future__ import annotations

import tempfile
from collections.abc import Callable, Sequence
from hashlib import sha256
from pathlib import Path
from typing import Protocol

from shogiarena._core.contexts.game_session.application.worker_bundle_builder import build_worker_bundle
from shogiarena._core.contexts.game_session.ports.worker_deployment import WorkerBundleBuildResult
from shogiarena._core.shared.kernel.json_types import JsonObject

REMOTE_WORKER_BUNDLE_FILENAME = "remote-worker-bundle.zip"


class _RemoteInstance(Protocol):
    @property
    def is_ssh(self) -> bool: ...


class _InstancePool(Protocol):
    def list_instances(self) -> Sequence[_RemoteInstance]: ...


def prepare_remote_worker_bundle(
    *,
    run_dir: Path,
    instance_pool: _InstancePool | None,
    is_resume: bool,
    builder: Callable[..., WorkerBundleBuildResult] = build_worker_bundle,
) -> WorkerBundleBuildResult | None:
    """SSH実行用bundleをseal前に生成し、resume時は既存bytesを非破壊で検証する。"""

    if instance_pool is None or not any(instance.is_ssh for instance in instance_pool.list_instances()):
        return None

    bundle_path = (run_dir / REMOTE_WORKER_BUNDLE_FILENAME).resolve()
    if not is_resume:
        return builder(output_path=bundle_path)

    with tempfile.TemporaryDirectory(prefix="shogiarena-worker-resume-") as temporary:
        staged = builder(output_path=(Path(temporary) / REMOTE_WORKER_BUNDLE_FILENAME).resolve())
        if not bundle_path.is_file():
            raise ValueError(
                "Remote worker bundle is missing from the sealed run; "
                f"expected digest {staged.bundle_sha256}, actual digest missing. "
                "Restore the sealed remote-worker-bundle.zip or use --no-resume to start a fresh run."
            )
        actual_digest = _sha256_file(bundle_path)
        if actual_digest != staged.bundle_sha256:
            raise ValueError(
                "Remote worker bundle does not match the sealed run bytes; "
                f"expected digest {staged.bundle_sha256}, actual digest {actual_digest}. "
                "Restore the sealed remote-worker-bundle.zip or use --no-resume to start a fresh run."
            )
        return staged.model_copy(update={"bundle_path": bundle_path})


def worker_bundle_provenance(bundle: WorkerBundleBuildResult | None) -> JsonObject | None:
    """resume authorityへ含めるpath非依存のworker bundle provenanceを返す。"""

    if bundle is None:
        return None
    return {
        "bundle_sha256": bundle.bundle_sha256,
        "manifest_sha256": bundle.manifest.manifest_sha256,
        "manifest": bundle.manifest.model_dump(mode="json"),
    }


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


__all__ = [
    "REMOTE_WORKER_BUNDLE_FILENAME",
    "prepare_remote_worker_bundle",
    "worker_bundle_provenance",
]
