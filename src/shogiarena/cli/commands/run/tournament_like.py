"""トーナメント系コマンドの共通処理."""

from __future__ import annotations

import argparse
from collections.abc import Callable
from pathlib import Path

import yaml

from shogiarena.arena.configs.tournament import TournamentRunConfig
from shogiarena.cli.errors import CliArgumentError, CliError
from shogiarena.utils.common.paths import resolve_path_like

from . import tournament as tournament_cmd
from .config_builder import build_cli_config_payload, write_temp_config


def add_tournament_common_args(
    parser: argparse.ArgumentParser,
    *,
    include_config: bool = True,
    include_sections: bool = True,
    include_tournament: bool = True,
    include_generate: bool = False,
    include_rating: bool = True,
    include_sprt: bool = True,
    include_openbench: bool = True,
) -> None:
    """トーナメント系の共通 CLI 引数を追加する。"""

    if include_config:
        parser.add_argument("config", nargs="?", help="Path to configuration YAML")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate configuration without executing",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Validate configuration and exit without scheduling",
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
        "--no-resume",
        action="store_true",
        help="Start a new run instead of resuming",
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
        if include_tournament:
            parser.add_argument(
                "--tournament",
                action="append",
                nargs="+",
                metavar="KEY=VALUE",
                help="Override tournament.* using YAML-style KEY=VALUE tokens",
            )
        if include_generate:
            parser.add_argument(
                "--generate",
                action="append",
                nargs="+",
                metavar="KEY=VALUE",
                help="Override generate.* using YAML-style KEY=VALUE tokens",
            )
        if include_rating:
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
        if include_sprt:
            parser.add_argument(
                "--sprt",
                action="append",
                nargs="+",
                metavar="KEY=VALUE",
                help="Override sprt.* using YAML-style KEY=VALUE tokens",
            )
        if include_openbench:
            parser.add_argument(
                "--openbench",
                action="append",
                nargs="+",
                metavar="KEY=VALUE",
                help="Override openbench.* using YAML-style KEY=VALUE tokens",
            )


def flatten_block_tokens(raw: list[list[str]] | None) -> list[str]:
    """--rules 等の複数指定を 1 次元配列に展開する。"""

    if not raw:
        return []
    flattened: list[str] = []
    for entry in raw:
        flattened.extend(entry)
    return flattened


def run_tournament_like(
    args: argparse.Namespace,
    *,
    require_sprt: bool,
    default_experiment: str = "tournament",
    label: str = "tournament",
    sections_override: dict[str, list[str] | None] | None = None,
    run_sync: Callable[..., None] | None = None,
) -> None:
    """トーナメント系コマンドを共通実装で実行する。"""

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

    if sections_override is not None:
        sections = sections_override
    else:
        sections = {
            "rules": flatten_block_tokens(args.rules),
            "tournament": flatten_block_tokens(args.tournament),
            "rating": flatten_block_tokens(args.rating),
            "dashboard": flatten_block_tokens(args.dashboard),
            "system": flatten_block_tokens(args.system),
            "sprt": flatten_block_tokens(args.sprt),
            "openbench": flatten_block_tokens(getattr(args, "openbench", None)),
        }
    has_cli = bool(args.engine) or any(sections.values()) or config_path is None

    if has_cli:
        experiment_name = args.experiment_name if config_path is None else None
        payload = build_cli_config_payload(
            base=base_config,
            engines_tokens=args.engine,
            sections=sections,
            experiment_name=experiment_name,
            default_experiment=default_experiment,
            label=label,
        )
        if require_sprt:
            cfg = TournamentRunConfig.from_mapping(payload, base_dir=Path.cwd())
            if getattr(cfg, "sprt", None) is None:
                raise CliArgumentError("SPRT mode requires sprt in the tournament config")
        config_path = write_temp_config(payload, label=label)
    elif config_path is None:
        raise CliArgumentError("configuration file is required when no CLI overrides are provided")

    runner = run_sync or tournament_cmd.run_tournament_sync
    runner(
        config_file=config_path,
        no_resume=args.no_resume,
        dry_run=args.dry_run,
        validate_only=args.validate_only,
        provision_mode=args.provision,
        git_worktree=args.git_worktree,
        experiment_name=args.experiment_name,
        run_dir_override=args.run_dir,
    )
