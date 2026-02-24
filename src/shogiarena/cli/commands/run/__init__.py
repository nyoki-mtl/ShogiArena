"""Unified implementation of the ``shogiarena run`` command group."""

from __future__ import annotations

import argparse
from pathlib import Path

import yaml

from shogiarena.cli.errors import CliArgumentError, CliError
from shogiarena.utils.common.paths import resolve_path_like

from . import analyze as analyze_cmd
from . import generate as generate_cmd
from . import mate as mate_cmd
from . import sprt as sprt_cmd
from . import spsa as spsa_cmd
from .config_builder import build_cli_config_payload, write_temp_config
from .tournament_like import add_tournament_common_args, flatten_block_tokens, run_tournament_like


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser(
        "run",
        help="Run tournament/SPSA/SPRT/generate workloads or quick commands",
    )
    run_sub = parser.add_subparsers(dest="run_command")
    run_sub.required = True

    _register_run_tournament(run_sub)
    _register_run_spsa(run_sub)
    _register_run_sprt(run_sub)
    generate_cmd.register(run_sub)
    _register_run_mate(run_sub)
    _register_run_analyze(run_sub)


def _register_run_tournament(run_sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = run_sub.add_parser(
        "tournament",
        help="Run a tournament from a YAML configuration",
    )
    add_tournament_common_args(parser)
    parser.set_defaults(handler=_run_tournament)


def _register_run_spsa(run_sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = run_sub.add_parser(
        "spsa",
        help="Run an SPSA tuning session from a YAML configuration",
    )
    parser.add_argument("config", nargs="?", help="Path to SPSA configuration YAML")
    parser.add_argument("--engine-trace", action="store_true", help="Enable verbose USI engine logging")
    parser.add_argument("--dry-run", action="store_true", help="Validate config without executing")
    parser.add_argument("--validate-only", action="store_true", help="Validate config and exit without scheduling")
    parser.add_argument("--no-resume", action="store_true", help="Start a new run instead of resuming")
    parser.add_argument(
        "--experiment-name",
        help="Override the experiment name (creates runs/<name>-<hash8>/timestamp)",
    )
    parser.add_argument(
        "--run-dir",
        help="Override the run directory (absolute or relative path)",
    )
    parser.add_argument(
        "--provision",
        choices=["none", "force"],
        default="none",
        help="Provision engine directories to SSH instances before run",
    )
    parser.add_argument(
        "--git-worktree",
        choices=["strict", "clean", "allow-dirty"],
        default="strict",
        help="Control git worktree handling before builds",
    )
    parser.add_argument(
        "--engine",
        action="append",
        nargs="+",
        metavar="KEY=VALUE",
        help="Engine definition (repeatable)",
    )
    parser.add_argument(
        "--rules",
        action="append",
        nargs="+",
        metavar="KEY=VALUE",
        help="Override rules.* using YAML-style KEY=VALUE tokens",
    )
    parser.add_argument(
        "--dashboard",
        action="append",
        nargs="+",
        metavar="KEY=VALUE",
        help="Override dashboard.* using YAML-style KEY=VALUE tokens",
    )
    parser.add_argument(
        "--spsa",
        action="append",
        nargs="+",
        metavar="KEY=VALUE",
        help="Override spsa.* using YAML-style KEY=VALUE tokens",
    )
    parser.set_defaults(handler=_run_spsa)


def _register_run_sprt(run_sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = run_sub.add_parser(
        "sprt",
        help="Run SPRT from config, or run a quick SPRT match without YAML",
    )
    parser.add_argument("config", nargs="?", help="Path to configuration YAML")
    sprt_cmd.register_sprt_args(parser)
    add_tournament_common_args(parser, include_config=False, include_sections=False)
    parser.set_defaults(handler=_run_sprt)


def _register_run_mate(run_sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = run_sub.add_parser(
        "mate",
        help="Run a single USI 'go mate' search",
    )
    mate_cmd.register_run(parser)
    parser.set_defaults(async_handler=mate_cmd._mate_command)


def _register_run_analyze(run_sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = run_sub.add_parser(
        "analyze",
        help="Analyze a position with a single USI 'go' search",
    )
    analyze_cmd.register_run(parser)
    parser.set_defaults(async_handler=analyze_cmd._run_command)


def _run_tournament(args: argparse.Namespace) -> None:
    run_tournament_like(args, require_sprt=False)


def _run_spsa(args: argparse.Namespace) -> None:
    config_path: Path | None = None
    if args.config:
        config_path = Path(resolve_path_like(args.config))
        if not config_path.exists():
            raise CliError(f"configuration file not found: {config_path}")

    if args.experiment_name and args.run_dir:
        raise CliArgumentError("--experiment-name and --run-dir cannot be used together")

    base_config: dict[str, object] | None = None
    if config_path is not None:
        with open(config_path, encoding="utf-8") as f:
            loaded = yaml.safe_load(f) or {}
        if not isinstance(loaded, dict):
            raise CliError("configuration file must contain a mapping at top-level")
        base_config = loaded

    sections = {
        "dashboard": flatten_block_tokens(args.dashboard),
        "rules": flatten_block_tokens(args.rules),
        "spsa": flatten_block_tokens(args.spsa),
    }
    has_cli = bool(args.engine) or any(sections.values()) or config_path is None

    if has_cli:
        experiment_name = args.experiment_name if config_path is None else None
        payload = build_cli_config_payload(
            base=base_config,
            engines_tokens=args.engine,
            sections=sections,
            experiment_name=experiment_name,
            default_experiment="spsa",
            label="spsa",
        )
        config_path = write_temp_config(payload, label="spsa")
    elif config_path is None:
        raise CliArgumentError("configuration file is required when no CLI overrides are provided")

    spsa_cmd.run_spsa_sync(
        config_file=config_path,
        engine_trace=args.engine_trace,
        dry_run=args.dry_run,
        validate_only=args.validate_only,
        no_resume=args.no_resume,
        provision_mode=args.provision,
        git_worktree=args.git_worktree,
        experiment_name=args.experiment_name,
        run_dir_override=args.run_dir,
    )


def _run_sprt(args: argparse.Namespace) -> None:
    if args.config:
        run_tournament_like(args, require_sprt=True)
        return
    sprt_cmd.SprtRunCommand(args).run()
