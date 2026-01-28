from __future__ import annotations

import logging
from decimal import Decimal
from typing import TYPE_CHECKING

import cshogi
from cshogi.KIF import board_to_bod

from shogiarena.records.parser.kif import endgame_to_lines, move_to_line
from shogiarena.utils.common.constants import MOVE_END
from shogiarena.utils.types.sfen import sfen_to_handicap_dict
from shogiarena.utils.types.types import GameResult

if TYPE_CHECKING:  # pragma: no cover - import-time cycle guard
    from .game_info import GameInfo


logger = logging.getLogger(__name__)


_CSA_END_MARKERS = {
    GameResult.BLACK_WIN: "%TORYO",
    GameResult.WHITE_WIN: "%TORYO",
    GameResult.DRAW_BY_REPETITION: "%SENNICHITE",
    GameResult.DRAW_BY_MAX_PLIES: "%MAX_MOVES",
    GameResult.BLACK_WIN_BY_DECLARATION: "%KACHI",
    GameResult.WHITE_WIN_BY_DECLARATION: "%KACHI",
    GameResult.BLACK_WIN_BY_FORFEIT: "%KACHI",
    GameResult.WHITE_WIN_BY_FORFEIT: "%KACHI",
    GameResult.BLACK_WIN_BY_ILLEGAL_MOVE: "%ILLEGAL_MOVE",
    GameResult.WHITE_WIN_BY_ILLEGAL_MOVE: "%ILLEGAL_MOVE",
    GameResult.BLACK_WIN_BY_TIMEOUT: "%TIME_UP",
    GameResult.WHITE_WIN_BY_TIMEOUT: "%TIME_UP",
    GameResult.ERROR: "%ERROR",
    GameResult.INVALID: "%CHUDAN",
    GameResult.PAUSED: "%CHUDAN",
}


def export_to_kif(game_info: GameInfo) -> str:
    """Serialize a :class:`GameInfo` instance to KIF format."""
    board = _create_board(game_info.init_position_sfen, reset_ply_counter=True)
    header_board = board.copy()

    output_lines: list[str] = []

    if game_info.start_date is not None:
        output_lines.append(f"開始日時：{game_info.start_date.strftime('%Y/%m/%d %H:%M:%S')}")
    if game_info.end_date is not None:
        output_lines.append(f"終了日時：{game_info.end_date.strftime('%Y/%m/%d %H:%M:%S')}")

    _append_kif_time_notes(output_lines, game_info.black_time_control, game_info.white_time_control)

    sfen_key = " ".join(game_info.init_position_sfen.split(" ")[:3] + ["1"])
    if sfen_key in sfen_to_handicap_dict:
        handicap = sfen_to_handicap_dict[sfen_key]
        output_lines.append(f"手合割：{handicap}")
    else:
        handicap = "平手"
        output_lines.append(f"手合割：{handicap}")
        output_lines.extend(board_to_bod(header_board))

    if handicap == "平手":
        output_lines.append(f"先手：{game_info.black_player_name}")
        output_lines.append(f"後手：{game_info.white_player_name}")
    else:
        output_lines.append(f"下手：{game_info.black_player_name}")
        output_lines.append(f"上手：{game_info.white_player_name}")

    output_lines.append("手数----指手---------消費時間--")

    black_total_time = 0
    white_total_time = 0
    last_move_time = 0

    for idx, move in enumerate(game_info.moves):
        if move == MOVE_END:
            break
        if not board.is_legal(move):
            logger.warning("Skipping illegal move during KIF export: %s", move)
            break

        move_time_ms = game_info.move_times_ms[idx]
        eval_value = game_info.eval_values[idx]

        last_move_time = (move_time_ms // 1000) if move_time_ms is not None else 0
        if board.turn == cshogi.BLACK:
            black_total_time += last_move_time
            output_lines.append(move_to_line(board, move, last_move_time, black_total_time))
        else:
            white_total_time += last_move_time
            output_lines.append(move_to_line(board, move, last_move_time, white_total_time))

        if eval_value is not None:
            output_lines.append(f"**評価値={eval_value if board.turn == cshogi.BLACK else -eval_value}")

        board.push(move)

    result_enum = game_info.game_result or GameResult.PAUSED
    if board.turn == cshogi.BLACK:
        black_total_time += last_move_time
        lastmove_line, reason_line = endgame_to_lines(
            board,
            result_enum,
            handicap,
            last_move_time,
            black_total_time,
        )
    else:
        white_total_time += last_move_time
        lastmove_line, reason_line = endgame_to_lines(
            board,
            result_enum,
            handicap,
            last_move_time,
            white_total_time,
        )

    output_lines.append(lastmove_line)
    output_lines.append(reason_line)

    return "\n".join(output_lines)


def export_to_csa(game_info: GameInfo) -> str:
    """Serialize a :class:`GameInfo` instance to CSA format."""
    board = _create_board(game_info.init_position_sfen)

    output_lines: list[str] = ["V2.2"]

    if game_info.black_player_name:
        output_lines.append(f"N+{game_info.black_player_name}")
    if game_info.white_player_name:
        output_lines.append(f"N-{game_info.white_player_name}")

    if game_info.game_name:
        output_lines.append(f"$EVENT:{game_info.game_name}")
    if game_info.start_date is not None:
        output_lines.append(f"$START_TIME:{game_info.start_date.strftime('%Y/%m/%d %H:%M:%S')}")
    if game_info.end_date is not None:
        output_lines.append(f"$END_TIME:{game_info.end_date.strftime('%Y/%m/%d %H:%M:%S')}")

    _append_common_time_specs(output_lines, game_info.black_time_control, game_info.white_time_control)

    output_lines.extend(board.csa_pos().strip().splitlines())

    for idx, move in enumerate(game_info.moves):
        if move == MOVE_END:
            break
        if not board.is_legal(move):
            logger.warning("Skipping illegal move during CSA export: %s", move)
            break

        move_time_ms = game_info.move_times_ms[idx]
        eval_value = game_info.eval_values[idx]
        move_comment = game_info.move_comments[idx]

        prefix = "+" if board.turn == cshogi.BLACK else "-"
        move_tokens = [f"{prefix}{cshogi.move_to_csa(move)}"]
        if move_time_ms is not None:
            move_tokens.append(f"T{move_time_ms // 1000}")
        output_lines.append(",".join(move_tokens))

        comment_lines: list[str] = []
        if eval_value is not None:
            comment_lines.append(f"**評価値={eval_value if board.turn == cshogi.BLACK else -eval_value}")
        if isinstance(move_comment, str) and move_comment:
            comment_lines.extend(move_comment.splitlines())
        for comment_line in comment_lines:
            output_lines.append("'" + comment_line)

        board.push(move)

    output_lines.append(_game_result_to_csa_marker(game_info.game_result))

    return "\n".join(output_lines)


def _create_board(init_sfen: str, *, reset_ply_counter: bool = False) -> cshogi.Board:
    board = cshogi.Board()
    if reset_ply_counter:
        parts = init_sfen.split()
        if len(parts) == 4:
            init_sfen = " ".join((*parts[:3], "1"))
    board.set_sfen(init_sfen)
    return board


def _strip_decimal(raw: str) -> str:
    value = Decimal(raw)
    text = format(value.normalize(), "f")
    stripped = text.rstrip("0").rstrip(".")
    return stripped or "0"


def _split_time_spec(spec: str | None) -> tuple[str, str, str] | None:
    if not spec:
        return None
    parts = spec.split("+")
    if len(parts) != 3:
        return None
    first, second, third = (_strip_decimal(part) for part in parts)
    return first, second, third


def _format_time_spec(spec: str | None) -> str | None:
    split = _split_time_spec(spec)
    if split is None:
        return None
    return "+".join(split)


def _append_common_time_specs(lines: list[str], black_spec: str | None, white_spec: str | None) -> None:
    formatted_black = _format_time_spec(black_spec)
    formatted_white = _format_time_spec(white_spec)
    if formatted_black and formatted_white and formatted_black == formatted_white:
        lines.append(f"$TIME:{formatted_black}")
        return
    if formatted_black:
        lines.append(f"$TIME+:{formatted_black}")
    if formatted_white:
        lines.append(f"$TIME-:{formatted_white}")


def _append_kif_time_notes(lines: list[str], black_spec: str | None, white_spec: str | None) -> None:
    formatted_black = _format_time_spec(black_spec)
    formatted_white = _format_time_spec(white_spec)
    if formatted_black:
        lines.append(f"備考：先手TimeControl：{formatted_black}")
    if formatted_white:
        lines.append(f"備考：後手TimeControl：{formatted_white}")


def _game_result_to_csa_marker(result: GameResult | None) -> str:
    if result is None:
        return "%CHUDAN"
    return _CSA_END_MARKERS.get(result, "%CHUDAN")
