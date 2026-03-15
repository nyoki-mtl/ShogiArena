from __future__ import annotations

import importlib

cli_main = importlib.import_module("shogiarena._core.interfaces.cli.main")
PARSER = cli_main.build_parser()


def test_expand_dotted_override_with_assignment() -> None:
    assert cli_main._expand_dotted_overrides(["run", "--rules.time_control=tc+0.3"]) == [
        "run",
        "--rules",
        "time_control=tc+0.3",
    ]


def test_reject_global_output_dir_for_config_command() -> None:
    try:
        cli_main._reject_global_output_dir(["--output-dir", "x", "config", "init"])
    except SystemExit as exc:
        assert exc.code is not None
    else:
        raise AssertionError("expected SystemExit")


def test_allow_output_dir_after_config_command() -> None:
    cli_main._reject_global_output_dir(["config", "init", "--output-dir", "x"])


def test_no_resume_flag_maps_to_should_skip_resume_for_run_tournament() -> None:
    args = PARSER.parse_args(["run", "tournament", "dummy.yaml", "--no-resume"])

    assert args.should_skip_resume is True


def test_no_resume_flag_maps_to_should_skip_resume_for_run_spsa() -> None:
    args = PARSER.parse_args(["run", "spsa", "dummy.yaml", "--no-resume"])

    assert args.should_skip_resume is True


def test_engine_trace_flag_maps_to_should_trace_engine_for_run_spsa() -> None:
    args = PARSER.parse_args(["run", "spsa", "dummy.yaml", "--engine-trace"])

    assert args.should_trace_engine is True
