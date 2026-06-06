from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from shogiarena._core.interfaces.cli.dashboard import command as dashboard_command


@pytest.mark.asyncio
async def test_dashboard_serve_writes_assets_under_dashboard_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "game.db").write_text("", encoding="utf-8")
    workers_dir = run_dir / "dashboard" / "data" / "workers"
    workers_dir.mkdir(parents=True)
    (workers_dir / "worker_0.js").write_text("", encoding="utf-8")
    observed: dict[str, Path] = {}

    def _fake_write_dashboard_assets(target_dir: Path, *_args: object, **_kwargs: object) -> None:
        observed["target_dir"] = target_dir
        raise RuntimeError("stop before server startup")

    monkeypatch.setattr(dashboard_command, "write_dashboard_assets", _fake_write_dashboard_assets)

    with pytest.raises(RuntimeError, match="stop before server startup"):
        await dashboard_command._serve_dashboard(argparse.Namespace(run_dir=str(run_dir), config=None, port=8080))

    assert observed["target_dir"] == run_dir.resolve() / "dashboard"
