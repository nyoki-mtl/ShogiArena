"""Map a CSA outcome onto the arena result vocabulary.

The log states the result from our own side (``win`` / ``lose`` / ``draw`` /
``unknown``); the arena states which colour won. Getting this wrong is not a
cosmetic problem: the dashboard's ``normalizeGameResult`` throws on a value
outside its vocabulary, so a bad mapping takes the page down.
"""

from __future__ import annotations

from collections.abc import Sequence

from shogiarena._core.contexts.csa_watch.domain.event_records import CsaColor

RESULT_WIN = "win"
RESULT_LOSE = "lose"
RESULT_DRAW = "draw"
RESULT_UNKNOWN = "unknown"

BLACK_WIN = "BLACK_WIN"
WHITE_WIN = "WHITE_WIN"
BLACK_WIN_BY_DECLARATION = "BLACK_WIN_BY_DECLARATION"
WHITE_WIN_BY_DECLARATION = "WHITE_WIN_BY_DECLARATION"
BLACK_WIN_BY_TIMEOUT = "BLACK_WIN_BY_TIMEOUT"
WHITE_WIN_BY_TIMEOUT = "WHITE_WIN_BY_TIMEOUT"
BLACK_WIN_BY_ILLEGAL_MOVE = "BLACK_WIN_BY_ILLEGAL_MOVE"
WHITE_WIN_BY_ILLEGAL_MOVE = "WHITE_WIN_BY_ILLEGAL_MOVE"
DRAW_BY_REPETITION = "DRAW_BY_REPETITION"
DRAW_BY_MAX_PLIES = "DRAW_BY_MAX_PLIES"
DRAW_BY_IMPASSE = "DRAW_BY_IMPASSE"
PAUSED = "PAUSED"

_DECLARATION_MARKERS = frozenset({"%KACHI", "#JISHOGI"})
_TIMEOUT_MARKERS = frozenset({"%TIME_UP", "#TIME_UP"})
_ILLEGAL_MARKERS = frozenset({"%ILLEGAL_MOVE", "#ILLEGAL_MOVE", "%+ILLEGAL_ACTION", "%-ILLEGAL_ACTION"})
_REPETITION_MARKERS = frozenset({"%SENNICHITE", "#SENNICHITE", "#OUTE_SENNICHITE"})
_MAX_PLIES_MARKERS = frozenset({"#MAX_MOVES"})
_IMPASSE_MARKERS = frozenset({"%JISHOGI", "%HIKIWAKE"})

_WIN_BY_MARKER: tuple[tuple[frozenset[str], str, str], ...] = (
    (_DECLARATION_MARKERS, BLACK_WIN_BY_DECLARATION, WHITE_WIN_BY_DECLARATION),
    (_TIMEOUT_MARKERS, BLACK_WIN_BY_TIMEOUT, WHITE_WIN_BY_TIMEOUT),
    (_ILLEGAL_MARKERS, BLACK_WIN_BY_ILLEGAL_MOVE, WHITE_WIN_BY_ILLEGAL_MOVE),
)

_DRAW_BY_MARKER: tuple[tuple[frozenset[str], str], ...] = (
    (_REPETITION_MARKERS, DRAW_BY_REPETITION),
    (_MAX_PLIES_MARKERS, DRAW_BY_MAX_PLIES),
    (_IMPASSE_MARKERS, DRAW_BY_IMPASSE),
)

# An unqualified draw from a CSA server is repetition in practice, and the arena
# vocabulary has no unqualified draw to fall back on.
DEFAULT_DRAW_RESULT = DRAW_BY_REPETITION


def _winner(result: str, my_color: CsaColor) -> CsaColor | None:
    if result == RESULT_WIN:
        return my_color
    if result == RESULT_LOSE:
        return "white" if my_color == "black" else "black"
    return None


def arena_game_result(result: str | None, my_color: CsaColor, terminal: Sequence[str]) -> str | None:
    """Return the arena result name, or ``None`` while the game is unfinished."""
    if result is None:
        return None
    if result == RESULT_UNKNOWN:
        # The game stopped without the server stating an outcome. Claim nothing.
        return PAUSED
    if result == RESULT_DRAW:
        markers = {marker.upper() for marker in terminal}
        for candidates, name in _DRAW_BY_MARKER:
            if markers & candidates:
                return name
        return DEFAULT_DRAW_RESULT

    winner = _winner(result, my_color)
    if winner is None:
        return PAUSED
    markers = {marker.upper() for marker in terminal}
    for candidates, black_name, white_name in _WIN_BY_MARKER:
        if markers & candidates:
            return black_name if winner == "black" else white_name
    return BLACK_WIN if winner == "black" else WHITE_WIN


__all__ = [
    "DEFAULT_DRAW_RESULT",
    "RESULT_DRAW",
    "RESULT_LOSE",
    "RESULT_UNKNOWN",
    "RESULT_WIN",
    "arena_game_result",
]
