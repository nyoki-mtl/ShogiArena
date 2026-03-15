from __future__ import annotations

from typing import Literal, overload

from shogiarena._core.shared.kernel.game_results import GameResult, parse_game_result_name


@overload
def coerce_game_result(raw: GameResult | object | None, *, is_strict: Literal[True]) -> GameResult: ...


@overload
def coerce_game_result(raw: GameResult | object | None, *, is_strict: Literal[False] = ...) -> GameResult | None: ...


@overload
def coerce_game_result(raw: GameResult | object | None, *, is_strict: bool = ...) -> GameResult | None: ...


def coerce_game_result(raw: GameResult | object | None, *, is_strict: bool = False) -> GameResult | None:
    """Convert value into ``GameResult``."""

    match raw:
        case GameResult() as result:
            return result
        case bool():
            if is_strict:
                raise ValueError(f"cannot coerce bool to GameResult: {raw!r}")
            return None
        case int() as number:
            try:
                return GameResult(number)
            except (ValueError, TypeError, OverflowError):
                if is_strict:
                    raise ValueError(f"invalid GameResult value: {raw!r}") from None
                return None
        case str() as svalue:
            stripped = svalue.strip()
            if not stripped:
                if is_strict:
                    raise ValueError("cannot coerce empty string to GameResult")
                return None
            try:
                return GameResult(int(stripped))
            except (ValueError, TypeError, OverflowError):
                try:
                    return parse_game_result_name(stripped)
                except ValueError:
                    if is_strict:
                        raise ValueError(f"cannot coerce string to GameResult: {raw!r}") from None
                    return None
        case _:
            if is_strict:
                raise ValueError(f"unsupported GameResult source type: {raw!r}")
            return None


__all__ = ["coerce_game_result"]
