"""Initial-position entry model and USI opening-line parsing helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from rsshogi.core import normalize_usi_position, parse_usi_position_parts

OpeningSourceFormat = Literal["auto", "sfen", "usi_line"]


def extract_opening_source_format(config: object) -> OpeningSourceFormat:
    """Extract ``rules.initial_positions.source_format`` from a config object when available."""

    rules = getattr(config, "rules", None)
    initial_positions = getattr(rules, "initial_positions", None)
    raw_format = getattr(initial_positions, "source_format", "auto")
    if raw_format == "sfen":
        return "sfen"
    if raw_format == "usi_line":
        return "usi_line"
    return "auto"


@dataclass(frozen=True, slots=True)
class InitialPositionEntry:
    """Initial position plus optional opening-line history and provenance."""

    initial_sfen: str
    opening_base_sfen: str | None = None
    opening_moves_usi: tuple[str, ...] = ()
    source_line: str | None = None
    source_line_no: int | None = None
    line_moves_usi: tuple[str, ...] = ()
    line_id: str | None = None
    source_path: str | None = None


@dataclass(frozen=True, slots=True)
class ResolvedOpeningPosition:
    """Normalized opening position separating post-opening SFEN and prior move history."""

    post_opening_sfen: str
    opening_base_sfen: str | None = None
    opening_moves_usi: tuple[str, ...] = ()


def normalize_usi_line_command(raw_line: str) -> str:
    """Coerce a USI position / move-sequence line into a canonical ``position ...`` string."""

    text = raw_line.strip()
    if not text:
        raise ValueError("opening position line must not be empty")
    if text.startswith("position "):
        return text
    parts = text.split()
    if parts[0] == "startpos":
        if len(parts) == 1:
            return "position startpos"
        if parts[1] == "moves":
            return f"position {text}"
        return f"position startpos moves {' '.join(parts[1:])}"
    if parts[0] == "sfen":
        return f"position {text}"
    if len(parts) >= 3 and "/" in parts[0]:
        return f"position sfen {text}"
    return f"position startpos moves {text}"


def resolve_opening_position_text(
    raw_line: str,
    *,
    treat_as_usi_line: bool = False,
    source_format: OpeningSourceFormat = "auto",
) -> ResolvedOpeningPosition:
    """Parse an opening specification, preserving base SFEN and prior moves when present."""

    text = raw_line.strip()
    if not text:
        raise ValueError("opening position line must not be empty")
    tokens = text.split()
    effective_format: OpeningSourceFormat = "usi_line" if treat_as_usi_line else source_format

    if effective_format == "sfen":
        if "moves" in tokens:
            raise ValueError(f"source_format='sfen' does not allow move sequences: {raw_line!r}")
        if text in {"startpos", "position startpos"}:
            return ResolvedOpeningPosition(post_opening_sfen="startpos")
        if text.startswith("sfen "):
            sfen_body = text[5:].strip()
        elif text.startswith("position sfen "):
            sfen_body = text[14:].strip()
        else:
            sfen_body = text
        return ResolvedOpeningPosition(post_opening_sfen=normalize_usi_position(sfen_body))

    is_bare_sfen_or_startpos = (
        effective_format == "auto"
        and "moves" not in tokens
        and (
            text in {"startpos", "position startpos"}
            or text.startswith("sfen ")
            or text.startswith("position sfen ")
            or (len(tokens) >= 3 and "/" in tokens[0])
        )
    )
    if is_bare_sfen_or_startpos:
        return ResolvedOpeningPosition(post_opening_sfen=normalize_usi_position(text))

    position_cmd = normalize_usi_line_command(text)
    usi_parts = parse_usi_position_parts(position_cmd)
    opening_moves = tuple(usi_parts.move_usi)
    if not opening_moves:
        return ResolvedOpeningPosition(post_opening_sfen=usi_parts.final_sfen)
    return ResolvedOpeningPosition(
        post_opening_sfen=usi_parts.final_sfen,
        opening_base_sfen=normalize_usi_position(usi_parts.initial_sfen),
        opening_moves_usi=opening_moves,
    )


def format_usi_position_with_moves(base_sfen: str, moves_usi: tuple[str, ...] | list[str]) -> str:
    """Build a USI ``position ...`` string from normalized base SFEN and USI move tokens."""

    normalized_base = normalize_usi_position(base_sfen)
    command = "position startpos" if normalized_base == "startpos" else f"position sfen {normalized_base}"
    if moves_usi:
        command += f" moves {' '.join(moves_usi)}"
    return command


__all__ = [
    "InitialPositionEntry",
    "OpeningSourceFormat",
    "ResolvedOpeningPosition",
    "extract_opening_source_format",
    "format_usi_position_with_moves",
    "normalize_usi_line_command",
    "resolve_opening_position_text",
]
