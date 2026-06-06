"""トーナメント系コマンドの実行ロジック."""

from __future__ import annotations

import argparse
from collections.abc import Awaitable, Callable
from pathlib import Path

from shogiarena._core.contexts.tournament.application.entrypoints import build_tournament_run_config
from shogiarena._core.interfaces.cli.config_file_loaders import (
    parse_tournament_config_boundary,
    parse_tournament_config_file,
)
from shogiarena._core.interfaces.cli.main import CliArgumentError, CliError
from shogiarena._core.interfaces.composition_root.default_root import build_default_root
from shogiarena._core.shared.kernel.exceptions import ContractParseError
from shogiarena._core.shared.kernel.json_types import JsonObject
from shogiarena._core.shared.kernel.paths import resolve_path_like
from shogiarena._core.shared.kernel.serialization import json_serialize

from . import tournament as tournament_cmd
from .config_builder import build_cli_config_payload
from .tournament_cli_options import flatten_block_tokens


async def run_tournament_like(
    args: argparse.Namespace,
    *,
    should_require_sprt: bool,
    default_experiment: str = "tournament",
    label: str = "tournament",
    sections_override: dict[str, list[str] | None] | None = None,
    run_command: Callable[..., Awaitable[None]] | None = None,
) -> None:
    """トーナメント系コマンドを共通実装で実行する。"""

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
            parsed_file_payload = parse_tournament_config_file(config_path)
        except (ContractParseError, TypeError, ValueError, OSError) as exc:
            raise CliError(f"Invalid tournament config: {config_path}: {exc}") from exc
        base_config = {str(key): json_serialize(value) for key, value in parsed_file_payload.items()}

    if sections_override is not None:
        sections = sections_override
    else:
        sections = {
            "rules": flatten_block_tokens(args.rules),
            "tournament": flatten_block_tokens(args.tournament),
            "rating": flatten_block_tokens(args.rating),
            "dashboard": flatten_block_tokens(args.dashboard),
            "logging": flatten_block_tokens(getattr(args, "logging", None)),
            "system": flatten_block_tokens(args.system),
            "sprt": flatten_block_tokens(args.sprt),
            "openbench": flatten_block_tokens(getattr(args, "openbench", None)),
        }
    path_preflight = getattr(args, "path_preflight", "off")
    if path_preflight != "off":
        system_tokens = list(sections.get("system") or [])
        system_tokens.append(f"path_preflight={path_preflight}")
        sections["system"] = system_tokens
    has_cli_overrides = bool(args.engine) or any(sections.values()) or config_path is None

    payload: JsonObject
    if has_cli_overrides:
        experiment_name = args.experiment_name if config_path is None else None
        payload = build_cli_config_payload(
            base=base_config,
            engines_tokens=args.engine,
            sections=sections,
            experiment_name=experiment_name,
            default_experiment=default_experiment,
            label=label,
        )
    elif base_config is None:
        raise CliArgumentError("configuration file is required when no CLI overrides are provided")
    else:
        payload = base_config

    try:
        parsed_payload = parse_tournament_config_boundary(payload)
        cfg = build_tournament_run_config(
            {str(key): json_serialize(value) for key, value in parsed_payload.items()},
            base_dir=config_path.parent if config_path is not None else Path.cwd(),
            source_path=config_path,
            runtime=build_default_root().tournament_runtime,
        )
    except ContractParseError as exc:
        raise CliError(f"Invalid tournament config: {exc}") from exc
    except (TypeError, ValueError) as exc:
        raise CliError(f"Invalid tournament config: {exc}") from exc

    if should_require_sprt and cfg.sprt is None:
        raise CliArgumentError("SPRT mode requires sprt in the tournament config")

    identity_path = config_path or Path(label)

    runner = run_command or tournament_cmd.run_tournament_command
    await runner(
        config_file=identity_path,
        should_skip_resume=args.should_skip_resume,
        is_dry_run=args.dry_run,
        should_validate_only=args.validate_only,
        provision_mode=args.provision,
        git_worktree=args.git_worktree,
        experiment_name=args.experiment_name,
        run_dir_override=args.run_dir,
        config_payload=payload if has_cli_overrides else None,
    )


__all__ = ["run_tournament_like"]
