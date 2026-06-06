from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from shogiarena._core.interfaces.boundaries.parsers import dashboard as dashboard_boundaries


def test_detect_worker_count(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    workers_dir = run_dir / "dashboard" / "data" / "workers"
    workers_dir.mkdir(parents=True)
    (workers_dir / "worker_0.js").write_text("", encoding="utf-8")
    (workers_dir / "worker_2.js").write_text("", encoding="utf-8")
    (workers_dir / "notes.txt").write_text("ignore", encoding="utf-8")

    assert dashboard_boundaries.detect_worker_count(run_dir) == 3


def test_infer_run_state_match_profile(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "manifest.json").write_text(
        json.dumps({"experiment_name": "match", "schedule": {"kind": "tournament"}}),
        encoding="utf-8",
    )

    assert dashboard_boundaries.infer_run_state_profile(run_dir) == "match"


def test_infer_dashboard_profiles_prefers_metadata(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "manifest.json").write_text(
        json.dumps({"sprt": True}),
        encoding="utf-8",
    )
    dashboard_dir = run_dir / "dashboard"
    dashboard_dir.mkdir()
    (dashboard_dir / ".dashboard_profiles.json").write_text(
        json.dumps({"profiles": ["generate", "tournament", "generate"]}),
        encoding="utf-8",
    )

    assert dashboard_boundaries.infer_dashboard_profiles(run_dir, config_mode=None) == ("generate", "tournament")


def test_load_tournament_config_for_dashboard_reads_profile(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    config_path = tmp_path / "tournament.yml"
    config_path.write_text("{}", encoding="utf-8")

    dummy_config = SimpleNamespace(
        tournament=SimpleNamespace(num_parallel=7),
        output_dir=tmp_path / "out",
        sprt=None,
        generate=None,
        experiment_name="match",
    )

    observed: dict[str, Path] = {}

    monkeypatch.setattr(
        dashboard_boundaries,
        "parse_tournament_config_file",
        lambda _path: {"ok": True},
    )
    monkeypatch.setattr(
        dashboard_boundaries,
        "build_tournament_run_config",
        lambda *_args, **_kwargs: dummy_config,
    )

    def _fake_latest_run_dir(config_file: Path, output_dir: Path) -> Path:
        observed["output_dir"] = output_dir
        return output_dir / "latest"

    monkeypatch.setattr(dashboard_boundaries, "latest_run_dir", _fake_latest_run_dir)

    run_dir, worker_count, profile = dashboard_boundaries.load_tournament_config_for_dashboard(config_path)

    assert worker_count == 7
    assert profile == "match"
    assert run_dir == tmp_path / "out" / "tournament" / "latest"
    assert observed["output_dir"] == tmp_path / "out" / "tournament"


def test_load_tournament_config_for_dashboard_rejects_invalid_num_parallel(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config_path = tmp_path / "tournament.yml"
    config_path.write_text("{}", encoding="utf-8")

    bad_config = SimpleNamespace(
        tournament=SimpleNamespace(num_parallel=0),
        output_dir=tmp_path / "out",
        sprt=None,
        generate=None,
        experiment_name=None,
    )
    monkeypatch.setattr(dashboard_boundaries, "parse_tournament_config_file", lambda _path: {"ok": True})
    monkeypatch.setattr(dashboard_boundaries, "build_tournament_run_config", lambda *_args, **_kwargs: bad_config)

    with pytest.raises(ValueError, match="positive tournament.num_parallel"):
        dashboard_boundaries.load_tournament_config_for_dashboard(config_path)


def test_load_spsa_config_for_dashboard_defaults_worker_count(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    config_path = tmp_path / "spsa.yml"
    config_path.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(
        dashboard_boundaries,
        "parse_spsa_config_file",
        lambda _path: {"spsa": {}},
    )
    monkeypatch.setattr(
        dashboard_boundaries,
        "latest_run_dir",
        lambda _path, _output_dir: tmp_path / "run",
    )

    run_dir, worker_count = dashboard_boundaries.load_spsa_config_for_dashboard(config_path)

    assert run_dir == tmp_path / "run"
    assert worker_count == 4


def test_load_spsa_config_for_dashboard_preserves_missing_run_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config_path = tmp_path / "spsa.yml"
    config_path.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(dashboard_boundaries, "parse_spsa_config_file", lambda _path: {"spsa": {"num_parallel": 2}})
    monkeypatch.setattr(dashboard_boundaries, "latest_run_dir", lambda _path, _output_dir: None)
    root_error = ValueError("original parse error")

    with pytest.raises(ValueError, match="run directory not found") as exc_info:
        dashboard_boundaries.load_spsa_config_for_dashboard(config_path, original_error=root_error)

    assert exc_info.value.__cause__ is root_error
