"""Remote worker deployment boundary contracts."""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict

WORKER_BUNDLE_SCHEMA_VERSION = "shogiarena.worker-bundle.v1"
WORKER_PYTHON_VERSION = "3.12.10"


class WorkerBundleManifest(BaseModel):
    """Immutable worker deploymentを識別するmanifest。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["shogiarena.worker-bundle.v1"] = WORKER_BUNDLE_SCHEMA_VERSION
    protocol_version: str
    package_version: str
    wheel_filename: str
    wheel_sha256: str
    lock_sha256: str
    dependencies_sha256: str
    target_os: Literal["linux"]
    architecture: Literal["x86_64"]
    python_version: str
    uv_version: str
    source_policy: Literal["snapshot"]
    deployment_id: str

    def canonical_bytes(self) -> bytes:
        """manifestのcanonical JSON bytesを返す。"""

        return json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

    @property
    def manifest_sha256(self) -> str:
        """canonical manifest digestを返す。"""

        return sha256(self.canonical_bytes()).hexdigest()


class WorkerBundleBuildResult(BaseModel):
    """Worker bundle buildの検証可能な出力。"""

    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)

    bundle_path: Path
    bundle_sha256: str
    manifest: WorkerBundleManifest


class WorkerBundleBuilderPort(Protocol):
    """Worker bundle builder adapter contract。"""

    def __call__(
        self,
        *,
        output_path: Path,
        project_root: Path | None = None,
        target_os: Literal["linux"] = "linux",
        architecture: Literal["x86_64"] = "x86_64",
        python_version: str = WORKER_PYTHON_VERSION,
    ) -> WorkerBundleBuildResult: ...


__all__ = [
    "WORKER_BUNDLE_SCHEMA_VERSION",
    "WORKER_PYTHON_VERSION",
    "WorkerBundleBuildResult",
    "WorkerBundleBuilderPort",
    "WorkerBundleManifest",
]
