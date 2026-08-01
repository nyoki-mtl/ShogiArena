from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from shogiarena._core.interfaces.cli.main import CliError, main
from shogiarena._core.interfaces.cli.run.spsa import (
    LEGACY_SPSA_ARCHIVE_MESSAGE,
    _reject_legacy_spsa_archive,
)


def test_existing_spsa_archive_is_rejected_without_modification(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    current_path = tmp_path / "spsa" / "current.json"
    current_path.parent.mkdir(parents=True)
    state_path.write_bytes(b'{"type":"spsa","completed_updates":1}')
    current_path.write_bytes(b'{"schema_version":"shogiarena.spsa.current.v1"}')
    before = {path: path.read_bytes() for path in (state_path, current_path)}

    with pytest.raises(CliError, match="pre-1.2 JSON-only") as exc_info:
        _reject_legacy_spsa_archive(tmp_path)

    assert str(exc_info.value) == LEGACY_SPSA_ARCHIVE_MESSAGE
    assert {path: path.read_bytes() for path in before} == before


def test_empty_spsa_run_directory_is_writable(tmp_path: Path) -> None:
    _reject_legacy_spsa_archive(tmp_path)


def test_current_ledger_spsa_run_directory_is_resumable(tmp_path: Path) -> None:
    ledger_path = tmp_path / "spsa" / "ledger.sqlite3"
    ledger_path.parent.mkdir(parents=True)
    ledger_path.write_bytes(b"ledger-placeholder")

    _reject_legacy_spsa_archive(tmp_path)


def _write_relative_spsa_config(tmp_path: Path) -> Path:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "positions.sfen").write_text("startpos\n", encoding="utf-8")
    (config_dir / "space.yaml").write_text(
        """
schema_version: shogiarena.spsa.space.v1
target:
  engine_family: test
  protocol: usi_options
  required_options_policy: strict
  tunable_manifest:
    required: false
    command: usi_tunables
parameters:
  - id: ParamA
    target:
      option: ParamA
      value_encoding: decimal
    value_type: float
    initial: 1.0
    bounds:
      min: 0.0
      max: 2.0
    schedule:
      c_end: 0.1
      r_end: 0.1
""".lstrip(),
        encoding="utf-8",
    )
    config_path = config_dir / "spsa.yaml"
    config_path.write_text(
        """
engines:
  - artifact: test-engine/abcdef12
rules:
  initial_positions:
    type: file
    source: positions.sfen
spsa:
  space: space.yaml
  num_updates: 1
  pairs_per_update: 1
  inflight_factor: 1
  num_parallel: 1
""".lstrip(),
        encoding="utf-8",
    )
    return config_path


@pytest.mark.parametrize("mode_args", [[], ["--validate-only"], ["--dry-run"]])
def test_spsa_cli_reports_same_boundary_error_for_all_validation_modes(
    caplog: pytest.LogCaptureFixture,
    mode_args: list[str],
) -> None:
    config_path = Path(".sandbox/configs/run/spsa/halfkp512x2-8-64.yaml")
    expected = f"Invalid SPSA config: {config_path}: spsa.inflight_factor must be a positive integer"
    caplog.set_level(logging.ERROR, logger="shogiarena.cli")

    with pytest.raises(SystemExit) as exc_info:
        main(
            [
                "run",
                "spsa",
                str(config_path),
                "--spsa",
                "inflight_factor=0",
                *mode_args,
            ]
        )

    assert exc_info.value.code == 1
    assert [record.getMessage() for record in caplog.records if record.name == "shogiarena.cli"][-1] == expected


@pytest.mark.parametrize("mode", ["--validate-only", "--dry-run"])
def test_spsa_cli_override_preserves_config_relative_paths(tmp_path: Path, mode: str) -> None:
    config_path = _write_relative_spsa_config(tmp_path)

    main(
        [
            "run",
            "spsa",
            str(config_path),
            "--spsa",
            "num_parallel=1",
            mode,
        ]
    )


def test_manifest_only_spsa_archive_is_rejected_by_production_cli_without_modification(tmp_path: Path) -> None:
    config_path = _write_relative_spsa_config(tmp_path)
    run_dir = tmp_path / "partial-run"
    run_dir.mkdir()
    manifest_path = run_dir / "manifest.json"
    manifest_path.write_text(json.dumps({"schema_version": "partial"}), encoding="utf-8")
    before = manifest_path.read_bytes()

    with pytest.raises(SystemExit) as exc_info:
        main(
            [
                "run",
                "spsa",
                str(config_path),
                "--run-dir",
                str(run_dir),
                "--no-resume",
            ]
        )

    assert exc_info.value.code == 1
    assert manifest_path.read_bytes() == before
    assert sorted(path.relative_to(run_dir) for path in run_dir.rglob("*")) == [Path("manifest.json")]


def test_legacy_spsa_resume_is_rejected_by_public_cli_without_modification(tmp_path: Path) -> None:
    config_path = _write_relative_spsa_config(tmp_path)
    run_dir = tmp_path / "legacy-run"
    state_path = run_dir / "state.json"
    current_path = run_dir / "spsa" / "current.json"
    current_path.parent.mkdir(parents=True)
    state_path.write_bytes(b'{"type":"spsa","completed_updates":1}')
    current_path.write_bytes(b'{"schema_version":"shogiarena.spsa.current.v1"}')
    before = {
        str(path.relative_to(run_dir)): path.read_bytes() for path in sorted(run_dir.rglob("*")) if path.is_file()
    }

    with pytest.raises(SystemExit) as exc_info:
        main(
            [
                "run",
                "spsa",
                str(config_path),
                "--run-dir",
                str(run_dir),
            ]
        )

    assert exc_info.value.code == 1
    assert {
        str(path.relative_to(run_dir)): path.read_bytes() for path in sorted(run_dir.rglob("*")) if path.is_file()
    } == before
