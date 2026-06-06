from __future__ import annotations

from pathlib import Path

from shogiarena._core.interfaces.cli.run.config_builder import materialize_engine_configs
from shogiarena._core.shared.kernel.json_types import JsonObject


def test_materialize_engine_configs_preserves_cli_options_and_overlays(tmp_path: Path) -> None:
    binary = tmp_path / "engine"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    overlay = tmp_path / "overlay.yaml"
    overlay.write_text("options: {}\n", encoding="utf-8")
    engine: JsonObject = {
        "name": "engine-a",
        "path": str(binary),
        "options": {"Threads": 4, "EvalDir": str(tmp_path / "eval")},
        "options_overlays": [str(overlay)],
        "path_options": ["EvalDir"],
        "go_options": {"depth": 10},
    }

    materialize_engine_configs([engine], label="test")

    assert engine["engine_path"] == str(binary.resolve())
    assert engine["options"] == {"Threads": 4, "EvalDir": str(tmp_path / "eval")}
    assert engine["options_overlays"] == [str(overlay)]
    assert engine["path_options"] == ["EvalDir"]
    assert engine["go_options"] == {"depth": 10}
