from pathlib import Path

import pytest

from shogiarena._core.platform.engine_runtime.usi_config import UsiEngineConfig


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
        "working_directory": "{output_dir}/runs",
        "engine_args": ["--threads", "4"],
        "environment": {"OMP_NUM_THREADS": 1},
        "options": {"EvalDir": "{output_dir}/evals/book", "Hash": 64},
        "go_options": {"nodes": 1000},
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
    assert resolved.go_options == {"nodes": 1000}
    assert resolved.is_early_ponder_enabled is True

    overrides = resolved.with_overrides(
        options={"EvalDir": "{output_dir}/evals/new", "SkillLevel": 20},
        go_options={"nodes": 1234},
        output_dir=output_dir,
        engine_dir=engine_dir,
        is_early_ponder_enabled=False,
    )

    assert overrides.options["EvalDir"] == str((evals_dir / "new").resolve())
    assert overrides.options["SkillLevel"] == 20
    assert overrides.go_options["nodes"] == 1234
    assert overrides.is_early_ponder_enabled is False
    # Original resolved config remains unchanged
    assert resolved.options["EvalDir"] == str((evals_dir / "book").resolve())
    assert resolved.go_options["nodes"] == 1000
    assert resolved.is_early_ponder_enabled is True


def test_from_mapping_normalizes_go_options() -> None:
    config = UsiEngineConfig.from_mapping(
        {
            "name": "test",
            "engine_path": "/tmp/dummy",
            "go_options": {"nodes": "1000", "depth": 8},
        }
    )

    assert config.go_options == {"nodes": 1000, "depth": 8}


def test_from_mapping_rejects_timing_go_options() -> None:
    with pytest.raises(ValueError, match="go_options.movetime"):
        UsiEngineConfig.from_mapping(
            {
                "name": "test",
                "engine_path": "/tmp/dummy",
                "go_options": {"movetime": 1000},
            }
        )


def test_from_mapping_rejects_unknown_go_option() -> None:
    with pytest.raises(ValueError, match="go_options.node"):
        UsiEngineConfig.from_mapping(
            {
                "name": "test",
                "engine_path": "/tmp/dummy",
                "go_options": {"node": 1000},
            }
        )


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
environment:
  OMP_NUM_THREADS: 2
        """.strip()
        + "\n",
        encoding="utf-8",
    )

    resolved = UsiEngineConfig.from_file(config_path, output_dir=output_dir, engine_dir=engine_dir)
    expected_engine = str((engine_dir / "sample_engine").resolve())
    expected_eval = str((output_dir / "evals" / "sample").resolve())
    assert resolved.engine_path == expected_engine
    assert resolved.options["EvalDir"] == expected_eval
    assert resolved.environment == {"OMP_NUM_THREADS": "2"}


def test_from_mapping_parses_mate_defaults_and_sync_strategy() -> None:
    config = UsiEngineConfig.from_mapping(
        {
            "name": "test",
            "engine_path": "/tmp/dummy",
            "mate_default_ply_limit": 20000,
            "mate_wait_for_bestmove": True,
            "isready_sync_strategy": "wait",
        }
    )
    assert config.mate_default_ply_limit == 20000
    assert config.should_mate_wait_for_bestmove is True
    assert config.isready_sync_strategy == "wait"


def test_from_mapping_parses_io_and_option_validation_policy() -> None:
    config = UsiEngineConfig.from_mapping(
        {
            "name": "test",
            "engine_path": "/tmp/dummy",
            "io": {
                "collect_info_strings": True,
                "collect_raw_io": False,
                "collect_stderr": False,
                "collect_outbound": True,
            },
            "option_validation": {
                "default": "warn",
                "overrides": {"BookFile": "allow_unlisted_combo_value"},
            },
        }
    )

    assert config.should_collect_info_strings is True
    assert config.should_collect_raw_io is False
    assert config.should_collect_stderr is False
    assert config.should_collect_outbound is True
    assert config.option_validation_default == "warn"
    assert config.option_validation_overrides == {"BookFile": "allow_unlisted_combo_value"}


def test_from_mapping_rejects_conflicting_mate_defaults() -> None:
    with pytest.raises(ValueError):
        UsiEngineConfig.from_mapping(
            {
                "name": "test",
                "engine_path": "/tmp/dummy",
                "mate_default_infinite": True,
                "mate_default_ply_limit": 30000,
            }
        )


def test_from_mapping_rejects_invalid_isready_sync_strategy() -> None:
    with pytest.raises(ValueError):
        UsiEngineConfig.from_mapping(
            {
                "name": "test",
                "engine_path": "/tmp/dummy",
                "isready_sync_strategy": "invalid",
            }
        )


def test_from_mapping_rejects_legacy_working_dir_key() -> None:
    # 'working_dir' was renamed to 'working_directory'; the stale key must fail fast rather than be
    # silently ignored (extra="allow") and fall back to the default working directory.
    # from_mapping wraps the wire-model ValidationError into a TypeError at the boundary.
    with pytest.raises(TypeError, match="working_directory"):
        UsiEngineConfig.from_mapping(
            {
                "name": "test",
                "engine_path": "/tmp/dummy",
                "working_dir": "/tmp/work",
            }
        )


def test_from_mapping_rejects_env_key() -> None:
    with pytest.raises(TypeError, match="environment"):
        UsiEngineConfig.from_mapping(
            {
                "name": "test",
                "engine_path": "/tmp/dummy",
                "env": {"OMP_NUM_THREADS": "2"},
            }
        )


def test_with_overrides_allows_runtime_name_and_path_overrides() -> None:
    config = UsiEngineConfig.from_mapping(
        {
            "name": "base",
            "engine_path": "/tmp/base-engine",
            "working_directory": "/tmp/work",
            "options": {"Hash": 64},
        }
    )

    overridden = config.with_overrides(
        name="runtime-name",
        engine_path="/opt/runtime-engine",
        working_directory="/opt/runtime-work",
    )

    assert overridden.name == "runtime-name"
    assert overridden.engine_path == "/opt/runtime-engine"
    assert overridden.working_directory == "/opt/runtime-work"
    assert overridden.options["Hash"] == 64
