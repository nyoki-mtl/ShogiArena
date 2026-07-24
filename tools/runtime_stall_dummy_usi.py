#!/usr/bin/env python3
"""Deterministic CPU-light USI engine used by the runtime-stall probe."""

from __future__ import annotations

import argparse
import sys
import time

try:
    from rsshogi.core import Board
except ImportError:  # ShogiArena 0.5.x used the pre-rename package.
    from rshogi.core import Board


def _send(line: str) -> None:
    sys.stdout.write(f"{line}\n")
    sys.stdout.flush()


def _board_from_position(command: str) -> Board:
    tokens = command.split()
    if len(tokens) < 2 or tokens[0] != "position":
        raise ValueError(f"unsupported position command: {command}")
    board = Board()
    index = 1
    if tokens[index] == "startpos":
        index += 1
    elif tokens[index] == "sfen":
        if len(tokens) < index + 5:
            raise ValueError(f"incomplete sfen command: {command}")
        board.set_sfen(" ".join(tokens[index + 1 : index + 5]))
        index += 5
    else:
        raise ValueError(f"unsupported position command: {command}")
    if index < len(tokens):
        if tokens[index] != "moves":
            raise ValueError(f"unexpected position suffix: {command}")
        for move in tokens[index + 1 :]:
            board.push_usi(move)
    return board


def _choose_move(position_command: str) -> str:
    board = _board_from_position(position_command)
    if hasattr(board, "legal_moves_move32"):
        legal_moves = board.legal_moves_move32()
    else:
        legal_moves = board.legal_moves_full()
    moves = sorted(move.to_usi() for move in legal_moves)
    return moves[0] if moves else "resign"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--delay-ms", type=float, default=15.0)
    parser.add_argument("--info-lines", type=int, default=4)
    return parser.parse_args()


def main() -> int:
    """Run the minimal USI protocol loop."""
    args = _parse_args()
    if args.delay_ms < 0:
        raise ValueError("--delay-ms must be non-negative")
    if args.info_lines < 0:
        raise ValueError("--info-lines must be non-negative")
    position_command = "position startpos"
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        if line == "usi":
            _send("id name RuntimeStallDummy")
            _send("id author ShogiArena")
            _send("option name Threads type spin default 1 min 1 max 1")
            _send("usiok")
        elif line == "isready":
            _send("readyok")
        elif line.startswith("position "):
            position_command = line
        elif line.startswith("go"):
            move = _choose_move(position_command)
            for index in range(args.info_lines):
                _send(f"info string runtime-stall-probe line={index}")
            _send(f"info depth 1 seldepth 1 nodes 1 time 1 score cp 0 pv {move}")
            time.sleep(args.delay_ms / 1000.0)
            _send(f"bestmove {move}")
        elif line == "stop":
            _send("bestmove resign")
        elif line == "quit":
            return 0
        elif line in {"usinewgame"} or line.startswith(("setoption ", "gameover ")):
            continue
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
