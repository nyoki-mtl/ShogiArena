"""SPRT helpers for ``shogiarena run sprt``."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from shogiarena._core.interfaces.cli.main import CliArgumentError, CliError
from shogiarena._core.shared.kernel.json_types import JsonObject
from shogiarena._core.shared.kernel.serialization import json_serialize

from . import tournament as tournament_cmd
from .config_builder import (
    build_cli_config_payload,
    ensure_engine_names,
    parse_engine_tokens,
)
from .tournament_cli_options import flatten_block_tokens


@dataclass(frozen=True, slots=True)
class SprtCliDefaults:
    """Default SPRT contract used by the quick CLI entry point."""

    elo0: float = 0.0
    elo1: float = 5.0
    alpha: float = 0.05
    beta: float = 0.05
    min_games: int = 0
    num_parallel: int = 1

    def to_payload(self) -> JsonObject:
        """Return JSON-ready SPRT defaults."""

        return {
            "elo0": self.elo0,
            "elo1": self.elo1,
            "alpha": self.alpha,
            "beta": self.beta,
            "min_games": self.min_games,
            "num_parallel": self.num_parallel,
        }


SPRT_CLI_DEFAULTS = SprtCliDefaults()


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
    normalized_sprt: JsonObject = SPRT_CLI_DEFAULTS.to_payload()
    normalized_sprt.update({str(key): json_serialize(value) for key, value in sprt_payload.items()})
    normalized_sprt["max_games"] = args.games
    payload["sprt"] = normalized_sprt

    if not _has_time_control(payload):
        raise CliError("time control (rules or engine-specific) is required")

    await tournament_cmd.run_tournament_command(
        config_file=Path("sprt"),
        should_skip_resume=args.should_skip_resume,
        is_dry_run=args.dry_run,
        should_validate_only=args.validate_only,
        provision_mode=args.provision,
        git_worktree=args.git_worktree,
        experiment_name=args.experiment_name,
        run_dir_override=args.run_dir,
        config_payload=payload,
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
