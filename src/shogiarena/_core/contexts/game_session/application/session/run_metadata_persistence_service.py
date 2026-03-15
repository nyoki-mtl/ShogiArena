"""Run metadata persistence service shared by session runners."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from importlib import metadata as importlib_metadata
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


class RunMetadataPersistenceService:
    """Persist resolved config and run metadata files for a run directory."""

    def write_run_metadata_files(
        self,
        *,
        run_dir: Path,
        config_payload: Mapping[str, object],
        package_name: str = "shogiarena",
    ) -> None:
        self._write_config_resolved(run_dir=run_dir, config_payload=config_payload)
        self._write_run_metadata(run_dir=run_dir, package_name=package_name)

    def _write_config_resolved(
        self,
        *,
        run_dir: Path,
        config_payload: Mapping[str, object],
    ) -> None:
        resolved_path = run_dir / "config_resolved.yaml"
        if resolved_path.exists():
            return
        try:
            resolved_path.write_text(yaml.safe_dump(dict(config_payload), sort_keys=False), encoding="utf-8")
        except (OSError, TypeError, ValueError) as exc:
            logger.exception("Failed to write resolved config to %s: %s", resolved_path, exc)

    def _write_run_metadata(
        self,
        *,
        run_dir: Path,
        package_name: str,
    ) -> None:
        metadata_path = run_dir / "run_metadata.json"
        if metadata_path.exists():
            return
        metadata = {
            "shogiarena_version": self._detect_package_version(package_name),
            "generated_at_iso": datetime.now(UTC).isoformat(),
        }
        try:
            metadata_path.write_text(
                json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        except (OSError, TypeError, ValueError) as exc:
            logger.exception("Failed to write run metadata to %s: %s", metadata_path, exc)

    @staticmethod
    def _detect_package_version(package_name: str) -> str:
        try:
            return importlib_metadata.version(package_name)
        except importlib_metadata.PackageNotFoundError:  # pragma: no cover - environment without package
            return "unknown"


__all__ = ["RunMetadataPersistenceService"]
