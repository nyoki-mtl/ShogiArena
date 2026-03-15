from __future__ import annotations

import json
from pathlib import Path

from shogiarena._core.contexts.game_session.application.session.run_metadata_persistence_service import (
    RunMetadataPersistenceService,
)


def test_write_run_metadata_files_creates_expected_files(tmp_path: Path) -> None:
    service = RunMetadataPersistenceService()
    run_dir = tmp_path / "run-1"
    run_dir.mkdir(parents=True)

    service.write_run_metadata_files(
        run_dir=run_dir,
        config_payload={"experiment_name": "exp-a", "games": 10},
        package_name="shogiarena",
    )

    resolved_path = run_dir / "config_resolved.yaml"
    metadata_path = run_dir / "run_metadata.json"
    assert resolved_path.exists()
    assert metadata_path.exists()
    assert "experiment_name: exp-a" in resolved_path.read_text(encoding="utf-8")

    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert "shogiarena_version" in metadata
    assert "generated_at_iso" in metadata


def test_write_run_metadata_files_does_not_overwrite_existing_files(tmp_path: Path) -> None:
    service = RunMetadataPersistenceService()
    run_dir = tmp_path / "run-2"
    run_dir.mkdir(parents=True)

    resolved_path = run_dir / "config_resolved.yaml"
    metadata_path = run_dir / "run_metadata.json"
    resolved_path.write_text("existing: true\n", encoding="utf-8")
    metadata_path.write_text('{"shogiarena_version":"fixed","generated_at_iso":"fixed"}\n', encoding="utf-8")

    service.write_run_metadata_files(
        run_dir=run_dir,
        config_payload={"experiment_name": "exp-b"},
        package_name="shogiarena",
    )

    assert resolved_path.read_text(encoding="utf-8") == "existing: true\n"
    assert metadata_path.read_text(encoding="utf-8") == '{"shogiarena_version":"fixed","generated_at_iso":"fixed"}\n'
