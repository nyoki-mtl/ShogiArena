#!/usr/bin/env python
"""Generate synthetic ``{run_id}-events.jsonl`` fixtures for the CSA watch tests.

Real bridge logs are never committed: they carry opponent engine names and
absolute engine paths. The shape of these fixtures is taken from real logs, but
every name, path and identifier is invented and every move is generated against a
real board so that replay of a fixture is a meaningful check.

Usage::

    uv run python tools/generate_csa_event_fixtures.py
"""

from __future__ import annotations

import argparse
import json
import random
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

from rsshogi.core import Board
from rsshogi.types import Color

DEFAULT_OUTPUT_DIR = Path("tests/fixtures/csa_watch")

HIRATE_SFEN = "lnsgkgsnl/1r5b1/ppppppppp/9/9/9/PPPPPPPPP/1B5R1/LNSGKGSNL b - 1"
LANCE_HANDICAP_SFEN = "lnsgkgsn1/1r5b1/ppppppppp/9/9/9/PPPPPPPPP/1B5R1/LNSGKGSNL w - 1"

BRIDGE_VERSION = "0.1.0"
ENGINE_CMD = "/opt/engines/fixture-engine/fixture-engine"
EVENT_SCHEMA = 1
SITE = "wdoor+floodgate-300-10F"
OUR_NAME = "fixture-arena"

TOTAL_MS = 300_000
INC_MS = 10_000


def _time_block() -> dict[str, Any]:
    return {
        "byoyomi_ms": 0,
        "inc_ms": INC_MS,
        "least_ms": 0,
        "roundup": False,
        "total_ms": TOTAL_MS,
        "unit_ms": 1000,
    }


class EventLogBuilder:
    """Emits records with the envelope invariants the reader relies on."""

    def __init__(self, *, start_ts_ms: int) -> None:
        self._records: list[dict[str, Any]] = []
        self._seq = 0
        self._at = 0
        self._start_ts = start_ts_ms
        self.game_id: str | None = None

    @property
    def records(self) -> tuple[dict[str, Any], ...]:
        return tuple(self._records)

    @property
    def at(self) -> int:
        return self._at

    def advance(self, delta_ms: int) -> None:
        self._at += delta_ms

    def emit(self, record_type: str, **body: Any) -> dict[str, Any]:
        self._seq += 1
        record = {
            "at": self._at,
            "game_id": self.game_id,
            "schema": EVENT_SCHEMA,
            "seq": self._seq,
            "ts": self._start_ts + self._at,
            "type": record_type,
            **body,
        }
        self._records.append(record)
        return record

    def drop_seq(self, count: int) -> None:
        """Simulate records that never reached the file."""
        self._seq += count

    def to_text(self, *, truncate_tail: bool = False) -> str:
        lines = [json.dumps(record, ensure_ascii=False, sort_keys=True) for record in self._records]
        text = "\n".join(lines) + "\n"
        if truncate_tail:
            text = text.rstrip("\n")
            text = text[: len(text) - 25]
        return text


class ScriptedGame:
    """A legal game played by picking moves deterministically from a seeded pool."""

    def __init__(self, initial_sfen: str, *, seed: int) -> None:
        self.initial_sfen = initial_sfen
        self._board = Board(initial_sfen)
        self._rng = random.Random(seed)

    def next_moves(self, count: int) -> list[str]:
        """Peek at a legal continuation without advancing the game."""
        preview = Board(self._board.to_sfen())
        moves: list[str] = []
        for _ in range(count):
            legal = preview.legal_moves()
            if not legal:
                break
            move = legal[0]
            moves.append(move.to_usi())
            preview.apply_move(move)
        return moves

    def play(self) -> tuple[str, str] | None:
        """Advance one ply. Returns ``(usi, csa)`` or ``None`` when mated."""
        legal = self._board.legal_moves()
        if not legal:
            return None
        move = legal[self._rng.randrange(len(legal))]
        # `to_csa` already carries the mover's sign.
        csa = self._board.move32_from_move(move).to_csa() or ""
        usi = move.to_usi()
        self._board.apply_move(move)
        return usi, csa

    @property
    def side_to_move(self) -> str:
        return "black" if self._board.turn == Color.BLACK else "white"

    def find_illegal_move(self) -> str:
        """A USI move that is illegal in the current position, chosen by construction.

        Hard-coding one would silently become legal if the scripted opening changed.
        """
        legal = {move.to_usi() for move in self._board.legal_moves()}
        for file in "123456789":
            for from_rank, to_rank in zip("abcdefgh", "bcdefghi", strict=True):
                candidate = f"{file}{from_rank}{file}{to_rank}"
                if candidate not in legal:
                    return candidate
        raise RuntimeError("every candidate move is legal, which cannot happen")


def _eval_body(game: ScriptedGame, ply: int) -> dict[str, Any]:
    return {
        "cp": ((ply * 37) % 400) - 120,
        "depth": 20 + (ply % 5),
        "mate": None,
        "nodes": 1_000_000 + ply * 4_321,
        "pv_usi": game.next_moves(3),
    }


def _write_game(
    builder: EventLogBuilder,
    game: ScriptedGame,
    *,
    game_id: str,
    my_color: str,
    black_name: str,
    white_name: str,
    plies: int,
    moves_so_far: Sequence[str] = (),
    with_ponder: bool = False,
    fallback_plies: Sequence[int] = (),
    finish: str | None = "win",
) -> None:
    builder.game_id = game_id
    builder.advance(1200)
    builder.emit(
        "game_start",
        entering_king_rule="CSARule27",
        initial_sfen=game.initial_sfen,
        max_moves=512,
        moves_so_far=list(moves_so_far),
        my_color=my_color,
        names={"black": black_name, "white": white_name},
        time=_time_block(),
    )
    builder.emit("state", phase="agreed")
    builder.advance(20)
    builder.emit("state", phase="playing")

    black_remaining = TOTAL_MS
    white_remaining = TOTAL_MS
    base_ply = len(moves_so_far)
    deadline_offset = 300_000

    builder.emit(
        "go",
        deadline_at=builder.at + deadline_offset,
        ply=base_ply,
        times={
            "binc_ms": INC_MS,
            "btime_ms": black_remaining,
            "byoyomi_ms": None,
            "winc_ms": INC_MS,
            "wtime_ms": white_remaining,
        },
    )

    for offset in range(plies):
        ply = base_ply + offset + 1
        side = game.side_to_move
        is_ours = side == my_color
        pv = _eval_body(game, ply) if is_ours else None
        played = game.play()
        if played is None:
            break
        usi, csa = played
        spent = 4_000 + (ply % 7) * 1_000
        if side == "black":
            black_remaining = max(0, black_remaining - spent + INC_MS)
        else:
            white_remaining = max(0, white_remaining - spent + INC_MS)
        builder.advance(spent)
        origin = "engine" if is_ours else "opponent"
        if ply in fallback_plies:
            origin = "fallback_pv"
        builder.emit(
            "move",
            black_remaining_ms=black_remaining,
            by=origin,
            csa=csa,
            eval=pv,
            ply=ply,
            side=side,
            t_ms=spent,
            usi=usi,
            white_remaining_ms=white_remaining,
        )
        if with_ponder and is_ours:
            predicted = game.next_moves(1)
            builder.emit(
                "ponder",
                actual_usi=None,
                deadline_at=None,
                outcome="started",
                ply=ply + 1,
                predicted_usi=predicted[0] if predicted else None,
            )
        elif with_ponder and not is_ours:
            builder.emit(
                "ponder",
                actual_usi=usi,
                deadline_at=builder.at + deadline_offset,
                outcome="hit" if (ply // 2) % 2 == 0 else "miss",
                ply=ply,
                predicted_usi=usi,
            )
        if offset + 1 < plies:
            builder.emit(
                "go",
                deadline_at=builder.at + deadline_offset,
                ply=ply,
                times={
                    "binc_ms": INC_MS,
                    "btime_ms": black_remaining,
                    "byoyomi_ms": None,
                    "winc_ms": INC_MS,
                    "wtime_ms": white_remaining,
                },
            )

    if finish is not None:
        terminal = {
            "win": ["%TORYO", "#RESIGN", "#WIN"],
            "lose": ["%TORYO", "#RESIGN", "#LOSE"],
            "draw": ["%SENNICHITE", "#SENNICHITE", "#DRAW"],
        }.get(finish, ["%CHUDAN"])
        builder.advance(500)
        builder.emit(
            "game_end",
            black_remaining_ms=black_remaining,
            result=finish,
            terminal=terminal,
            white_remaining_ms=white_remaining,
        )
        builder.game_id = None
        builder.emit("state", phase="waiting_pairing")


def _open_run(builder: EventLogBuilder) -> None:
    builder.emit("bridge_start", engine_cmd=ENGINE_CMD, version=BRIDGE_VERSION)
    builder.emit("state", phase="warming")
    builder.advance(800)
    builder.emit("state", phase="connecting")
    builder.advance(100)
    builder.emit("state", phase="waiting_pairing")


def build_even_complete() -> EventLogBuilder:
    builder = EventLogBuilder(start_ts_ms=1_800_000_000_000)
    _open_run(builder)
    game = ScriptedGame(HIRATE_SFEN, seed=11)
    _write_game(
        builder,
        game,
        game_id=f"{SITE}+{OUR_NAME}+fixture-opponent-a+20260805120000",
        my_color="black",
        black_name=OUR_NAME,
        white_name="fixture-opponent-a",
        plies=24,
        finish="win",
    )
    return builder


def build_ponder_outcomes() -> EventLogBuilder:
    builder = EventLogBuilder(start_ts_ms=1_800_001_000_000)
    _open_run(builder)
    game = ScriptedGame(HIRATE_SFEN, seed=23)
    _write_game(
        builder,
        game,
        game_id=f"{SITE}+fixture-opponent-b+{OUR_NAME}+20260805123000",
        my_color="white",
        black_name="fixture-opponent-b",
        white_name=OUR_NAME,
        plies=16,
        with_ponder=True,
        finish="lose",
    )
    return builder


def build_engine_death_fallback() -> EventLogBuilder:
    builder = EventLogBuilder(start_ts_ms=1_800_002_000_000)
    _open_run(builder)
    game = ScriptedGame(HIRATE_SFEN, seed=31)
    game_id = f"{SITE}+{OUR_NAME}+fixture-opponent-c+20260805130000"
    _write_game(
        builder,
        game,
        game_id=game_id,
        my_color="black",
        black_name=OUR_NAME,
        white_name="fixture-opponent-c",
        plies=10,
        fallback_plies=(9,),
        finish=None,
    )
    builder.game_id = game_id
    builder.emit("alert", code="engine_dead", detail="engine process ended", level="error")
    builder.advance(200)
    builder.emit("alert", code="engine_restarted", detail="restarting the engine (1 of 2)", level="warn")
    builder.advance(3_000)
    builder.emit(
        "game_end",
        black_remaining_ms=210_000,
        result="lose",
        terminal=["%TORYO", "#RESIGN", "#LOSE"],
        white_remaining_ms=250_000,
    )
    builder.game_id = None
    builder.emit("state", phase="closing")
    return builder


def build_future_records() -> EventLogBuilder:
    builder = EventLogBuilder(start_ts_ms=1_800_003_000_000)
    _open_run(builder)
    # A record type this build has never seen, and an unknown field on a known type.
    builder.emit("weather", outlook="unsettled", pressure_hpa=1004)
    game = ScriptedGame(HIRATE_SFEN, seed=41)
    _write_game(
        builder,
        game,
        game_id=f"{SITE}+{OUR_NAME}+fixture-opponent-d+20260805140000",
        my_color="black",
        black_name=OUR_NAME,
        white_name="fixture-opponent-d",
        plies=6,
        finish="draw",
    )
    builder.emit("weather", outlook="clearing")
    builder.emit("state", phase="closing", operator_note="added in a later schema")
    return builder


def build_seq_gap() -> EventLogBuilder:
    builder = EventLogBuilder(start_ts_ms=1_800_004_000_000)
    _open_run(builder)
    game = ScriptedGame(HIRATE_SFEN, seed=53)
    builder.drop_seq(3)
    _write_game(
        builder,
        game,
        game_id=f"{SITE}+{OUR_NAME}+fixture-opponent-e+20260805150000",
        my_color="black",
        black_name=OUR_NAME,
        white_name="fixture-opponent-e",
        plies=8,
        finish="win",
    )
    return builder


def build_buoy_handicap() -> EventLogBuilder:
    builder = EventLogBuilder(start_ts_ms=1_800_005_000_000)
    _open_run(builder)
    game = ScriptedGame(LANCE_HANDICAP_SFEN, seed=67)
    moves_so_far: list[str] = []
    for _ in range(4):
        played = game.play()
        if played is None:
            break
        moves_so_far.append(played[0])
    _write_game(
        builder,
        game,
        game_id=f"{SITE}+fixture-opponent-f+{OUR_NAME}+20260805160000",
        my_color="black",
        black_name="fixture-opponent-f",
        white_name=OUR_NAME,
        plies=6,
        moves_so_far=moves_so_far,
        finish="win",
    )
    return builder


def build_illegal_move() -> EventLogBuilder:
    """A log whose move list cannot be a real game. Replay must stop and say where."""
    builder = EventLogBuilder(start_ts_ms=1_800_006_000_000)
    _open_run(builder)
    game = ScriptedGame(HIRATE_SFEN, seed=71)
    game_id = f"{SITE}+{OUR_NAME}+fixture-opponent-g+20260805170000"
    _write_game(
        builder,
        game,
        game_id=game_id,
        my_color="black",
        black_name=OUR_NAME,
        white_name="fixture-opponent-g",
        plies=4,
        finish=None,
    )
    builder.game_id = game_id
    builder.advance(4_000)
    builder.emit(
        "move",
        black_remaining_ms=280_000,
        by="engine",
        csa=None,
        eval=None,
        ply=5,
        side=game.side_to_move,
        t_ms=4_000,
        usi=game.find_illegal_move(),
        white_remaining_ms=290_000,
    )
    builder.emit(
        "game_end",
        black_remaining_ms=280_000,
        result="unknown",
        terminal=["%CHUDAN"],
        white_remaining_ms=290_000,
    )
    return builder


FIXTURES: tuple[tuple[str, str, bool], ...] = (
    ("1800000000", "build_even_complete", False),
    ("1800001000", "build_ponder_outcomes", False),
    ("1800002000", "build_engine_death_fallback", False),
    ("1800003000", "build_future_records", False),
    ("1800004000", "build_seq_gap", False),
    ("1800005000", "build_buoy_handicap", False),
    ("1800006000", "build_illegal_move", False),
    ("1800007000", "build_even_complete", True),
)


def iter_fixtures() -> Iterator[tuple[str, str]]:
    builders = {
        "build_even_complete": build_even_complete,
        "build_ponder_outcomes": build_ponder_outcomes,
        "build_engine_death_fallback": build_engine_death_fallback,
        "build_future_records": build_future_records,
        "build_seq_gap": build_seq_gap,
        "build_buoy_handicap": build_buoy_handicap,
        "build_illegal_move": build_illegal_move,
    }
    for run_id, builder_name, truncate in FIXTURES:
        builder = builders[builder_name]()
        yield f"{run_id}-events.jsonl", builder.to_text(truncate_tail=truncate)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args(argv)

    output_dir: Path = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    for name, text in iter_fixtures():
        (output_dir / name).write_text(text, encoding="utf-8", newline="\n")
        print(f"wrote {output_dir / name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
