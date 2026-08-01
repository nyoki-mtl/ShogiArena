"""Preplaced remote artifact mapping generation."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from shogiarena._core.shared.kernel.content_hashing import sha256_file, sha256_path
from shogiarena._core.shared.kernel.json_types import JsonObject


@dataclass(frozen=True, slots=True)
class PreplacedArtifactSource:
    """Logical ID生成に必要なlocal sourceとremote配置先。"""

    engine_name: str
    local_path: Path
    remote_path: str


def remote_engine_artifact_logical_id(engine_name: str) -> str:
    """Remote Linux worker用engine artifact logical IDを返す。"""

    if not engine_name:
        raise ValueError("engine name must not be empty")
    return f"{engine_name}-linux"


def content_addressed_resource_logical_id(logical_prefix: str, digest: str) -> str:
    """Content digestを含むpath resource logical IDを返す。"""

    if not logical_prefix:
        raise ValueError("logical prefix must not be empty")
    return f"{logical_prefix}-resource-{digest[:12]}"


def build_preplaced_resource_mapping(
    *,
    engines: Sequence[PreplacedArtifactSource],
    resources: Sequence[PreplacedArtifactSource],
) -> JsonObject:
    """Local artifactからpreplaced provisioning用JSON mappingを作る。"""

    if not engines:
        raise ValueError("at least one engine artifact is required")
    engine_names = {source.engine_name for source in engines}
    mapping: JsonObject = {}
    for source in engines:
        local_path = source.local_path.resolve()
        digest = sha256_file(local_path)
        logical_id = remote_engine_artifact_logical_id(source.engine_name)
        _add_mapping_entry(mapping, logical_id, remote_path=source.remote_path, digest=digest)
    for source in resources:
        if source.engine_name not in engine_names:
            raise ValueError(f"resource references an undeclared engine: {source.engine_name}")
        local_path = source.local_path.resolve()
        _, digest = sha256_path(local_path)
        logical_id = content_addressed_resource_logical_id(
            remote_engine_artifact_logical_id(source.engine_name),
            digest,
        )
        _add_mapping_entry(mapping, logical_id, remote_path=source.remote_path, digest=digest)
    return mapping


def _add_mapping_entry(mapping: JsonObject, logical_id: str, *, remote_path: str, digest: str) -> None:
    entry: JsonObject = {"path": remote_path, "sha256": digest}
    previous = mapping.get(logical_id)
    if previous is not None and previous != entry:
        raise ValueError(f"conflicting preplaced mapping for logical ID: {logical_id}")
    mapping[logical_id] = entry


__all__ = [
    "PreplacedArtifactSource",
    "build_preplaced_resource_mapping",
    "content_addressed_resource_logical_id",
    "remote_engine_artifact_logical_id",
]
