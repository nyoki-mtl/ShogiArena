"""Implementation of the ``shogiarena run analyze`` command."""

from __future__ import annotations

import argparse
import logging
from collections.abc import Sequence

from rshogi.core import Move, normalize_usi_position

from shogiarena._core.contexts.match.ports.usi_think_ports import UsiThinkPVPort, UsiThinkRequest, UsiThinkResultPort
from shogiarena._core.interfaces.cli.main import CliArgumentError
from shogiarena._core.interfaces.cli.option_parsing import parse_option_overrides

from .engine_loader import load_engine

LOGGER = logging.getLogger("shogiarena.cli.analyze")
PositionTuple = tuple[str, tuple[Move, ...]]


def parse_position_argument(text: str) -> PositionTuple:
    """Parse a USI ``position`` string into base SFEN and move list."""

    raw = (text or "").strip()
    if not raw:
        raise CliArgumentError("position string must not be empty")

    if raw.startswith("position "):
        raw = raw[len("position ") :].strip()

    tokens = raw.split()
    if not tokens:
        raise CliArgumentError("position string must contain tokens")

    head = tokens[0]
    moves: Sequence[Move] = ()
    remainder: Sequence[str] = ()

    if head == "startpos":
        base_sfen = "startpos"
        remainder = tokens[1:]
    elif head == "sfen":
        if len(tokens) < 5:
            raise CliArgumentError("expected 'sfen <board> <turn> <hand> <ply>'")
        base_sfen = " ".join(tokens[1:5])
        remainder = tokens[5:]
    else:
        if len(tokens) < 4:
            raise CliArgumentError("position requires 4 tokens for SFEN base")
        base_sfen = " ".join(tokens[:4])
        remainder = tokens[4:]

    if remainder:
        if remainder[0] != "moves":
            raise CliArgumentError("expected 'moves' keyword after base position")
        moves = tuple(Move.from_usi(tok.strip()) for tok in remainder[1:] if tok.strip())

    normalized = normalize_usi_position(base_sfen)
    return normalized, tuple(moves)


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
    searchmoves = tuple(Move.from_usi(m) for m in (args.searchmoves or ()))
    if args.movetime is not None:
        if args.movetime <= 0:
            raise CliArgumentError("movetime must be > 0")
        return UsiThinkRequest(
            movetime=args.movetime,
            depth=args.depth,
            nodes=args.nodes,
            is_ponder=args.ponder,
            searchmoves=searchmoves,
        )

    return UsiThinkRequest(
        is_infinite=True,
        depth=args.depth,
        nodes=args.nodes,
        is_ponder=args.ponder,
        searchmoves=searchmoves,
    )


def _format_eval(eval_value: object) -> str:
    formatter = getattr(eval_value, "to_string", None)
    if callable(formatter):
        formatted = formatter()
        if isinstance(formatted, str) and formatted:
            return formatted
    if isinstance(eval_value, int):
        return f"cp {eval_value}"
    return str(eval_value)


def _format_bound(bound_value: object) -> str:
    formatter = getattr(bound_value, "to_string", None)
    if callable(formatter):
        formatted = formatter()
        if isinstance(formatted, str):
            return formatted
    return ""


def _format_info(pv: UsiThinkPVPort) -> str:
    parts: list[str] = []
    if pv.depth is not None:
        parts.append(f"d{pv.depth}")
    if pv.seldepth is not None:
        parts.append(f"sd{pv.seldepth}")
    if pv.eval is not None:
        parts.append(_format_eval(pv.eval))
        bound_value = _format_bound(getattr(pv, "bound", None))
        if bound_value:
            parts.append(bound_value)
    if pv.nodes is not None:
        parts.append(f"n={pv.nodes}")
    if pv.time is not None:
        parts.append(f"t={pv.time}ms")
    pv_moves = getattr(pv, "pv", None)
    if isinstance(pv_moves, list | tuple):
        parts.append("pv=" + " ".join(m.to_usi() for m in pv_moves))
    return " ".join(parts)


async def _run_command(args: argparse.Namespace) -> None:
    overrides = parse_option_overrides(args.option)

    engine = await load_engine(
        args.engine,
        extra_options=overrides,
        instance_id=None,
        instance_pool=None,
    )

    position, moves = parse_position_argument(args.position)

    async def info_handler(pv: UsiThinkPVPort) -> None:
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


def _print_result(result: UsiThinkResultPort) -> None:
    bestmove = result.bestmove.to_usi() if result.bestmove is not None else "(none)"
    ponder = f" ponder {result.ponder.to_usi()}" if result.ponder is not None else ""
    print(f"bestmove {bestmove}{ponder}")
    pv = result.get_last_pv()
    pv_moves = getattr(pv, "pv", None) if pv is not None else None
    if isinstance(pv_moves, list | tuple):
        print("pv " + " ".join(m.to_usi() for m in pv_moves))
