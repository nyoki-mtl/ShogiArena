from __future__ import annotations

import logging
import math
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import cshogi
from cshogi.KIF import move_to_kif

from shogiarena.utils.common.constants import MOVE_END
from shogiarena.utils.types.sfen import handicap_to_sfen_dict

from .common import (
    board_map_to_sfen,
    ensure_hand_dict,
    hand_counts_to_sfen,
    resolve_time_controls,
)

logger = logging.getLogger(__name__)


_RESULT_NAMES = {
    "中断",
    "投了",
    "持将棋",
    "千日手",
    "詰み",
    "切れ負け",
    "反則勝ち",
    "反則負け",
    "入玉宣言",
    "入玉勝ち",
}

_MOVE_PATTERN = re.compile(
    r"\A *[0-9]+ (中断|投了|持将棋|千日手|詰み|切れ負け|反則勝ち|反則負け|入玉宣言|入玉勝ち|"
    + r"(([１２３４５６７８９])([零一二三四五六七八九])|同　)([歩香桂銀金角飛玉と杏圭全馬龍])"
    + r"(打|(成?)\(([0-9])([0-9])\)))\s*(\(([ :0-9]+)/([ :0-9]+)\))?\s*\Z"
)
_RESULT_SUMMARY_PATTERN = re.compile(
    r"　*まで(\d+)手で((先|下|後|上)手の(勝ち|反則勝ち|反則負け)|千日手|持将棋|中断|入玉宣言|入玉勝ち)"
)

_PLAYER_RATING_PATTERN = re.compile(r"(.+)\((\d+)\)")

_END_NAME_TO_MARKER = {
    "投了": "%TORYO",
    "詰み": "%TORYO",
    "持将棋": "%JISHOGI",
    "千日手": "%SENNICHITE",
    "切れ負け": "%TIME_UP",
    "反則勝ち": "%ILLEGAL_MOVE",
    "反則負け": "%ILLEGAL_MOVE",
    "中断": "%CHUDAN",
    "入玉宣言": "%KACHI",
    "入玉勝ち": "%KACHI",
}


def resolve_result_from_name(end_name: str, turn: int) -> tuple[str, int]:
    marker = _END_NAME_TO_MARKER.get(end_name, "%CHUDAN")
    if end_name == "反則勝ち":
        return marker, 1 + turn
    if end_name == "反則負け":
        return marker, 2 - turn
    if end_name in {"投了", "詰み", "切れ負け"}:
        return marker, 2 - turn
    if end_name in {"持将棋", "千日手", "中断"}:
        return marker, 0
    if end_name in {"入玉宣言", "入玉勝ち"}:
        return marker, 1 + turn
    return marker, 0


KANJI_NUMERALS = {
    "〇": 0,
    "零": 0,
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}

HAND_PIECE_MAP = {
    "歩": "FU",
    "香": "KY",
    "桂": "KE",
    "銀": "GI",
    "金": "KI",
    "角": "KA",
    "飛": "HI",
    "玉": "OU",
    "王": "OU",
}

BOARD_PIECE_MAP = {
    "歩": "FU",
    "香": "KY",
    "桂": "KE",
    "銀": "GI",
    "金": "KI",
    "角": "KA",
    "飛": "HI",
    "玉": "OU",
    "王": "OU",
    "と": "TO",
    "杏": "NY",
    "圭": "NK",
    "全": "NG",
    "馬": "UM",
    "竜": "RY",
    "龍": "RY",
}

BOARD_PIECE_ALIASES = {
    "成銀": "全",
    "成桂": "圭",
    "成香": "杏",
    "成歩": "と",
}


class KIFParseError(Exception):
    pass


def kanji_to_int(text: str) -> int:
    if not text:
        return 1
    total = 0
    current = 0
    for ch in text:
        if ch == "十":
            current = max(current, 1)
            total += current * 10
            current = 0
        else:
            digit = KANJI_NUMERALS.get(ch)
            if digit is None:
                continue
            current = current * 10 + digit
    total += current
    return total if total else 1


def parse_hand_pieces(text: str) -> dict[str, int]:
    cleaned = text.replace("　", "").replace(" ", "")
    if cleaned in {"", "なし"}:
        return {}
    counts: dict[str, int] = {}
    i = 0
    while i < len(cleaned):
        piece_char = cleaned[i]
        i += 1
        if piece_char not in HAND_PIECE_MAP:
            continue
        count_start = i
        while i < len(cleaned) and cleaned[i] in KANJI_NUMERALS:
            i += 1
        count_text = cleaned[count_start:i]
        count = kanji_to_int(count_text)
        piece_code = HAND_PIECE_MAP[piece_char]
        counts[piece_code] = counts.get(piece_code, 0) + count
    return counts


def parse_board_block(lines: list[str], start_index: int) -> tuple[int, dict[str, tuple[str, str]]]:
    idx = start_index
    board_map: dict[str, tuple[str, str]] = {}

    idx += 1  # skip header like "  ９ ８ ７ ..."
    if idx < len(lines) and lines[idx].startswith("+"):
        idx += 1

    for row in range(9):
        if idx >= len(lines):
            break
        row_line = lines[idx]
        idx += 1
        segments = row_line.split("|")[1:10]
        for file_index, raw_token in enumerate(segments):
            token = raw_token.strip()
            if not token or token in {"・", "　"}:
                continue
            color = "+"
            if token[0] in {"v", "^"}:
                color = "-"
                token = token[1:]
            token = BOARD_PIECE_ALIASES.get(token, token)
            piece_code = BOARD_PIECE_MAP.get(token)
            if piece_code is None:
                continue
            file = 9 - file_index
            rank = row + 1
            board_map[f"{file}{rank}"] = (color, piece_code)

    if idx < len(lines) and lines[idx].startswith("+"):
        idx += 1

    return idx, board_map


class KIFParser:
    def __init__(self, strict_warn: bool = False) -> None:
        self.board = cshogi.Board()
        self.strict_warn = strict_warn

    def warning(self, msg: str) -> None:
        if self.strict_warn:
            raise KIFParseError(msg)
        logger.warning(msg)

    def parse_kif(self, kif_path: Path, encoding: str = "utf-8") -> dict[str, Any]:
        kif_str = kif_path.read_text(encoding=encoding)
        return self.parse_kif_str(kif_str)

    def parse_kif_str(self, kif_str: str) -> dict[str, Any]:
        lines = kif_str.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        if lines:
            lines[0] = lines[0].lstrip("\ufeff")

        self.board.reset()

        start_date: datetime | None = None
        end_date: datetime | None = None
        kisen: str | None = None
        black_player_name: str | None = None
        white_player_name: str | None = None
        black_rate = 0
        white_rate = 0
        handicap_name: str | None = None

        board_map: dict[str, tuple[str, str]] = {}
        hand_counts: dict[str, dict[str, int]] = {"+": {}, "-": {}}
        time_specs: dict[str, str | None] = {"common": None, "+": None, "-": None}

        game_comments: list[str] = []

        idx = 0
        # Header parsing
        while idx < len(lines):
            line = lines[idx]
            stripped = line.strip()
            if not stripped:
                idx += 1
                continue
            if re.match(r"^\s*\d+\s", line) or stripped.startswith("手数＝") or stripped.startswith("まで"):
                break
            if stripped.startswith("変化："):
                break
            if stripped.startswith("*"):
                game_comments.append(stripped[1:].lstrip())
                idx += 1
                continue
            if "９" in line and "１" in line and line.strip().startswith("９"):
                idx, parsed_board = parse_board_block(lines, idx)
                board_map.update(parsed_board)
                continue
            if stripped.startswith("先手の持駒："):
                hand_counts["+"] = parse_hand_pieces(stripped.split("：", 1)[1])
                idx += 1
                continue
            if stripped.startswith("後手の持駒："):
                hand_counts["-"] = parse_hand_pieces(stripped.split("：", 1)[1])
                idx += 1
                continue
            if "：" in line:
                key, value = line.split("：", 1)
                key = key.strip()
                value = value.strip()
                if key == "開始日時":
                    for fmt in ("%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M", "%Y/%m/%d"):
                        try:
                            start_date = datetime.strptime(value, fmt)
                            break
                        except ValueError:
                            continue
                elif key == "終了日時":
                    for fmt in ("%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M", "%Y/%m/%d"):
                        try:
                            end_date = datetime.strptime(value, fmt)
                            break
                        except ValueError:
                            continue
                elif key == "棋戦":
                    kisen = value
                elif key in ["先手", "下手"]:
                    match = _PLAYER_RATING_PATTERN.match(value)
                    if match:
                        black_player_name = match.group(1)
                        black_rate = int(match.group(2))
                    else:
                        black_player_name = value
                elif key in ["後手", "上手"]:
                    match = _PLAYER_RATING_PATTERN.match(value)
                    if match:
                        white_player_name = match.group(1)
                        white_rate = int(match.group(2))
                    else:
                        white_player_name = value
                elif key in ["持ち時間"]:
                    match = re.match(r"(\d+)時間(\d+)分", value)
                    if match:
                        minutes = int(match.group(1)) * 60 + int(match.group(2))
                        seconds = minutes * 60
                        time_specs["common"] = f"{seconds}+0+0"
                elif key in ["先手持ち時間", "下手持ち時間"]:
                    match = re.match(r"(\d+)時間(\d+)分", value)
                    if match:
                        minutes = int(match.group(1)) * 60 + int(match.group(2))
                        seconds = minutes * 60
                        time_specs["+"] = f"{seconds}+0+0"
                elif key in ["後手持ち時間", "上手持ち時間"]:
                    match = re.match(r"(\d+)時間(\d+)分", value)
                    if match:
                        minutes = int(match.group(1)) * 60 + int(match.group(2))
                        seconds = minutes * 60
                        time_specs["-"] = f"{seconds}+0+0"
                elif key in ["秒読み"]:
                    match = re.match(r"(\d+)秒", value)
                    if match:
                        seconds = int(match.group(1))
                        base = time_specs["common"] or "0+0+0"
                        base_seconds = base.split("+")[0]
                        time_specs["common"] = f"{base_seconds}+{seconds}+0"
                elif key in ["先手秒読み", "下手秒読み"]:
                    match = re.match(r"(\d+)秒", value)
                    if match:
                        seconds = int(match.group(1))
                        base = time_specs["+"] or time_specs["common"] or "0+0+0"
                        base_seconds = base.split("+")[0]
                        time_specs["+"] = f"{base_seconds}+{seconds}+0"
                elif key in ["後手秒読み", "上手秒読み"]:
                    match = re.match(r"(\d+)秒", value)
                    if match:
                        seconds = int(match.group(1))
                        base = time_specs["-"] or time_specs["common"] or "0+0+0"
                        base_seconds = base.split("+")[0]
                        time_specs["-"] = f"{base_seconds}+{seconds}+0"
                elif key == "手合割":
                    handicap_name = value
                idx += 1
                continue

            idx += 1

        ensure_hand_dict(hand_counts)

        if board_map:
            board_sfen = board_map_to_sfen(board_map)
            hands_sfen = hand_counts_to_sfen(hand_counts)
            init_position_sfen = f"{board_sfen} b {hands_sfen} 1"
            self.board.set_sfen(init_position_sfen)
        elif handicap_name and handicap_name in handicap_to_sfen_dict:
            init_position_sfen = handicap_to_sfen_dict[handicap_name]
            self.board.set_sfen(init_position_sfen)
        else:
            init_position_sfen = cshogi.STARTING_SFEN
            self.board.reset()

        moves: list[int] = []
        move_times: list[int | None] = []
        move_comments: list[str | None] = []
        last_to_square: int | None = None
        end_marker: str | None = None
        winner: int | None = None
        fallback_end_marker: str | None = None
        illegal_turn: int | None = None

        while idx < len(lines):
            line = lines[idx]
            idx += 1
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("変化："):
                break
            if stripped.startswith("*"):
                comment = stripped[1:].lstrip()
                if moves:
                    prev_comment = move_comments[-1]
                    if prev_comment is not None:
                        move_comments[-1] = prev_comment + "\n" + comment
                    else:
                        move_comments[-1] = comment
                else:
                    game_comments.append(comment)
                continue
            if stripped.startswith("まで"):
                summary_match = _RESULT_SUMMARY_PATTERN.match(stripped)
                if summary_match and end_marker and winner is not None:
                    continue
                break

            working_line = line
            if stripped.startswith("手数＝"):
                m_hand = re.match(r"^手数＝\s*(\d+)\s*(.*)$", stripped)
                if not m_hand:
                    continue
                move_no = int(m_hand.group(1))
                rest = m_hand.group(2).lstrip()
                if rest and rest[0] in {"▲", "△"}:
                    rest = rest[1:]
                working_line = f"{move_no:4d} {rest}"

            move_line = working_line.replace("王", "玉").replace("竜", "龍")
            move_line = move_line.replace("成銀", "全").replace("成桂", "圭").replace("成香", "杏")
            m = _MOVE_PATTERN.match(move_line)
            if m is None:
                continue

            move_time_str = m.group(11)
            move_time_ms: int | None
            if move_time_str:
                time_part = move_time_str.strip()
                try:
                    minute_str, second_str = time_part.split(":")
                except ValueError:
                    move_time_ms = None
                else:
                    try:
                        move_time_ms = (int(minute_str) * 60 + int(second_str)) * 1000
                    except ValueError:
                        move_time_ms = None
            else:
                move_time_ms = None

            if m.group(1) not in _RESULT_NAMES:
                piece_type = cshogi.PIECE_JAPANESE_SYMBOLS.index(m.group(5))
                if m.group(2) == "同　":
                    if last_to_square is None:
                        raise KIFParseError("同の指し手で直前の移動先が不明です")
                    to_square = last_to_square
                else:
                    to_field = cshogi.NUMBER_JAPANESE_NUMBER_SYMBOLS.index(m.group(3)) - 1
                    to_rank = cshogi.NUMBER_JAPANESE_KANJI_SYMBOLS.index(m.group(4)) - 1
                    to_square = to_rank + to_field * 9
                    last_to_square = to_square

                if m.group(6) == "打":
                    move_usi = f"{cshogi.PIECE_SYMBOLS[piece_type].upper()}*{cshogi.SQUARE_NAMES[to_square]}"
                else:
                    from_field = int(m.group(8)) - 1
                    from_rank = int(m.group(9)) - 1
                    from_square = from_rank + from_field * 9
                    promotion = m.group(7) == "成"
                    move_usi = (
                        cshogi.SQUARE_NAMES[from_square] + cshogi.SQUARE_NAMES[to_square] + ("+" if promotion else "")
                    )

                move = self.board.move_from_usi(move_usi)
                if not self.board.is_legal(move):
                    if fallback_end_marker is None:
                        fallback_end_marker = "%ILLEGAL_MOVE"
                        illegal_turn = self.board.turn
                    break

                moves.append(move)
                move_times.append(move_time_ms)
                move_comments.append(None)
                self.board.push(move)
            else:
                end_name = m.group(1)
                end_marker, winner = resolve_result_from_name(end_name, self.board.turn)
                break

        if end_marker is None and fallback_end_marker is not None:
            end_marker = fallback_end_marker
            turn_for_winner = illegal_turn if illegal_turn is not None else self.board.turn
            winner = 2 - turn_for_winner

        if end_marker is None:
            end_marker = "%CHUDAN"
        if winner is None:
            winner = 0

        moves.append(MOVE_END)
        if len(move_times) == len(moves) - 1:
            move_times.append(None)
        if len(move_comments) == len(moves) - 1:
            move_comments.append(None)

        if len(move_times) != len(moves):
            raise KIFParseError("Move times do not align with moves")
        if len(move_comments) != len(moves):
            raise KIFParseError("Move comments do not align with moves")

        game_comment = "\n".join(game_comments) if game_comments else None

        black_time_control, white_time_control = resolve_time_controls(time_specs)

        return {
            "kisen": kisen,
            "black_player_name": black_player_name,
            "white_player_name": white_player_name,
            "black_rate": black_rate,
            "white_rate": white_rate,
            "black_time_control": black_time_control,
            "white_time_control": white_time_control,
            "start_date": start_date,
            "end_date": end_date,
            "endgame": end_marker,
            "win": winner,
            "init_position_sfen": init_position_sfen,
            "moves": moves,
            "move_times": move_times,
            "move_comments": move_comments,
            "game_comment": game_comment,
        }


def sec_to_time(sec: float) -> tuple[int, int, int]:
    h, m_ = divmod(math.ceil(sec), 60 * 60)
    m, s = divmod(m_, 60)
    return h, m, s


def move_to_line(board: cshogi.Board, move: int, sec: float = 0, sec_sum: float = 0) -> str:
    prev_move = board.history[-1] if board.history else None
    m, s = divmod(math.ceil(sec), 60)
    h_sum, m_sum, s_sum = sec_to_time(sec_sum)

    if cshogi.move_is_drop(move):
        padding = "    "
    elif cshogi.move_is_promotion(move):
        padding = ""
    else:
        padding = "  "
    move_str = move_to_kif(move, prev_move) + padding

    return f"{board.move_number:>4} {move_str}      ({m:>2}:{s:02}/{h_sum:02}:{m_sum:02}:{s_sum:02})"


def endgame_to_lines(
    board: cshogi.Board, result_code: int, handicap: str = "平手", sec: float = 0, sec_sum: float = 0
) -> list[str]:
    ply = board.move_number
    m, s = divmod(math.ceil(sec), 60)
    h_sum, m_sum, s_sum = sec_to_time(sec_sum)

    if handicap == "平手":
        turn_str = {0: "先手", 1: "後手", 2: None, 3: None}[result_code % 4]
    else:
        turn_str = {0: "上手", 1: "下手", 2: None, 3: None}[result_code % 4]

    # NOTE: result_code=3 (ERROR) used to raise; handle gracefully
    if result_code in [0, 1]:
        move_str = "投了        "
        reason_line = f"まで{ply - 1}手で{turn_str}の勝ち"
    elif result_code in [16, 17]:
        move_str = "切れ負け    "
        reason_line = f"まで{ply - 1}手で{turn_str}の勝ち"
    elif result_code in [4, 5]:
        # 入玉宣言は宣言を手としてカウントしたほうが自然なはず。先手勝ちのとき奇数手になる。
        move_str = "入玉宣言    "
        reason_line = f"まで{ply}手で入玉宣言"
    elif result_code in [8, 9]:
        move_str = "不戦勝      "
        reason_line = f"まで{ply - 1}手で{turn_str}の不戦勝"
    elif result_code in [12, 13]:
        if board.turn == result_code % 4:
            move_str = "反則勝ち    "
        else:
            move_str = "反則負け    "
        reason_line = f"まで{ply - 1}手で{turn_str}の反則勝ち"
    elif result_code in [
        2,
    ]:
        move_str = "千日手      "
        reason_line = f"まで{ply - 1}手で千日手"
    elif result_code in [
        6,
    ]:
        move_str = "持将棋      "
        reason_line = f"まで{ply - 1}手で持将棋"
    elif result_code in [7, 10, 3]:
        move_str = "中断        "
        reason_line = f"まで{ply - 1}手で中断"
    else:
        raise ValueError(result_code)

    lastmove_line = f"{ply:>4} {move_str}      ({m:>2}:{s:02}/{h_sum:02}:{m_sum:02}:{s_sum:02})"

    return [lastmove_line, reason_line]
