from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from shogiarena._core.interfaces.cli.main import CliArgumentError
from shogiarena._core.interfaces.cli.run.config_builder import materialize_engine_configs
from shogiarena._core.shared.kernel.json_types import JsonObject


def test_materialize_engine_configs_writes_direct_binary_config(tmp_path: Path) -> None:
    binary = tmp_path / "engine"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    overlay = tmp_path / "overlay.yaml"
    overlay.write_text("options: {}\n", encoding="utf-8")
    output_dir = tmp_path / "runs"
    engine: JsonObject = {
        "name": "engine-a",
        "path": str(binary),
        "working_directory": str(tmp_path),
        "engine_args": ["--usi"],
        "environment": {"RSHOGI_TRACE": "1"},
        "options": {"Threads": 4, "EvalDir": str(tmp_path / "eval")},
        "options_overlays": [str(overlay)],
        "path_options": ["EvalDir"],
        "go_options": {"depth": 10},
        "enable_early_ponder": True,
    }

    materialize_engine_configs([engine], label="test", output_dir=output_dir)

    generated_config = Path(str(engine["engine_path"]))
    assert generated_config == output_dir / "generated_engine_configs" / "test" / "01-engine-a.yaml"
    raw_config = yaml.safe_load(generated_config.read_text(encoding="utf-8"))
    assert raw_config["engine_path"] == str(binary.resolve())
    assert raw_config["working_directory"] == str(tmp_path.resolve())
    assert raw_config["engine_args"] == ["--usi"]
    assert raw_config["environment"] == {"RSHOGI_TRACE": "1"}
    assert raw_config["go_options"] == {"depth": 10}
    assert raw_config["enable_early_ponder"] is True

    assert engine["name"] == "engine-a"
    assert engine["options"] == {"Threads": 4, "EvalDir": str(tmp_path / "eval")}
    assert engine["options_overlays"] == [str(overlay)]
    assert engine["path_options"] == ["EvalDir"]
    assert "go_options" not in engine


def test_materialize_engine_configs_rejects_unknown_direct_binary_keys(tmp_path: Path) -> None:
    binary = tmp_path / "engine"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    engine: JsonObject = {"path": str(binary), "unexpected": True}

    with pytest.raises(CliArgumentError, match="unsupported engine key"):
        materialize_engine_configs([engine], label="test", output_dir=tmp_path)
