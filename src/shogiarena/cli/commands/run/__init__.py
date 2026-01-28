"""Unified implementation of the ``shogiarena run`` command group."""

from __future__ import annotations

import argparse
from pathlib import Path

import yaml

from shogiarena.arena.configs.tournament import ArenaConfig
from shogiarena.cli.errors import CliArgumentError, CliError
from shogiarena.utils.common.paths import resolve_path_like

from . import analyze as analyze_cmd
from . import mate as mate_cmd
from . import sprt as sprt_cmd
from . import spsa as spsa_cmd
from . import tournament as tournament_cmd
from .config_builder import build_cli_config_payload, write_temp_config


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
    _register_run_selfplay(run_sub)
    _register_run_mate(run_sub)
    _register_run_analyze(run_sub)


def _add_tournament_common_args(
    parser: argparse.ArgumentParser,
    *,
    include_config: bool = True,
    include_sections: bool = True,
) -> None:
    if include_config:
        parser.add_argument("config", nargs="?", help="Path to configuration YAML")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate configuration without executing",
    )
    parser.add_argument(
        "--run-name",
        help="Override the run group name (creates runs/<name>-<hash8>/timestamp)",
    )
    parser.add_argument(
        "--run-dir",
        help="Override the run directory (absolute or relative path)",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing run directory",
    )
    parser.add_argument(
        "--provision",
        choices=["none", "force"],
        default="none",
        help="Provision engine assets to SSH instances before execution",
    )
    parser.add_argument(
        "--git-worktree",
        choices=["strict", "clean", "allow-dirty"],
        default="strict",
        help="Control git worktree handling before builds",
    )
    if include_sections:
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
            "--tournament",
            action="append",
            nargs="+",
            metavar="KEY=VALUE",
            help="Override tournament.* using YAML-style KEY=VALUE tokens",
        )
        parser.add_argument(
            "--rating",
            action="append",
            nargs="+",
            metavar="KEY=VALUE",
            help="Override rating.* using YAML-style KEY=VALUE tokens",
        )
        parser.add_argument(
            "--dashboard",
            action="append",
            nargs="+",
            metavar="KEY=VALUE",
            help="Override dashboard.* using YAML-style KEY=VALUE tokens",
        )
        parser.add_argument(
            "--system",
            action="append",
            nargs="+",
            metavar="KEY=VALUE",
            help="Override system.* using YAML-style KEY=VALUE tokens",
        )
        parser.add_argument(
            "--sprt",
            action="append",
            nargs="+",
            metavar="KEY=VALUE",
            help="Override sprt.* using YAML-style KEY=VALUE tokens",
        )


def _register_run_tournament(run_sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = run_sub.add_parser(
        "tournament",
        help="Run a tournament from a YAML configuration",
    )
    _add_tournament_common_args(parser)
    parser.set_defaults(handler=_run_tournament)


def _register_run_spsa(run_sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = run_sub.add_parser(
        "spsa",
        help="Run an SPSA tuning session from a YAML configuration",
    )
    parser.add_argument("config", nargs="?", help="Path to SPSA configuration YAML")
    parser.add_argument("--engine-trace", action="store_true", help="Enable verbose USI engine logging")
    parser.add_argument("--dry-run", action="store_true", help="Validate config without executing")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing run directory")
    parser.add_argument(
        "--run-name",
        help="Override the run group name (creates runs/<name>-<hash8>/timestamp)",
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
    _add_tournament_common_args(parser, include_config=False, include_sections=False)
    parser.set_defaults(handler=_run_sprt)


def _register_run_selfplay(run_sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = run_sub.add_parser(
        "generate",
        help="Generate kifu via selfplay from a YAML configuration",
    )
    _add_tournament_common_args(parser)
    parser.set_defaults(handler=_run_selfplay)


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
    _run_tournament_like(args, require_sprt=False)


def _run_selfplay(args: argparse.Namespace) -> None:
    _run_tournament_like(args, require_sprt=False)


def _run_tournament_like(
    args: argparse.Namespace,
    *,
    require_sprt: bool,
    default_experiment: str = "tournament",
    label: str = "tournament",
) -> None:
    config_path: Path | None = None
    if args.config:
        config_path = Path(resolve_path_like(args.config))
        if not config_path.exists():
            raise CliError(f"configuration file not found: {config_path}")

    if args.run_name and args.run_dir:
        raise CliArgumentError("--run-name and --run-dir cannot be used together")

    base_config: dict[str, object] | None = None
    if config_path is not None:
        with open(config_path, encoding="utf-8") as f:
            loaded = yaml.safe_load(f) or {}
        if not isinstance(loaded, dict):
            raise CliError("configuration file must contain a mapping at top-level")
        base_config = loaded

    sections = {
        "rules": _flatten_block_tokens(args.rules),
        "tournament": _flatten_block_tokens(args.tournament),
        "rating": _flatten_block_tokens(args.rating),
        "dashboard": _flatten_block_tokens(args.dashboard),
        "system": _flatten_block_tokens(args.system),
        "sprt": _flatten_block_tokens(args.sprt),
    }
    has_cli = bool(args.engine) or any(sections.values()) or config_path is None

    if has_cli:
        experiment_name = args.run_name if config_path is None else None
        payload = build_cli_config_payload(
            base=base_config,
            engines_tokens=args.engine,
            sections=sections,
            experiment_name=experiment_name,
            default_experiment=default_experiment,
            label=label,
        )
        if require_sprt:
            cfg = ArenaConfig.from_mapping(payload, base_dir=Path.cwd())
            if getattr(cfg, "sprt", None) is None:
                raise CliArgumentError("SPRT mode requires sprt in the tournament config")
        config_path = write_temp_config(payload, label=label)
    elif config_path is None:
        raise CliArgumentError("configuration file is required when no CLI overrides are provided")

    tournament_cmd.run_tournament_sync(
        config_file=config_path,
        overwrite=args.overwrite,
        dry_run=args.dry_run,
        provision_mode=args.provision,
        git_worktree=args.git_worktree,
        run_name=args.run_name,
        run_dir_override=args.run_dir,
    )


def _run_spsa(args: argparse.Namespace) -> None:
    config_path: Path | None = None
    if args.config:
        config_path = Path(resolve_path_like(args.config))
        if not config_path.exists():
            raise CliError(f"configuration file not found: {config_path}")

    if args.run_name and args.run_dir:
        raise CliArgumentError("--run-name and --run-dir cannot be used together")

    base_config: dict[str, object] | None = None
    if config_path is not None:
        with open(config_path, encoding="utf-8") as f:
            loaded = yaml.safe_load(f) or {}
        if not isinstance(loaded, dict):
            raise CliError("configuration file must contain a mapping at top-level")
        base_config = loaded

    sections = {
        "dashboard": _flatten_block_tokens(args.dashboard),
        "rules": _flatten_block_tokens(args.rules),
        "spsa": _flatten_block_tokens(args.spsa),
    }
    has_cli = bool(args.engine) or any(sections.values()) or config_path is None

    if has_cli:
        experiment_name = args.run_name if config_path is None else None
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
        overwrite=args.overwrite,
        provision_mode=args.provision,
        git_worktree=args.git_worktree,
        run_name=args.run_name,
        run_dir_override=args.run_dir,
    )


def _run_sprt(args: argparse.Namespace) -> None:
    if args.config:
        _run_tournament_like(args, require_sprt=True)
        return
    sprt_cmd.SprtRunCommand(args).run()


def _flatten_block_tokens(raw: list[list[str]] | None) -> list[str]:
    if not raw:
        return []
    flattened: list[str] = []
    for entry in raw:
        flattened.extend(entry)
    return flattened
