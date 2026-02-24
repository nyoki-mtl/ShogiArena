"""Implementation of the ``shogiarena run mate`` command."""

from __future__ import annotations

import argparse
import logging

from rshogi.core import Move

from shogiarena.cli.commands._helpers import parse_option_overrides
from shogiarena.cli.errors import CliError

from .helpers.engine_loader import load_engine
from .helpers.position import parse_position_argument

LOGGER = logging.getLogger("shogiarena.cli.mate")


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser(
        "mate",
        help="Run a single USI 'go mate' search",
        description="Launch a USI engine and execute a 'go mate' search on the provided position.",
    )
    register_run(parser)
    parser.set_defaults(async_handler=_mate_command)


def register_run(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("engine", help="Engine binary or YAML config path")
    parser.add_argument(
        "position",
        nargs="?",
        default="startpos",
        help="USI position string (default: 'startpos')",
    )
    parser.add_argument(
        "--ply-limit",
        type=int,
        help="Limit the mate search depth in plies",
    )
    parser.add_argument(
        "--node-limit",
        type=int,
        help="Limit the mate search nodes",
    )
    parser.add_argument(
        "--infinite",
        action="store_true",
        help="Use 'go mate infinite'",
    )
    parser.add_argument(
        "--wait-bestmove",
        action="store_true",
        help="Wait for trailing bestmove after checkmate before completing",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="Timeout in seconds waiting for mate result",
    )
    parser.add_argument(
        "--option",
        action="append",
        metavar="KEY=VALUE",
        help="Override engine option (repeatable)",
    )


def _format_moves(moves: tuple[Move, ...]) -> str:
    return " ".join(m.to_usi() for m in moves)


async def _mate_command(args: argparse.Namespace) -> None:
    overrides = parse_option_overrides(args.option)
    instance_pool = None
    engine = await load_engine(
        args.engine,
        extra_options=overrides,
        instance_id=None,
        instance_pool=instance_pool,
    )

    position, moves = parse_position_argument(args.position)

    if args.ply_limit is not None and args.ply_limit <= 0:
        raise CliError("ply-limit must be positive")
    if args.node_limit is not None and args.node_limit <= 0:
        raise CliError("node-limit must be positive")
    if args.infinite and (args.ply_limit is not None or args.node_limit is not None):
        raise CliError("infinite cannot be combined with ply-limit or node-limit")
    if args.ply_limit is not None and args.node_limit is not None:
        raise CliError("Specify only one of ply-limit or node-limit")

    async with engine:
        await engine.new_game()
        LOGGER.debug(
            "Starting mate search (ply_limit=%s, node_limit=%s, infinite=%s)",
            args.ply_limit,
            args.node_limit,
            args.infinite,
        )
        result = await engine.think_mate(
            sfen=position,
            moves=moves,
            ply_limit=args.ply_limit,
            node_limit=args.node_limit,
            infinite=bool(args.infinite),
            wait_for_bestmove=bool(args.wait_bestmove),
            timeout=args.timeout,
        )

    if result.is_mate:
        mate_in = result.mate_in_ply
        if mate_in is not None:
            print(f"mate {mate_in}")
        else:
            print("mate")
        if result.moves:
            print("moves " + _format_moves(result.moves))
    else:
        print("nomate")
