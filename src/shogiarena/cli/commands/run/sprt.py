"""SPRT runner for ``shogiarena run sprt``."""

from __future__ import annotations

import argparse
import logging
from typing import Any

from shogiarena.arena.configs.tournament import ArenaConfig
from shogiarena.arena.runners.sprt_runner import SprtRunner
from shogiarena.cli.errors import CliArgumentError, CliError
from shogiarena.utils.common import project_dirs
from shogiarena.utils.common.settings import SETTINGS, validate_overlays

from .base_run import BaseRunCommand
from .config_builder import (
    materialize_engine_configs,
    parse_engine_tokens,
    prepare_positions_file,
    resolve_run_dir,
    write_temp_config,
)
from .config_overrides import apply_section_overrides

LOGGER = logging.getLogger("shogiarena.cli.run_sprt")


def register_sprt_args(parser: argparse.ArgumentParser) -> None:
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
    parser.add_argument(
        "--games",
        type=int,
        default=400,
        help="Maximum number of games to play (default: 400)",
    )
    parser.add_argument(
        "--no-swap",
        action="store_true",
        help="Do not swap colors (engine 1 stays black)",
    )
    parser.add_argument(
        "--position",
        default="startpos",
        help="Starting position in SFEN/USI format (default: startpos)",
    )
    parser.add_argument(
        "--positions-file",
        help="Path to a text file with one SFEN/USI position per line",
    )


class SprtRunCommand(BaseRunCommand):
    def run(self) -> None:
        run_sprt_sync(self.args, base_cmd=self)


def run_sprt_sync(args: argparse.Namespace, *, base_cmd: BaseRunCommand | None = None) -> None:
    cmd = base_cmd or BaseRunCommand(argparse.Namespace())
    if args.games <= 0:
        raise CliError("games must be positive")

    engine_tokens = args.engine
    if not engine_tokens or len(engine_tokens) < 2:
        raise CliArgumentError("sprt requires exactly two --engine entries")
    if len(engine_tokens) > 2:
        raise CliArgumentError("sprt requires exactly two --engine entries")

    engines = [parse_engine_tokens(tokens) for tokens in engine_tokens]
    _ensure_engine_names(engines)
    _resolve_tested_engine(engines)
    materialize_engine_configs(engines, label="sprt")

    experiment_name = getattr(args, "run_name", None) or "sprt"
    output_dir = project_dirs.output_dir
    run_dir = resolve_run_dir(experiment_name, output_dir, getattr(args, "run_dir", None))
    run_dir.mkdir(parents=True, exist_ok=True)

    positions_path = prepare_positions_file(
        run_dir=run_dir,
        position=args.position,
        positions_file=args.positions_file,
        filename="sprt_positions.txt",
    )
    flip_policy = "none" if args.no_swap else "alternate"
    initial_positions: dict[str, Any] = {"type": "startpos", "flip_policy": flip_policy}
    if positions_path is not None:
        initial_positions["type"] = "file"
        initial_positions["source"] = str(positions_path)

    payload: dict[str, Any] = {
        "experiment_name": experiment_name,
        "output_dir": str(output_dir),
        "run_dir": str(run_dir),
        "engines": engines,
        "rules": {
            "initial_positions": initial_positions,
        },
        "sprt": {
            "elo0": 0.0,
            "elo1": 5.0,
            "alpha": 0.05,
            "beta": 0.05,
            "min_games": 0,
            "max_games": args.games,
            "num_parallel": 1,
        },
        "dashboard": {"enabled": False},
    }

    apply_section_overrides(payload, "rules", _flatten_block_tokens(args.rules))
    apply_section_overrides(payload, "tournament", _flatten_block_tokens(args.tournament))
    apply_section_overrides(payload, "rating", _flatten_block_tokens(args.rating))
    apply_section_overrides(payload, "dashboard", _flatten_block_tokens(args.dashboard))
    apply_section_overrides(payload, "system", _flatten_block_tokens(args.system))
    apply_section_overrides(payload, "sprt", _flatten_block_tokens(args.sprt))

    if not _has_time_control(payload):
        raise CliError("time control (rules or engine-specific) is required")

    instance_pool = None
    validate_overlays(SETTINGS)
    config_path = write_temp_config(payload, label="sprt")
    config = ArenaConfig.from_yaml(config_path)

    cmd.apply_git_worktree(config.engines, getattr(args, "git_worktree", "strict"))

    if instance_pool and getattr(args, "provision", "none") == "force" and not getattr(args, "dry_run", False):
        cmd.provision_engines(config.engines, instance_pool)

    if getattr(args, "dry_run", False):
        LOGGER.info("DRY RUN: Validated SPRT config")
        return

    runner = SprtRunner(
        config,
        overwrite=False,
        instance_pool=instance_pool,
    )
    runner.run_sync()


def _flatten_block_tokens(raw: list[list[str]] | None) -> list[str]:
    if not raw:
        return []
    flattened: list[str] = []
    for entry in raw:
        flattened.extend(entry)
    return flattened


def _ensure_engine_names(engines: list[dict[str, Any]]) -> None:
    for idx, engine in enumerate(engines, 1):
        if not engine.get("name"):
            engine["name"] = f"engine-{idx}"


def _resolve_tested_engine(engines: list[dict[str, Any]]) -> None:
    saw_tested = False
    for idx, engine in enumerate(engines):
        tested = engine.pop("tested", None)
        if tested is None:
            continue
        if bool(tested) is False:
            continue
        if idx != 0:
            raise CliArgumentError("tested engine must be the first --engine entry")
        if saw_tested:
            raise CliArgumentError("only one engine can set tested=true")
        name = engine.get("name")
        if not name:
            raise CliArgumentError("tested engine must have a name")
        saw_tested = True


def _has_time_control(payload: dict[str, Any]) -> bool:
    rules = payload.get("rules")
    if isinstance(rules, dict) and rules.get("time_control"):
        return True
    engines = payload.get("engines")
    if isinstance(engines, list):
        for engine in engines:
            if isinstance(engine, dict) and engine.get("time_control"):
                return True
    return False

    # tested flag is optional; when absent, engine-1 is treated as tested by convention.
