"""Shared readers for run manifest resume/provenance state."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from pathlib import Path
from typing import cast

from shogiarena._core.shared.kernel.json_types import JsonObject
from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_optional_text, coerce_str
from shogiarena._core.shared.kernel.serialization import json_serialize


def read_run_manifest(path: Path, *, logger: logging.Logger | None = None) -> JsonObject | None:
    """Read a manifest object, returning ``None`` for absent or invalid files."""

    if not path.exists():
        if logger is not None:
            logger.info("Run manifest not found: %s", path)
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        if logger is not None:
            logger.warning("Failed to read manifest.json: %s", exc)
        return None
    if not isinstance(raw, Mapping):
        if logger is not None:
            logger.warning("manifest.json must contain an object: %s", path)
        return None
    return {str(key): json_serialize(value) for key, value in raw.items()}


def manifest_status(manifest: Mapping[str, object] | None) -> str | None:
    """Return the normalized manifest status."""

    if manifest is None:
        return None
    return coerce_str(manifest.get("status"))


def sealed_manifest_resume_hash(manifest: Mapping[str, object] | None) -> str | None:
    """Return the sealed resume hash when the manifest is resumable."""

    if manifest_status(manifest) != "provenance_sealed" or manifest is None:
        return None
    hashes = manifest.get("hashes")
    if not isinstance(hashes, Mapping):
        return None
    hashes_map = cast(Mapping[object, object], hashes)
    return coerce_optional_text(hashes_map.get("resume_hash"))


def read_sealed_manifest_resume_hash(path: Path, *, logger: logging.Logger | None = None) -> str | None:
    """Read a sealed manifest resume hash from ``path``."""

    return sealed_manifest_resume_hash(read_run_manifest(path, logger=logger))


def is_resumable_manifest(manifest: Mapping[str, object] | None) -> bool:
    """Return whether the manifest carries a usable sealed resume hash."""

    return sealed_manifest_resume_hash(manifest) is not None


__all__ = [
    "is_resumable_manifest",
    "manifest_status",
    "read_run_manifest",
    "read_sealed_manifest_resume_hash",
    "sealed_manifest_resume_hash",
]
