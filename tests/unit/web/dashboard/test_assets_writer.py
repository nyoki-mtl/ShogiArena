from __future__ import annotations

import json
from pathlib import Path

import pytest

from shogiarena._core.interfaces.dashboard import assets_writer
from shogiarena._core.interfaces.dashboard.assets_writer import write_dashboard_assets


def test_write_dashboard_assets_materializes_runtime_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run_dir = tmp_path / "run"
    static_dir = tmp_path / "static"
    template_root = tmp_path / "frontend"

    manifest_dir = static_dir / "dist" / ".vite"
    manifest_dir.mkdir(parents=True)
    (static_dir / "dist" / "assets").mkdir(parents=True)
    (static_dir / "js" / "shared").mkdir(parents=True)
    (static_dir / "js" / "shared" / "shogi-board.js").write_text("// board\n", encoding="utf-8")
    (static_dir / "dist" / "assets" / "index.js").write_text("console.log('dashboard');\n", encoding="utf-8")
    (static_dir / "dist" / "assets" / "index.css").write_text("body{}\n", encoding="utf-8")
    manifest = {
        "src/main.ts": {
            "file": "assets/index.js",
            "css": ["assets/index.css"],
            "imports": [],
        }
    }
    (manifest_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    template_root.mkdir(parents=True)
    (template_root / "index.html").write_text(
        "\n".join(
            (
                "<!doctype html>",
                "<html>",
                "<head>",
                "<!-- DASHBOARD_STYLES -->",
                "<!-- DASHBOARD_SCRIPTS:start --><!-- DASHBOARD_SCRIPTS:end -->",
                "</head>",
                "<body>",
                "<!-- WORKER_SCRIPTS -->",
                '<div data-guidelines="__LIVE_DIAGNOSTICS_CONFIG__"></div>',
                "</body>",
                "</html>",
            )
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(assets_writer, "_resolve_dashboard_asset_dirs", lambda: (static_dir, template_root))

    write_dashboard_assets(run_dir, num_workers=2, should_overwrite_data=True, profiles=("tournament",))

    assert (run_dir / "index.html").exists()
    assert (run_dir / "data" / "workers" / "worker_0.js").exists()
    assert (run_dir / "data" / "arena_port.js").exists()
    assert (run_dir / "static" / "dist").exists()
