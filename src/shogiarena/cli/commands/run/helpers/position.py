"""Helpers for parsing SFEN/position arguments from CLI input."""

from __future__ import annotations

from collections.abc import Sequence

from rshogi.core import normalize_usi_position

from shogiarena.cli.errors import CliArgumentError

PositionTuple = tuple[str, tuple[str, ...]]


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
    moves: Sequence[str] = ()
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
        moves = tuple(tok.strip() for tok in remainder[1:] if tok.strip())

    normalized = normalize_usi_position(base_sfen)
    return normalized, tuple(moves)
