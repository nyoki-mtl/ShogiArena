"""Unified implementation of the ``shogiarena run`` command group."""

from __future__ import annotations

import argparse
from pathlib import Path

from shogiarena._core.interfaces.cli.config_file_loaders import parse_spsa_config_file
from shogiarena._core.interfaces.cli.main import CliArgumentError, CliError
from shogiarena._core.shared.kernel.exceptions import ContractParseError
from shogiarena._core.shared.kernel.json_types import JsonObject
from shogiarena._core.shared.kernel.paths import resolve_path_like
from shogiarena._core.shared.kernel.serialization import json_serialize

from . import analyze as analyze_cmd
from . import mate as mate_cmd
from . import sprt as sprt_cmd
from . import spsa as spsa_cmd
from . import tournament as tournament_cmd
from .config_builder import build_cli_config_payload, write_temp_config
from .tournament_cli_options import add_tournament_common_args, flatten_block_tokens
from .tournament_command_support import run_tournament_like


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
    _register_run_generate(run_sub)
    _register_run_mate(run_sub)
    _register_run_analyze(run_sub)


def _register_run_tournament(run_sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = run_sub.add_parser(
        "tournament",
        help="Run a tournament from a YAML configuration",
    )
    add_tournament_common_args(parser)
    parser.set_defaults(async_handler=_run_tournament)


def _register_run_spsa(run_sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = run_sub.add_parser(
        "spsa",
        help="Run an SPSA tuning session from a YAML configuration",
    )
    parser.add_argument("config", nargs="?", help="Path to SPSA configuration YAML")
    parser.add_argument(
        "--engine-trace",
        dest="should_trace_engine",
        action="store_true",
        help="Enable verbose USI engine logging",
    )
    parser.add_argument("--dry-run", action="store_true", help="Validate config without executing")
    parser.add_argument("--validate-only", action="store_true", help="Validate config and exit without scheduling")
    parser.add_argument(
        "--no-resume",
        dest="should_skip_resume",
        action="store_true",
        help="Start a new run instead of resuming",
    )
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
    parser.set_defaults(async_handler=_run_spsa)


def _register_run_sprt(run_sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = run_sub.add_parser(
        "sprt",
        help="Run SPRT from config, or run a quick SPRT match without YAML",
    )
    sprt_cmd.register_sprt_args(parser)
    add_tournament_common_args(parser)
    parser.set_defaults(async_handler=_run_sprt)


def _register_run_generate(run_sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = run_sub.add_parser(
        "generate",
        help="Generate kifu via selfplay from a YAML configuration",
    )
    add_tournament_common_args(
        parser,
        should_include_tournament=False,
        should_include_generate=True,
        should_include_rating=False,
        should_include_sprt=False,
    )
    parser.set_defaults(async_handler=_run_generate)


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


async def _run_tournament(args: argparse.Namespace) -> None:
    await run_tournament_like(args, should_require_sprt=False)


async def _run_spsa(args: argparse.Namespace) -> None:
    config_path: Path | None = None
    if args.config:
        config_path = Path(resolve_path_like(args.config))
        if not config_path.exists():
            raise CliError(f"configuration file not found: {config_path}")

    if args.experiment_name and args.run_dir:
        raise CliArgumentError("--experiment-name and --run-dir cannot be used together")

    base_config: JsonObject | None = None
    if config_path is not None:
        try:
            parsed_file_payload = parse_spsa_config_file(config_path)
        except (ContractParseError, TypeError, ValueError, OSError) as exc:
            raise CliError(f"Invalid SPSA config: {config_path}: {exc}") from exc
        base_config = {str(key): json_serialize(value) for key, value in parsed_file_payload.items()}

    sections = {
        "dashboard": flatten_block_tokens(args.dashboard),
        "rules": flatten_block_tokens(args.rules),
        "spsa": flatten_block_tokens(args.spsa),
    }
    has_cli_overrides = bool(args.engine) or any(sections.values()) or config_path is None

    if has_cli_overrides:
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

    await spsa_cmd.run_spsa_command(
        config_file=config_path,
        should_trace_engine=args.should_trace_engine,
        is_dry_run=args.dry_run,
        should_validate_only=args.validate_only,
        should_skip_resume=args.should_skip_resume,
        provision_mode=args.provision,
        git_worktree=args.git_worktree,
        experiment_name=args.experiment_name,
        run_dir_override=args.run_dir,
    )


async def _run_sprt(args: argparse.Namespace) -> None:
    if args.config:
        await run_tournament_like(args, should_require_sprt=True)
        return
    await sprt_cmd.run_sprt_command(args)


async def _run_generate(args: argparse.Namespace) -> None:
    sections = {
        "rules": flatten_block_tokens(args.rules),
        "generate": flatten_block_tokens(args.generate),
        "dashboard": flatten_block_tokens(args.dashboard),
        "system": flatten_block_tokens(args.system),
    }
    await run_tournament_like(
        args,
        should_require_sprt=False,
        default_experiment="generate",
        label="generate",
        sections_override=sections,
        run_command=tournament_cmd.run_generate_command,
    )
