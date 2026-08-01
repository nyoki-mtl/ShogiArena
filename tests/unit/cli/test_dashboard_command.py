from __future__ import annotations

import argparse
from pathlib import Path
from types import SimpleNamespace

import pytest

from shogiarena._core.interfaces.cli.dashboard import command as dashboard_command
from shogiarena._core.interfaces.cli.main import CliError


@pytest.mark.asyncio
async def test_dashboard_serve_does_not_materialize_assets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "game.db").write_text("", encoding="utf-8")
    workers_dir = run_dir / "dashboard" / "data" / "workers"
    workers_dir.mkdir(parents=True)
    (workers_dir / "worker_0.js").write_text("", encoding="utf-8")
    before = {path.relative_to(run_dir): path.read_bytes() for path in run_dir.rglob("*") if path.is_file()}

    class _StopServer:
        async def start(self) -> None:
            raise RuntimeError("stop before server startup")

    monkeypatch.setattr(
        dashboard_command,
        "build_default_root",
        lambda: SimpleNamespace(api_server_factory=lambda *_args, **_kwargs: _StopServer()),
    )

    with pytest.raises(RuntimeError, match="stop before server startup"):
        await dashboard_command._serve_dashboard(argparse.Namespace(run_dir=str(run_dir), config=None, port=8080))

    after = {path.relative_to(run_dir): path.read_bytes() for path in run_dir.rglob("*") if path.is_file()}
    assert after == before


@pytest.mark.asyncio
async def test_dashboard_serve_requests_read_only_server(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "game.db").write_text("", encoding="utf-8")
    workers_dir = run_dir / "dashboard" / "data" / "workers"
    workers_dir.mkdir(parents=True)
    (workers_dir / "worker_0.js").write_text("", encoding="utf-8")
    observed: dict[str, object] = {}

    class _StopServer:
        async def start(self) -> None:
            raise RuntimeError("stop after server construction")

        async def stop(self) -> None:
            return

    def _fake_api_server_factory(*_args: object, **kwargs: object) -> _StopServer:
        observed.update(kwargs)
        return _StopServer()

    monkeypatch.setattr(
        dashboard_command,
        "build_default_root",
        lambda: SimpleNamespace(api_server_factory=_fake_api_server_factory),
    )

    with pytest.raises(RuntimeError, match="stop after server construction"):
        await dashboard_command._serve_dashboard(argparse.Namespace(run_dir=str(run_dir), config=None, port=8080))

    assert observed["instance_pool"] is None
    assert observed["read_only"] is True
    assert observed["dashboard_num_workers"] == 1
    assert observed["dashboard_profiles"] == ("tournament",)


@pytest.mark.asyncio
async def test_dashboard_serve_keeps_explicit_run_dir_when_config_supplies_worker_count(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = tmp_path / "explicit-run"
    run_dir.mkdir()
    (run_dir / "game.db").write_text("", encoding="utf-8")
    ledger_path = run_dir / "spsa" / "ledger.sqlite3"
    ledger_path.parent.mkdir()
    ledger_path.write_bytes(b"ledger-placeholder")
    config_path = tmp_path / "spsa.yaml"
    config_path.write_text("spsa: {}\n", encoding="utf-8")
    observed: dict[str, object] = {}

    class _StopServer:
        async def start(self) -> None:
            raise RuntimeError("stop after server construction")

    monkeypatch.setattr(
        dashboard_command,
        "load_tournament_config_for_dashboard",
        lambda _path, *, run_dir_override: (_ for _ in ()).throw(ValueError("not tournament")),
    )
    monkeypatch.setattr(
        dashboard_command,
        "load_spsa_config_for_dashboard",
        lambda _path, *, original_error, run_dir_override: (run_dir_override, 3),
    )

    def _fake_api_server_factory(*_args: object, **kwargs: object) -> _StopServer:
        observed.update(kwargs)
        return _StopServer()

    monkeypatch.setattr(
        dashboard_command,
        "build_default_root",
        lambda: SimpleNamespace(api_server_factory=_fake_api_server_factory),
    )

    with pytest.raises(RuntimeError, match="stop after server construction"):
        await dashboard_command._serve_dashboard(
            argparse.Namespace(run_dir=str(run_dir), config=str(config_path), port=8080)
        )

    assert observed["db_path"] == run_dir / "game.db"
    assert observed["dashboard_num_workers"] == 3
    assert observed["dashboard_profiles"] == ("spsa",)


@pytest.mark.asyncio
async def test_dashboard_serve_rejects_legacy_spsa_archive_with_actionable_error(tmp_path: Path) -> None:
    run_dir = tmp_path / "legacy-run"
    run_dir.mkdir()
    (run_dir / "game.db").write_bytes(b"")
    spsa_dir = run_dir / "spsa"
    spsa_dir.mkdir()
    (spsa_dir / "meta.json").write_text('{"type":"spsa"}', encoding="utf-8")

    with pytest.raises(CliError, match="Use ShogiArena 1.1.0"):
        await dashboard_command._serve_dashboard(argparse.Namespace(run_dir=str(run_dir), config=None, port=8080))
