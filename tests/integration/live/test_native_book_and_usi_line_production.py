"""Production CLI smoke for NativeBook (.book) preflight/provenance and usi_line history."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import sys
import textwrap
from pathlib import Path

import pytest
import yaml
from rsshogi.book import NativeBookEditor, NativeCandidate
from rsshogi.core import Board, Move

from shogiarena._core.contexts.dashboard.application.game.detail_builder import build_game_detail_payload
from shogiarena._core.platform.db.store.arena_db_adapter import ArenaDBAdapter
from shogiarena._core.platform.db.store.repository_factory import SQLiteShogiDBFactory
from shogiarena.cli import main

_ENGINE_SOURCE = """\
#!/usr/bin/env python3
from pathlib import Path
import sys

LOG_PATH = Path(__LOG_PATH__)
BLACK_MOVES = ["5i5h", "5h5i", "5i5h", "5h5i", "7g7f"]
WHITE_MOVES = ["5a5b", "5b5a", "5a5b", "5b5a", "3c3d"]
played_moves = 0
move_idx = 0


def send(line: str) -> None:
    sys.stdout.write(line + "\\n")
    sys.stdout.flush()


while True:
    raw = sys.stdin.readline()
    if not raw:
        break
    line = raw.strip()
    if not line:
        continue
    with LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write(line + "\\n")
    if line == "usi":
        send("id name NativeBookUsiLineMock")
        send("option name USI_Ponder type check default false")
        send("option name MaxGameMoves type spin default 0 min 0 max 100000")
        send("option name BookFile type filename default <empty>")
        send("option name BookEnableFlip type check default false")
        send("option name BookIgnorePly type check default false")
        send("usiok")
    elif line == "isready":
        send("readyok")
    elif line == "quit":
        break
    elif line.startswith("position "):
        played_moves = len(line.split(" moves ", 1)[1].split()) if " moves " in line else 0
    elif line.startswith("go"):
        is_white = played_moves % 2 == 1
        seq = WHITE_MOVES if is_white else BLACK_MOVES
        mv = seq[move_idx % len(seq)]
        move_idx += 1
        for reply in __REPLIES__ or ("bestmove " + mv,):
            send(reply)
    elif line == "stop":
        send("bestmove resign")
"""


def _write_launcher(engines_dir: Path, name: str, log_path: Path, *, replies: tuple[str, ...] = ()) -> Path:
    script = engines_dir / f"{name}.py"
    script.write_text(
        textwrap.dedent(_ENGINE_SOURCE)
        .replace("__LOG_PATH__", repr(str(log_path)))
        .replace("__REPLIES__", repr(replies)),
        encoding="utf-8",
    )
    os.chmod(script, 0o755)
    if not sys.platform.startswith("win"):
        return script
    launcher = script.with_suffix(".bat")
    launcher.write_text(f'@echo off\r\n"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
    return launcher


def test_production_cli_runs_native_book_preflight_and_usi_line_history(tmp_path: Path) -> None:
    book_path = tmp_path / "StandardBook.book"
    editor = NativeBookEditor.create(str(book_path))
    editor.upsert_candidate(Board(), NativeCandidate(Move.from_usi("7g7f")))
    editor.save()
    editor.close()

    lines_path = tmp_path / "openings.txt"
    lines_path.write_text("startpos moves 5i5h 5a5b 5h5i 5b5a\n", encoding="utf-8")

    engines_dir = tmp_path / "engines"
    engines_dir.mkdir()
    pos_log_b = tmp_path / "positions_black.log"
    pos_log_w = tmp_path / "positions_white.log"
    launcher_b = _write_launcher(engines_dir, "eng_b", pos_log_b)
    launcher_w = _write_launcher(engines_dir, "eng_w", pos_log_w)
    eng_b_yaml = engines_dir / "eng_b.yaml"
    eng_w_yaml = engines_dir / "eng_w.yaml"
    eng_b_yaml.write_text(yaml.safe_dump({"name": "EngineB", "engine_path": launcher_b.as_posix()}), encoding="utf-8")
    eng_w_yaml.write_text(yaml.safe_dump({"name": "EngineW", "engine_path": launcher_w.as_posix()}), encoding="utf-8")

    run_dir = tmp_path / "run"
    config_path = tmp_path / "tournament.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "experiment_name": "native-book-usi-line-smoke",
                "output_dir": tmp_path.as_posix(),
                "engines": [
                    {
                        "name": "EngineB",
                        "engine_path": eng_b_yaml.as_posix(),
                        "options": {
                            "BookFile": book_path.as_posix(),
                            "BookEnableFlip": True,
                            "BookIgnorePly": True,
                        },
                    },
                    {
                        "name": "EngineW",
                        "engine_path": eng_w_yaml.as_posix(),
                        "options": {
                            "BookFile": book_path.as_posix(),
                            "BookEnableFlip": False,
                            "BookIgnorePly": True,
                        },
                    },
                ],
                "tournament": {
                    "scheduler": "round_robin",
                    "games_per_pair": 1,
                    "num_parallel": 1,
                    "seed": 1,
                    "engine_lifecycle": "per_game",
                },
                "rules": {
                    "initial_positions": {
                        "type": "file",
                        "source_format": "usi_line",
                        "source": lines_path.as_posix(),
                        "flip_policy": "none",
                    },
                    "repetition_occurrences_to_draw": 4,
                    "time_control": {"time_ms": 5000, "increment_ms": 100, "expiry_margin_ms": 500},
                },
                "dashboard": {"enabled": False},
                "system": {"path_preflight": "error", "resource_capacity_preflight": "off"},
            }
        ),
        encoding="utf-8",
    )

    main(["run", "tournament", str(config_path), "--run-dir", str(run_dir)])

    black_commands = pos_log_b.read_text(encoding="utf-8").splitlines()
    white_commands = pos_log_w.read_text(encoding="utf-8").splitlines()
    for commands in (black_commands, white_commands):
        new_game = commands.index("usinewgame")
        first_go = next(i for i, cmd in enumerate(commands) if cmd.startswith("go "))
        assert "isready" in commands[new_game + 2 : first_go]
        assert "binc 100" in commands[first_go]
        assert "winc 100" in commands[first_go]
        assert any(cmd.startswith("setoption name MaxGameMoves value ") for cmd in commands)
    black_positions = [cmd for cmd in black_commands if cmd.startswith("position ")]
    white_positions = [cmd for cmd in white_commands if cmd.startswith("position ")]
    assert black_positions[0] == "position startpos moves 5i5h 5a5b 5h5i 5b5a"
    assert white_positions[0] == "position startpos moves 5i5h 5a5b 5h5i 5b5a"
    assert "position startpos moves 5i5h 5a5b 5h5i 5b5a 5i5h" in white_positions

    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    engine_b_manifest = next(item for item in manifest["engines"] if item["name"] == "EngineB")
    assert engine_b_manifest["effective_options"]["BookEnableFlip"] is True
    assert engine_b_manifest["effective_options"]["BookIgnorePly"] is True

    with sqlite3.connect(run_dir / "game.db") as conn:
        init_sfen, game_result, num_moves, attrs_raw = conn.execute(
            "SELECT initial_position_sfen, game_result, num_moves, metadata_attributes_json FROM game"
        ).fetchone()

    attrs = json.loads(attrs_raw)
    assert attrs["black_time_control_spec"].startswith("t5000+i100;")
    assert attrs["white_time_control_spec"].startswith("t5000+i100;")
    with ArenaDBAdapter(SQLiteShogiDBFactory(run_dir / "game.db")) as adapter:
        record = adapter.load_record(game_id=1)
    assert record is not None
    detail = build_game_detail_payload(record=record, game_id="1", logger=logging.getLogger(__name__))
    assert detail["time_control_black"] == attrs["black_time_control_spec"]
    assert detail["time_control_white"] == attrs["white_time_control_spec"]
    participation = json.loads(attrs["_arena_participation"])
    book_prov = participation[0]["extra"]["book"]
    assert book_prov["resolved_path"].endswith("StandardBook.book")
    assert book_prov["fingerprint"]["partial_sha256"]
    assert book_prov["options"]["BookEnableFlip"] is True
    assert book_prov["options"]["BookIgnorePly"] is True

    schedule_meta = json.loads(attrs["_arena_schedule"])
    assert schedule_meta["opening_base_sfen"] == "startpos"
    assert schedule_meta["opening_moves_usi"] == ["5i5h", "5a5b", "5h5i", "5b5a"]

    # Post-opening canonical SFEN is persisted in game.db, and 4-fold repetition
    # triggers on game ply 8 because the 2 prior occurrences from usi_line are preserved.
    assert init_sfen == "lnsgkgsnl/1r5b1/ppppppppp/9/9/9/PPPPPPPPP/1B5R1/LNSGKGSNL b - 5"
    assert num_moves == 8
    assert game_result == "DRAW_BY_REPETITION"

    # Corrupt .book is rejected by production CLI preflight when path_preflight="error".
    corrupt_book = tmp_path / "Corrupt.book"
    corrupt_book.write_bytes(b"not-a-valid-native-book")
    bad_config = tmp_path / "bad_tournament.yaml"
    bad_payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    bad_payload["engines"][0]["options"]["BookFile"] = corrupt_book.as_posix()
    bad_config.write_text(yaml.safe_dump(bad_payload), encoding="utf-8")

    with pytest.raises(SystemExit) as exc_info:
        main(["run", "tournament", str(bad_config), "--dry-run"])
    assert exc_info.value.code == 1


@pytest.mark.parametrize(
    ("replies", "position", "expected"),
    [
        (("bestmove resign",), "startpos", "WHITE_WIN"),
        (("info string error search worker failed", "bestmove resign"), "startpos", "ERROR"),
        (("bestmove",), "startpos", "ERROR"),
        (("bestmove 7g7e",), "startpos", "ERROR"),
        (("bestmove win",), "startpos", "ERROR"),
        (("bestmove win",), "sfen 4K4/4S4/PPPPPPPPP/9/9/9/9/9/4k4 b 2RB3G 1", "BLACK_WIN_BY_DECLARATION"),
        (("bestmove win",), "sfen 4K4/9/9/9/9/9/ppppppppp/4s4/4k4 w 2rb2g 1", "WHITE_WIN_BY_DECLARATION"),
        (("bestmove win",), "sfen 4K4/4S4/PPPPPPPPP/9/9/9/9/9/4k4 b 2RB2G 1", "ERROR"),
    ],
)
def test_production_cli_persists_the_terminal_result(
    tmp_path: Path, replies: tuple[str, ...], position: str, expected: str
) -> None:
    engines = []
    for name in ("A", "B"):
        launcher = _write_launcher(tmp_path, name, tmp_path / f"{name}.log", replies=replies)
        engine_config = tmp_path / f"{name}.yaml"
        engine_config.write_text(
            yaml.safe_dump({"name": name, "engine_path": launcher.as_posix()}),
            encoding="utf-8",
        )
        engines.append({"name": name, "engine_path": engine_config.as_posix(), "options": {"BookFile": "<empty>"}})

    openings = tmp_path / "openings.txt"
    openings.write_text(position + "\n", encoding="utf-8")
    run_dir = tmp_path / "run"
    config_path = tmp_path / "tournament.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "experiment_name": "terminal-result-smoke",
                "output_dir": tmp_path.as_posix(),
                "engines": engines,
                "tournament": {"scheduler": "round_robin", "games_per_pair": 1, "num_parallel": 1},
                "rules": {
                    "initial_positions": {
                        "type": "file",
                        "source_format": "usi_line",
                        "source": openings.as_posix(),
                        "flip_policy": "none",
                    },
                    "time_control": {"time_ms": 5000},
                },
                "dashboard": {"enabled": False},
                "system": {"path_preflight": "error", "resource_capacity_preflight": "off"},
            }
        ),
        encoding="utf-8",
    )

    main(["run", "tournament", str(config_path), "--run-dir", str(run_dir)])
    with sqlite3.connect(run_dir / "game.db") as conn:
        assert conn.execute("SELECT game_result, num_moves FROM game").fetchall() == [(expected, 0)]
    for name in ("A", "B"):
        assert "setoption name BookFile value <empty>" in (tmp_path / f"{name}.log").read_text(encoding="utf-8")
