import json
import textwrap
from dataclasses import dataclass
from pathlib import Path

import pytest

from shogiarena._core.contexts.spsa.adapters.fixed_option_preflight import (
    FIXED_OPTION_PREFLIGHT_FILENAME,
    run_yaneuraou_fixed_option_preflight,
)
from shogiarena._core.contexts.spsa.adapters.runner_session_lifecycle import prepare_spsa_domain_inputs
from tests.unit.spsa_config_test_helpers import load_spsa_run_config


@dataclass(frozen=True, slots=True)
class _Engine:
    name: str | None
    engine_path: Path | None
    artifact: str | None = None


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content).lstrip(), encoding="utf-8")
    return path


def _write_engine_config(tmp_path: Path, engine_path: str) -> Path:
    return _write(
        tmp_path / "cfg" / "engine.yaml",
        f"""
        name: tuned
        engine_path: {json.dumps(str(engine_path))}
        """,
    )


def _report_payload(run_dir: Path) -> dict[str, object]:
    return json.loads((run_dir / "spsa" / FIXED_OPTION_PREFLIGHT_FILENAME).read_text(encoding="utf-8"))


def test_preflight_fails_when_engine_options_fix_target_option(tmp_path: Path) -> None:
    engine_binary = _write(tmp_path / "engine" / "YaneuraOu", "")
    _write(tmp_path / "engine" / "engine_options.txt", "tune.param1 = 42\n")
    engine_config = _write_engine_config(tmp_path, str(engine_binary))

    with pytest.raises(ValueError, match="Tune.Param1"):
        run_yaneuraou_fixed_option_preflight(
            engines=[_Engine(name="tuned", engine_path=engine_config)],
            target_option_names=["Tune.Param1"],
            run_dir=tmp_path,
            output_dir=tmp_path / "out",
            engine_dir=tmp_path / "engines",
        )

    payload = _report_payload(tmp_path)
    engines = payload["engines"]
    assert isinstance(engines, list)
    assert engines[0]["status"] == "conflict"
    option_files = engines[0]["option_files"]
    assert isinstance(option_files, list)
    assert option_files[0]["conflicts"] == ["Tune.Param1"]


def test_preflight_uses_relative_eval_dir_for_eval_options(tmp_path: Path) -> None:
    engine_binary = _write(tmp_path / "engine" / "YaneuraOu", "")
    _write(tmp_path / "engine" / "engine_options.txt", "EvalDir custom_eval\n")
    _write(tmp_path / "engine" / "custom_eval" / "eval_options.txt", "option name Tune.Param2 type spin default 7\n")
    engine_config = _write_engine_config(tmp_path, str(engine_binary))

    with pytest.raises(ValueError, match="Tune.Param2"):
        run_yaneuraou_fixed_option_preflight(
            engines=[_Engine(name="tuned", engine_path=engine_config)],
            target_option_names=["Tune.Param2"],
            run_dir=tmp_path,
            output_dir=tmp_path / "out",
            engine_dir=tmp_path / "engines",
        )

    payload = _report_payload(tmp_path)
    engines = payload["engines"]
    assert isinstance(engines, list)
    option_files = engines[0]["option_files"]
    assert isinstance(option_files, list)
    assert option_files[1]["path"].replace("\\", "/").endswith("custom_eval/eval_options.txt")
    assert option_files[1]["conflicts"] == ["Tune.Param2"]


def test_preflight_missing_option_files_passes_with_checked_engine(tmp_path: Path) -> None:
    engine_binary = _write(tmp_path / "engine" / "YaneuraOu", "")
    engine_config = _write_engine_config(tmp_path, str(engine_binary))

    report = run_yaneuraou_fixed_option_preflight(
        engines=[_Engine(name="tuned", engine_path=engine_config)],
        target_option_names=["Tune.Param1"],
        run_dir=tmp_path,
        output_dir=tmp_path / "out",
        engine_dir=tmp_path / "engines",
    )

    assert report.has_conflicts is False
    payload = _report_payload(tmp_path)
    engines = payload["engines"]
    assert isinstance(engines, list)
    assert engines[0]["status"] == "checked"
    option_files = engines[0]["option_files"]
    assert isinstance(option_files, list)
    assert [item["status"] for item in option_files] == ["missing", "missing"]


def test_preflight_non_conflicting_fixed_options_pass(tmp_path: Path) -> None:
    engine_binary = _write(tmp_path / "engine" / "YaneuraOu", "")
    _write(tmp_path / "engine" / "engine_options.txt", "Threads = 1\n")
    engine_config = _write_engine_config(tmp_path, str(engine_binary))

    report = run_yaneuraou_fixed_option_preflight(
        engines=[_Engine(name="tuned", engine_path=engine_config)],
        target_option_names=["Tune.Param1"],
        run_dir=tmp_path,
        output_dir=tmp_path / "out",
        engine_dir=tmp_path / "engines",
    )

    assert report.has_conflicts is False
    payload = _report_payload(tmp_path)
    engines = payload["engines"]
    assert isinstance(engines, list)
    assert engines[0]["status"] == "checked"
    option_files = engines[0]["option_files"]
    assert isinstance(option_files, list)
    assert option_files[0]["option_names"] == ["Threads"]
    assert option_files[0]["conflicts"] == []


def test_preflight_skips_remote_engine_path(tmp_path: Path) -> None:
    engine_config = _write_engine_config(tmp_path, "ssh://worker.example/bin/YaneuraOu")

    report = run_yaneuraou_fixed_option_preflight(
        engines=[_Engine(name="remote", engine_path=engine_config)],
        target_option_names=["Tune.Param1"],
        run_dir=tmp_path,
        output_dir=tmp_path / "out",
        engine_dir=tmp_path / "engines",
    )

    assert report.has_conflicts is False
    payload = _report_payload(tmp_path)
    engines = payload["engines"]
    assert isinstance(engines, list)
    assert engines[0]["status"] == "skipped"
    assert engines[0]["reason"] == "remote_engine_path"


def test_prepare_spsa_domain_inputs_runs_fixed_option_preflight(tmp_path: Path) -> None:
    engine_binary = _write(tmp_path / "engine" / "YaneuraOu", "")
    _write(tmp_path / "engine" / "engine_options.txt", "Tune.Param1 = 42\n")
    engine_config = _write_engine_config(tmp_path, str(engine_binary))
    space_path = _write(
        tmp_path / "cfg" / "space.yaml",
        """
        schema_version: shogiarena.spsa.space.v1
        target:
          protocol: usi_options
        parameters:
          - id: param1
            target:
              option: Tune.Param1
              value_encoding: decimal
            value_type: float
            initial: 10.0
            bounds:
              min: 0.0
              max: 20.0
            schedule:
              c_end: 2.0
              r_end: 0.5
        """,
    )
    sfens_path = _write(tmp_path / "cfg" / "sfens.txt", "startpos\n")
    config_path = _write(
        tmp_path / "cfg" / "spsa.yaml",
        f"""
        experiment_name: exp
        engines:
          - engine_path: {json.dumps(str(engine_config))}
            name: tuned
        rules:
          initial_positions:
            type: file
            source: {json.dumps(str(sfens_path))}
          time_control:
            node_limit: 100
        spsa:
          space: {json.dumps(str(space_path))}
          num_updates: 1
          num_parallel: 1
        """,
    )
    config = load_spsa_run_config(config_path)

    with pytest.raises(ValueError, match="Tune.Param1"):
        prepare_spsa_domain_inputs(
            config=config,
            run_dir=tmp_path,
            schedule_hash="schedule",
            resume_hash="resume",
        )

    assert (tmp_path / "spsa" / FIXED_OPTION_PREFLIGHT_FILENAME).exists()
    assert not (tmp_path / "state.json").exists()
