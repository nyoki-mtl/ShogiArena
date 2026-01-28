"""Implementation of the ``shogiarena run analyze`` command."""

from __future__ import annotations

import argparse
import logging

from shogiarena.arena.engines.usi_think import UsiThinkRequest
from shogiarena.arena.engines.usi_types import UsiThinkPV, UsiThinkResult
from shogiarena.cli.commands._helpers import parse_option_overrides
from shogiarena.cli.errors import CliArgumentError

from .helpers.engine_loader import load_engine
from .helpers.position import parse_position_argument

LOGGER = logging.getLogger("shogiarena.cli.analyze")


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser(
        "analyze",
        help="Analyze a position with a single USI 'go' search",
        description=(
            "Launch a USI engine and run a single 'go' search on the provided position. "
            "Works with engine binaries or YAML configs."
        ),
    )
    register_run(parser)
    parser.set_defaults(async_handler=_run_command)


def register_run(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("engine", help="Engine binary or YAML config path")
    parser.add_argument(
        "position",
        nargs="?",
        default="startpos",
        help="USI position string (default: 'startpos')",
    )
    parser.add_argument(
        "--nodes",
        type=int,
        help="Search node limit",
    )
    parser.add_argument(
        "--depth",
        type=int,
        help="Search depth limit",
    )
    parser.add_argument(
        "--movetime",
        type=int,
        help="Fixed movetime in milliseconds",
    )
    parser.add_argument(
        "--infinite",
        action="store_true",
        help="Run an infinite search (default when no other limits provided)",
    )
    parser.add_argument(
        "--ponder",
        action="store_true",
        help="Set ponder flag on go command",
    )
    parser.add_argument(
        "--searchmoves",
        nargs="*",
        help="Restrict search to specific moves (USI format)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="Timeout in seconds waiting for bestmove",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress intermediate info lines",
    )
    parser.add_argument(
        "--option",
        action="append",
        metavar="KEY=VALUE",
        help="Override engine option (repeatable)",
    )


def _build_request(args: argparse.Namespace) -> UsiThinkRequest:
    searchmoves = tuple(args.searchmoves or ())
    if args.movetime is not None:
        if args.movetime <= 0:
            raise CliArgumentError("movetime must be > 0")
        return UsiThinkRequest(
            movetime=args.movetime,
            depth=args.depth,
            nodes=args.nodes,
            ponder=args.ponder,
            searchmoves=searchmoves,
        )

    return UsiThinkRequest(
        infinite=True,
        depth=args.depth,
        nodes=args.nodes,
        ponder=args.ponder,
        searchmoves=searchmoves,
    )


def _format_info(pv: UsiThinkPV) -> str:
    parts: list[str] = []
    if pv.depth is not None:
        parts.append(f"d{pv.depth}")
    if pv.seldepth is not None:
        parts.append(f"sd{pv.seldepth}")
    if pv.eval is not None:
        parts.append(pv.eval.to_string())
        if pv.bound is not None and pv.bound.to_string():
            parts.append(pv.bound.to_string())
    if pv.nodes is not None:
        parts.append(f"n={pv.nodes}")
    if pv.time is not None:
        parts.append(f"t={pv.time}ms")
    if pv.pv:
        parts.append("pv=" + " ".join(pv.pv))
    return " ".join(parts)


async def _run_command(args: argparse.Namespace) -> None:
    overrides = parse_option_overrides(args.option)
    instance_pool = None

    engine = await load_engine(
        args.engine,
        extra_options=overrides,
        instance_id=None,
        instance_pool=instance_pool,
    )

    position, moves = parse_position_argument(args.position)

    async def info_handler(pv: UsiThinkPV) -> None:
        if args.quiet:
            return
        line = _format_info(pv)
        if line:
            print(f"info {line}", flush=True)

    request = _build_request(args)

    async with engine:
        await engine.new_game()
        LOGGER.debug("Starting search: %s", request.to_command())
        result = await engine.think(
            sfen=position,
            moves=moves,
            request=request,
            info_handler=info_handler,
            timeout=args.timeout,
        )

    _print_result(result)


def _print_result(result: UsiThinkResult) -> None:
    bestmove = result.bestmove or "(none)"
    ponder = f" ponder {result.ponder}" if result.ponder else ""
    print(f"bestmove {bestmove}{ponder}")
    pv = result.get_last_pv()
    if pv and pv.pv:
        print("pv " + " ".join(pv.pv))
