from pathlib import Path

import pytest

from shogiarena.arena.engines.usi_config import UsiEngineConfig


def test_resolve_paths_and_overrides(tmp_path: Path) -> None:
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    engine_dir = tmp_path / "engines"
    engines_dir = engine_dir / "Test"
    evals_dir = output_dir / "evals"
    engines_dir.mkdir(parents=True)
    evals_dir.mkdir(parents=True)

    mapping = {
        "name": "TestEngine",
        "engine_path": "{engine_dir}/Test/bin",
        "working_dir": "{output_dir}/runs",
        "engine_args": ["--threads", "4"],
        "env": {"OMP_NUM_THREADS": 1},
        "options": {"EvalDir": "{output_dir}/evals/book", "Hash": 64},
        "go_options": {"movetime": 1000},
        "enable_early_ponder": True,
    }

    config = UsiEngineConfig.from_mapping(mapping)
    resolved = config.resolve_paths(output_dir=output_dir, engine_dir=engine_dir)

    assert resolved.name == "TestEngine"
    assert resolved.engine_path == str((engines_dir / "bin").resolve())
    assert resolved.working_directory == str((output_dir / "runs").resolve())
    assert resolved.environment == {"OMP_NUM_THREADS": "1"}
    assert resolved.options["Hash"] == 64
    assert resolved.options["EvalDir"] == str((evals_dir / "book").resolve())
    assert resolved.go_options == {"movetime": 1000}
    assert resolved.enable_early_ponder is True

    overrides = resolved.with_overrides(
        options={"EvalDir": "{output_dir}/evals/new", "SkillLevel": 20},
        go_options={"nodes": 1234},
        output_dir=output_dir,
        engine_dir=engine_dir,
        enable_early_ponder=False,
    )

    assert overrides.options["EvalDir"] == str((evals_dir / "new").resolve())
    assert overrides.options["SkillLevel"] == 20
    assert overrides.go_options["nodes"] == 1234
    assert overrides.enable_early_ponder is False
    # Original resolved config remains unchanged
    assert resolved.options["EvalDir"] == str((evals_dir / "book").resolve())
    assert "nodes" not in resolved.go_options
    assert resolved.enable_early_ponder is True


def test_from_file_handles_missing_engine_path(tmp_path: Path) -> None:
    config_path = tmp_path / "engine.yaml"
    config_path.write_text("name: sample\n", encoding="utf-8")

    with pytest.raises(ValueError):
        UsiEngineConfig.from_file(config_path)


def test_from_file_loads_yaml(tmp_path: Path) -> None:
    engine_dir = tmp_path / "engines"
    engine_dir.mkdir(parents=True)
    output_dir = tmp_path / "output"
    output_dir.mkdir(parents=True)
    config_path = tmp_path / "engine.yaml"
    config_path.write_text(
        """
name: sample
engine_path: "{engine_dir}/sample_engine"
options:
  EvalDir: "{output_dir}/evals/sample"
        """.strip()
        + "\n",
        encoding="utf-8",
    )

    resolved = UsiEngineConfig.from_file(config_path, output_dir=output_dir, engine_dir=engine_dir)
    expected_engine = str((engine_dir / "sample_engine").resolve())
    expected_eval = str((output_dir / "evals" / "sample").resolve())
    assert resolved.engine_path == expected_engine
    assert resolved.options["EvalDir"] == expected_eval
