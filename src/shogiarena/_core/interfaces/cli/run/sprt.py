"""SPRT helpers for ``shogiarena run sprt``."""

from __future__ import annotations

from shogiarena._core.interfaces.cli.main import CliArgumentError, CliError
from shogiarena._core.shared.kernel.json_types import JsonObject
from shogiarena._core.shared.kernel.serialization import json_serialize

from . import tournament as tournament_cmd
from .config_builder import (
    build_cli_config_payload,
    ensure_engine_names,
    parse_engine_tokens,
    write_temp_config,
)
from .tournament_cli_options import flatten_block_tokens


def register_sprt_args(parser) -> None:
    parser.add_argument(
        "--games",
        type=int,
        default=400,
        help="Maximum number of games to play (default: 400)",
    )


async def run_sprt_command(args) -> None:
    if args.games <= 0:
        raise CliError("games must be positive")

    engine_tokens = args.engine or []
    if len(engine_tokens) != 2:
        raise CliArgumentError("sprt requires exactly two --engine entries")

    engines = [parse_engine_tokens(tokens) for tokens in engine_tokens]
    ensure_engine_names(engines)
    _resolve_tested_engine(engines)

    payload = build_cli_config_payload(
        base={
            "experiment_name": args.experiment_name or "sprt",
            "engines": engines,
            "dashboard": {"enabled": False},
        },
        engines_tokens=None,
        sections={
            "rules": flatten_block_tokens(args.rules),
            "tournament": flatten_block_tokens(args.tournament),
            "rating": flatten_block_tokens(args.rating),
            "dashboard": flatten_block_tokens(args.dashboard),
            "system": flatten_block_tokens(args.system),
            "sprt": flatten_block_tokens(args.sprt),
            "openbench": flatten_block_tokens(args.openbench),
        },
        experiment_name=args.experiment_name,
        default_experiment="sprt",
        label="sprt",
    )
    payload["engines"] = engines
    sprt_payload = payload.get("sprt")
    if not isinstance(sprt_payload, dict):
        sprt_payload = {}
    normalized_sprt: JsonObject = {str(key): json_serialize(value) for key, value in sprt_payload.items()}
    normalized_sprt.setdefault("elo0", 0.0)
    normalized_sprt.setdefault("elo1", 5.0)
    normalized_sprt.setdefault("alpha", 0.05)
    normalized_sprt.setdefault("beta", 0.05)
    normalized_sprt.setdefault("min_games", 0)
    normalized_sprt["max_games"] = args.games
    normalized_sprt.setdefault("num_parallel", 1)
    payload["sprt"] = normalized_sprt

    if not _has_time_control(payload):
        raise CliError("time control (rules or engine-specific) is required")

    config_path = write_temp_config(payload, label="sprt")
    await tournament_cmd.run_tournament_command(
        config_file=config_path,
        should_skip_resume=args.should_skip_resume,
        is_dry_run=args.dry_run,
        should_validate_only=args.validate_only,
        provision_mode=args.provision,
        git_worktree=args.git_worktree,
        experiment_name=args.experiment_name,
        run_dir_override=args.run_dir,
    )


def _resolve_tested_engine(engines: list[JsonObject]) -> None:
    has_seen_tested = False
    for idx, engine in enumerate(engines):
        tested = engine.pop("tested", None)
        if tested is None:
            continue
        if bool(tested) is False:
            continue
        if idx != 0:
            raise CliArgumentError("tested engine must be the first --engine entry")
        if has_seen_tested:
            raise CliArgumentError("only one engine can set tested=true")
        name = engine.get("name")
        if not name:
            raise CliArgumentError("tested engine must have a name")
        has_seen_tested = True


def _has_time_control(payload: JsonObject) -> bool:
    rules = payload.get("rules")
    if isinstance(rules, dict):
        rules_map: JsonObject = {str(key): json_serialize(value) for key, value in rules.items()}
        if rules_map.get("time_control"):
            return True
    engines = payload.get("engines")
    if isinstance(engines, list):
        for engine in engines:
            if isinstance(engine, dict):
                engine_map: JsonObject = {str(key): json_serialize(value) for key, value in engine.items()}
                if engine_map.get("time_control"):
                    return True
    return False
