"""Common helpers shared across shogidb parsers."""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping

from .exceptions import ParserInternalError

_CSA_TO_SFEN: dict[str, str] = {
    "FU": "P",
    "KY": "L",
    "KE": "N",
    "GI": "S",
    "KI": "G",
    "KA": "B",
    "HI": "R",
    "OU": "K",
    "TO": "+P",
    "NY": "+L",
    "NK": "+N",
    "NG": "+S",
    "UM": "+B",
    "RY": "+R",
}

# NOTE: CSA/KIF both rely on the same ordering when emitting SFEN strings.
_HAND_ORDER_CODES: tuple[str, ...] = ("FU", "KY", "KE", "GI", "KI", "KA", "HI")
_HAND_ORDER: tuple[tuple[str, str], ...] = tuple((code, _CSA_TO_SFEN[code]) for code in _HAND_ORDER_CODES)

_VALID_COLORS = {"+", "-"}


def piece_to_sfen(color: str, piece_code: str) -> str:
    """Return the SFEN token for a piece code with explicit side information."""

    if color not in _VALID_COLORS:
        raise ParserInternalError(f"Unexpected color token: {color!r}")
    try:
        token = _CSA_TO_SFEN[piece_code]
    except KeyError as exc:  # pragma: no cover - defensive guard
        raise ParserInternalError(f"Unsupported piece code: {piece_code!r}") from exc

    if token.startswith("+"):
        letter = token[1]
        return "+" + (letter.lower() if color == "-" else letter)
    return token if color == "+" else token.lower()


def board_map_to_sfen(board_map: Mapping[str, tuple[str, str]]) -> str:
    """Convert a square->piece map into the board section of an SFEN string."""

    rows: list[str] = []
    for rank in range(1, 10):
        empties = 0
        tokens: list[str] = []
        for file in range(9, 0, -1):
            square = f"{file}{rank}"
            piece_info = board_map.get(square)
            if piece_info is None:
                empties += 1
                continue
            if empties:
                tokens.append(str(empties))
                empties = 0
            color, piece_code = piece_info
            tokens.append(piece_to_sfen(color, piece_code))
        if empties:
            tokens.append(str(empties))
        rows.append("".join(tokens) or "9")
    return "/".join(rows)


def hand_counts_to_sfen(hand_counts: Mapping[str, Mapping[str, int]]) -> str:
    """Convert piece counts in hand to the SFEN hand section."""

    plus = hand_counts.get("+")
    minus = hand_counts.get("-")
    if plus is None or minus is None:
        raise ParserInternalError("Hand counts must include '+' and '-' entries")

    parts: list[str] = []
    for piece_code, letter in _HAND_ORDER:
        count = plus.get(piece_code, 0)
        if count:
            parts.append(f"{count}{letter}" if count > 1 else letter)
    for piece_code, letter in _HAND_ORDER:
        count = minus.get(piece_code, 0)
        if count:
            lower = letter.lower()
            parts.append(f"{count}{lower}" if count > 1 else lower)
    return "-" if not parts else "".join(parts)


def ensure_hand_dict(hand_counts: MutableMapping[str, dict[str, int]]) -> None:
    """Ensure hand maps contain both sides to simplify lookups downstream."""

    hand_counts.setdefault("+", {})
    hand_counts.setdefault("-", {})


def resolve_time_controls(time_specs: Mapping[str, str | None]) -> tuple[str | None, str | None]:
    """Return per-side time control strings using the common/side overrides."""

    common = time_specs.get("common")
    black_specific = time_specs.get("+")
    white_specific = time_specs.get("-")

    if black_specific or white_specific:
        return black_specific or common, white_specific or common
    return common, common
