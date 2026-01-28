import logging
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import cshogi

from shogiarena.utils.common.constants import MOVE_END

from .common import (
    board_map_to_sfen,
    hand_counts_to_sfen,
    resolve_time_controls,
)

logger = logging.getLogger(__name__)


class CSAParseError(Exception):
    pass


_DRAW_MARKERS = {
    "%CHUDAN",
    "%SENNICHITE",
    "%JISHOGI",
    "%HIKIWAKE",
    "%MAX_MOVES",
    "%FUZUMI",
    "%ERROR",
    "%MATTA",
}

_LOSS_MARKERS = {"%TORYO", "%TIME_UP", "%ILLEGAL_MOVE", "%TSUMI"}

_PROMOTED_TO_BASE = {
    "TO": "FU",
    "NY": "KY",
    "NK": "KE",
    "NG": "GI",
    "UM": "KA",
    "RY": "HI",
}

_PROMOTION_MAP = {
    "FU": "TO",
    "KY": "NY",
    "KE": "NK",
    "GI": "NG",
    "KA": "UM",
    "HI": "RY",
}

_INITIAL_COUNTS = {
    "+": {"FU": 9, "KY": 2, "KE": 2, "GI": 2, "KI": 2, "KA": 1, "HI": 1, "OU": 1},
    "-": {"FU": 9, "KY": 2, "KE": 2, "GI": 2, "KI": 2, "KA": 1, "HI": 1, "OU": 1},
}


def compute_winner_from_marker(marker: str, turn: int) -> int:
    if marker in _DRAW_MARKERS:
        return 0
    if marker in _LOSS_MARKERS:
        return 2 - turn
    if marker == "%KACHI":
        return 1 + turn
    if marker == "%+ILLEGAL_ACTION":
        return 2
    if marker == "%-ILLEGAL_ACTION":
        return 1
    raise CSAParseError(f"Unknown end marker: {marker}")


def parse_time_limit_v22(value: str) -> str | None:
    """
    http://www2.computer-shogi.org/protocol/record_v22.html
    """
    if "+" not in value:
        return None
    base_part, byo_str = value.split("+", 1)
    parts = base_part.split(":")
    if len(parts) != 2:
        return None
    try:
        hour_str, minute_str = parts
        total_seconds = int(hour_str) * 3600 + int(minute_str) * 60
        byoyomi = int(byo_str)
    except ValueError:
        return None
    return f"{total_seconds}+{byoyomi}+0"


def parse_time_limit_v30(value: str) -> str | None:
    """
    http://www2.computer-shogi.org/protocol/record_v3.html
    """
    cleaned = value.strip()
    parts = cleaned.split("+")
    if len(parts) != 3:
        return None
    try:
        base, byoyomi, increment = parts
        Decimal(base)
        Decimal(byoyomi)
        Decimal(increment)
    except InvalidOperation:
        return None
    return "+".join(parts)


def base_piece(piece_code: str) -> str:
    return _PROMOTED_TO_BASE.get(piece_code, piece_code)


def decode_start_board() -> dict[str, tuple[str, str]]:
    board_map: dict[str, tuple[str, str]] = {}
    board_part = cshogi.STARTING_SFEN.split(" ")[0]
    rows = board_part.split("/")
    sfen_to_csa = {
        "P": "FU",
        "L": "KY",
        "N": "KE",
        "S": "GI",
        "G": "KI",
        "B": "KA",
        "R": "HI",
        "K": "OU",
    }
    for rank_index, row in enumerate(rows):
        rank = rank_index + 1
        file = 9
        i = 0
        while file >= 1 and i < len(row):
            char = row[i]
            if char.isdigit():
                file -= int(char)
                i += 1
                continue
            promoted = False
            if char == "+":
                promoted = True
                i += 1
                char = row[i]
            color = "+" if char.isupper() else "-"
            piece_letter = char.upper()
            piece_code = sfen_to_csa[piece_letter]
            if promoted:
                piece_code = _PROMOTION_MAP.get(piece_code, piece_code)
            square = f"{file}{rank}"
            board_map[square] = (color, piece_code)
            file -= 1
            i += 1
    return board_map


def parse_row_lines(lines: list[str]) -> dict[str, tuple[str, str]]:
    board_map: dict[str, tuple[str, str]] = {}
    rows: dict[int, str] = {}
    for row_line in lines:
        rank = int(row_line[1])
        rows[rank] = row_line[2:]
    for rank in range(1, 10):
        row_spec = rows.get(rank, "")
        compact = row_spec.replace(" ", "")
        file = 9
        i = 0
        while file >= 1 and i < len(compact):
            token = compact[i]
            if token in "+-":
                piece_code = compact[i + 1 : i + 3]
                board_map[f"{file}{rank}"] = (token, piece_code)
                i += 3
            elif token == "*":
                i += 1
            else:
                raise CSAParseError(f"Invalid board row token: {row_spec}")
            file -= 1
    return board_map


def parse_piece_line(line: str) -> list[tuple[str, str, str]]:
    color = line[1]
    data = line[2:].strip().replace(" ", "")
    if not data:
        return []
    if len(data) % 4 != 0:
        raise CSAParseError(f"Malformed CSA piece line: {line}")
    result = []
    for i in range(0, len(data), 4):
        square = data[i : i + 2]
        piece_code = data[i + 2 : i + 4]
        result.append((color, square, piece_code))
    return result


class CSAParser:
    def __init__(self, strict_warn: bool = False) -> None:
        self.board = cshogi.Board()
        self.strict_warn = strict_warn

    def warning(self, msg: str) -> None:
        if self.strict_warn:
            raise CSAParseError(msg)
        logger.warning(msg)

    def parse_csa(self, csa_path: Path, encoding: str = "utf-8") -> dict[str, Any]:
        csa_str = csa_path.read_text(encoding=encoding)
        return self.parse_csa_str(csa_str)

    def parse_csa_str(self, csa_str: str) -> dict[str, Any]:
        lines = [line.rstrip("\r") for line in csa_str.splitlines()]
        if lines:
            lines[0] = lines[0].lstrip("\ufeff")

        self.board.reset()

        black_player_name: str | None = None
        white_player_name: str | None = None
        start_date: datetime | None = None
        end_date: datetime | None = None
        event: str | None = None
        black_rate = 0
        white_rate = 0
        init_position_sfen = cshogi.STARTING_SFEN
        game_comments: list[str] = []
        moves: list[int] = []
        move_times: list[int | None] = []
        move_comments: list[str | None] = []
        end_marker: str | None = None
        winner: int | None = None
        finished = False
        fallback_end_marker: str | None = None
        illegal_turn: int | None = None

        position_lines: list[str] = []
        side_to_move_token: str | None = None
        time_specs: dict[str, str | None] = {"common": None, "+": None, "-": None}

        def parse_time(token: str) -> int:
            if not token.startswith("T"):
                raise CSAParseError(f"Unexpected time segment: {token}")
            raw_value = token[1:]
            try:
                seconds = Decimal(raw_value)
            except InvalidOperation as exc:  # pragma: no cover - defensive
                raise CSAParseError(f"Invalid time value: {token}") from exc
            if seconds < 0:
                raise CSAParseError(f"Move time must be non-negative: {token}")
            return int((seconds * 1000).to_integral_value())

        def append_move(move_value: int) -> None:
            moves.append(move_value)
            move_times.append(None)
            move_comments.append(None)

        def ensure_move_available() -> None:
            if not moves:
                raise CSAParseError("Move-dependent line appeared before any move")

        def set_last_move_time(token: str) -> None:
            ensure_move_available()
            move_times[-1] = parse_time(token)

        def set_last_move_comment(text: str) -> None:
            ensure_move_available()
            move_comments[-1] = text

        def apply_suffixes(tokens: list[str]) -> None:
            for token in tokens:
                token = token.strip()
                if not token:
                    continue
                if token.startswith("T"):
                    set_last_move_time(token)
                elif token.startswith("'"):
                    set_last_move_comment(token[1:].lstrip())
                else:
                    self.warning(f"Unsupported CSA token after move: {token}")

        header_end = len(lines)
        for idx, raw_line in enumerate(lines):
            line = raw_line.strip()
            if not line:
                header_end = idx + 1
                break
            if line[0] in "+-%":
                if len(line) >= 7:
                    header_end = idx
                    break
                side_to_move_token = line[0]
                continue
            if line.startswith("'"):
                comment = line[1:].lstrip()
                lowered = comment.lower()
                if lowered.startswith("black_rate:"):
                    try:
                        black_rate = int(float(comment.split(":", maxsplit=1)[1]))
                    except ValueError as exc:
                        raise CSAParseError(f"Invalid black_rate value: {comment}") from exc
                    continue
                if lowered.startswith("white_rate:"):
                    try:
                        white_rate = int(float(comment.split(":", maxsplit=1)[1]))
                    except ValueError as exc:
                        raise CSAParseError(f"Invalid white_rate value: {comment}") from exc
                    continue
                game_comments.append(comment)
                continue
            if line.startswith("N+"):
                black_player_name = line[2:]
                continue
            if line.startswith("N-"):
                white_player_name = line[2:]
                continue
            if line.startswith("$START_TIME:"):
                start_date = datetime.strptime(line[12:], "%Y/%m/%d %H:%M:%S")
                continue
            if line.startswith("$END_TIME:"):
                end_date = datetime.strptime(line[10:], "%Y/%m/%d %H:%M:%S")
                continue
            if line.startswith("$EVENT:"):
                event = line[7:]
                continue
            if line.startswith("$TIME_LIMIT:"):
                parsed = parse_time_limit_v22(line[12:])
                if parsed is None:
                    self.warning(f"Unsupported $TIME_LIMIT value: {line}")
                else:
                    time_specs["common"] = parsed
                continue
            if line.startswith("$TIME+:"):
                parsed = parse_time_limit_v30(line[7:])
                if parsed is None:
                    self.warning(f"Unsupported $TIME+ value: {line}")
                else:
                    time_specs["+"] = parsed
                continue
            if line.startswith("$TIME-:"):
                parsed = parse_time_limit_v30(line[7:])
                if parsed is None:
                    self.warning(f"Unsupported $TIME- value: {line}")
                else:
                    time_specs["-"] = parsed
                continue
            if line.startswith("$TIME:"):
                parsed = parse_time_limit_v30(line[6:])
                if parsed is None:
                    self.warning(f"Unsupported $TIME value: {line}")
                else:
                    time_specs["common"] = parsed
                continue
            if line.startswith("PI") or line.startswith("P"):
                position_lines.append(line)
                continue

        idx = header_end

        board_map: dict[str, tuple[str, str]] = {}
        hand_counts: dict[str, dict[str, int]] = {"+": {}, "-": {}}
        fill_remaining: dict[str, bool] = {"+": False, "-": False}

        pi_line = next((line for line in position_lines if line.startswith("PI")), None)
        row_lines = [
            line for line in position_lines if len(line) >= 2 and line.startswith("P") and line[1] in "123456789"
        ]
        piece_lines = [
            line for line in position_lines if line.startswith("P+") or line.startswith("P-") or line.startswith("P0")
        ]

        if not position_lines:
            board_map = decode_start_board()
        elif row_lines:
            board_map = parse_row_lines(row_lines)
        elif pi_line is not None:
            board_map = decode_start_board()
            suffix = pi_line[2:].strip()
            if suffix:
                if len(suffix) % 4 != 0:
                    raise CSAParseError(f"Invalid PI specification: {pi_line}")
                for i in range(0, len(suffix), 4):
                    square = suffix[i : i + 2]
                    board_map.pop(square, None)
        else:
            board_map = {}

        for entry_line in piece_lines:
            for color, square, piece_code in parse_piece_line(entry_line):
                if square == "00":
                    if piece_code == "AL":
                        fill_remaining[color] = True
                        continue
                    hand_counts[color][piece_code] = hand_counts[color].get(piece_code, 0) + 1
                else:
                    board_map[square] = (color, piece_code)

        placed_counts = {color: dict.fromkeys(counts, 0) for color, counts in _INITIAL_COUNTS.items()}

        for color, piece_code in board_map.values():
            base = base_piece(piece_code)
            if base in placed_counts[color]:
                placed_counts[color][base] += 1

        for color in ("+", "-"):
            for piece_code, count in hand_counts[color].items():
                base = base_piece(piece_code)
                if base in placed_counts[color]:
                    placed_counts[color][base] += count

        for color in ("+", "-"):
            if not fill_remaining[color]:
                continue
            for piece_code, initial in _INITIAL_COUNTS[color].items():
                remaining = initial - placed_counts[color][piece_code]
                if remaining < 0:
                    remaining = 0
                if remaining:
                    hand_counts[color][piece_code] = hand_counts[color].get(piece_code, 0) + remaining
                    placed_counts[color][piece_code] += remaining

        board_sfen = board_map_to_sfen(board_map)
        side_char = side_to_move_token if side_to_move_token in {"+", "-"} else "+"
        turn = "b" if side_char == "+" else "w"
        hands_sfen = hand_counts_to_sfen(hand_counts)
        init_position_sfen = f"{board_sfen} {turn} {hands_sfen} 1"

        self.board.set_sfen(init_position_sfen)

        black_time_control, white_time_control = resolve_time_controls(time_specs)

        while idx < len(lines):
            line = lines[idx].strip()
            idx += 1

            if not line:
                continue

            if finished:
                if line.startswith("'"):
                    game_comments.append(line[1:].lstrip())
                    continue
                if line.startswith("T"):
                    set_last_move_time(line)
                    continue
                raise CSAParseError(f"Unexpected content after end marker: {line}")

            if line.startswith("'"):
                comment = line[1:].lstrip()
                if comment.startswith("*") and moves:
                    set_last_move_comment(comment)
                else:
                    game_comments.append(comment)
                continue

            if line.startswith("T"):
                set_last_move_time(line)
                continue

            if line[0] in "+-":
                parts = line.split(",")
                move_field = parts[0]
                if len(move_field) < 7:
                    raise CSAParseError(f"Invalid move line: {line}")
                move = self.board.move_from_csa(move_field[1:7])
                if not self.board.is_legal(move):
                    self.warning(f"Illegal move detected: {move_field}")
                    if fallback_end_marker is None:
                        fallback_end_marker = "%ILLEGAL_MOVE"
                        illegal_turn = self.board.turn
                    append_move(MOVE_END)
                    apply_suffixes(parts[1:])
                    continue
                self.board.push(move)
                append_move(move)
                apply_suffixes(parts[1:])
                continue

            if line.startswith("%"):
                parts = line.split(",")
                marker = parts[0]
                if marker.startswith("%%"):
                    marker = marker[1:]
                end_marker = marker
                winner = compute_winner_from_marker(marker, self.board.turn)
                append_move(MOVE_END)
                finished = True
                apply_suffixes(parts[1:])
                continue

            raise CSAParseError(f"Unrecognized CSA line: {line}")

        if end_marker is None and fallback_end_marker is not None:
            end_marker = fallback_end_marker
            turn_for_winner = self.board.turn if illegal_turn is None else illegal_turn
            winner = compute_winner_from_marker(end_marker, turn_for_winner)
            finished = True

        if not finished or end_marker is None or winner is None:
            raise CSAParseError("CSA record terminated without an end marker")

        if len(move_times) != len(moves):
            raise CSAParseError("Move times do not align with moves")
        if len(move_comments) != len(moves):
            raise CSAParseError("Move comments do not align with moves")

        game_comment = "\n".join(game_comments) if game_comments else None

        return {
            "black_player_name": black_player_name,
            "white_player_name": white_player_name,
            "black_rate": black_rate,
            "white_rate": white_rate,
            "start_date": start_date,
            "end_date": end_date,
            "event": event,
            "endgame": end_marker,
            "win": winner,
            "init_position_sfen": init_position_sfen,
            "moves": moves,
            "move_times": move_times,
            "move_comments": move_comments,
            "game_comment": game_comment,
            "black_time_control": black_time_control,
            "white_time_control": white_time_control,
        }
