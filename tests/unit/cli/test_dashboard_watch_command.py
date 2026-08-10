from __future__ import annotations

from pathlib import Path

from shogiarena._core.interfaces.cli.dashboard.watch_command import _write_dashboard_api_port


def test_write_dashboard_api_port_uses_selected_watch_port(tmp_path: Path) -> None:
    dashboard_dir = tmp_path / "dashboard"
    (dashboard_dir / "data").mkdir(parents=True)

    _write_dashboard_api_port(dashboard_dir, 7778)

    assert (dashboard_dir / "data" / "arena_port.js").read_text(encoding="utf-8") == ("window.ARENA_API_PORT = 7778;\n")
