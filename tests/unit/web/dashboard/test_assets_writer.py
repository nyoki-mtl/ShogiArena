from __future__ import annotations

from pathlib import Path

from shogiarena._core.interfaces.dashboard.assets_writer import write_dashboard_assets


def test_write_dashboard_assets_materializes_runtime_files(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"

    write_dashboard_assets(run_dir, num_workers=2, should_overwrite_data=True, profiles=("tournament",))

    assert (run_dir / "index.html").exists()
    assert (run_dir / "data" / "workers" / "worker_0.js").exists()
    assert (run_dir / "data" / "arena_port.js").exists()
    assert (run_dir / "static" / "dist").exists()
