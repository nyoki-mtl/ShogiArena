from __future__ import annotations

import sqlite3

import pytest
import rshogi
from rshogi.initial_positions import InitialPosition

from shogiarena._core.platform.db.store.record_store import DBRecordStore
from shogiarena._core.platform.db.store.repository_factory import SQLiteShogiDBFactory
from shogiarena._core.shared.kernel.game_results import GameResult


def _make_record(
    *,
    game_name: str = "game-db",
    game_type: str = "arena",
    time_control_black: str = "900+60+0",
    time_control_white: str = "900+60+0",
    end_time_ms: int | None = 123,
    end_comment: str | None = "done",
) -> rshogi.record.GameRecord:
    result_payload: dict[str, object] = {"result": GameResult.BLACK_WIN.name, "ply_count": 1}
    if end_time_ms is not None:
        result_payload["end_time_ms"] = end_time_ms
    if end_comment is not None:
        result_payload["end_comment"] = end_comment
    return rshogi.record.GameRecord.from_dict(
        {
            "metadata": {
                "black_player": "black",
                "white_player": "white",
                "game_name": game_name,
                "game_type": game_type,
                "start_date": "2026-01-01T00:00:00",
                "end_date": "2026-01-01T00:01:00",
                "updated_date": "2026-01-01T00:01:00",
                "black_time_control": time_control_black,
                "white_time_control": time_control_white,
                "attributes": {
                    "game_name": game_name,
                    "game_type": game_type,
                    "updated_date": "2026-01-01T00:01:00",
                },
            },
            "init_position_sfen": InitialPosition.STANDARD.value,
            "moves": [{"move": "7g7f", "time_ms": 123, "engine_info": {"eval": 15}}],
            "result": result_payload,
        }
    )


def test_db_record_store_load_roundtrip_keeps_metadata_attributes_string_typed(tmp_path) -> None:
    repo = SQLiteShogiDBFactory(tmp_path / "db.sqlite3").create()
    repo.create_tables()
    store = DBRecordStore(repo)
    record = _make_record()

    store.append([record])
    loaded = store.load(game_name="game-db")

    assert loaded is not None
    attrs = loaded.metadata.attributes
    assert attrs["storage"] == "db"
    assert attrs["game_name"] == "game-db"
    assert attrs["game_type"] == "arena"
    assert attrs["updated_date"] == "2026-01-01T00:01:00"
    assert loaded.metadata.black_time_control is not None
    assert loaded.metadata.white_time_control is not None
    assert loaded.metadata.black_time_control.to_spec() == "900+60+0"
    assert loaded.metadata.white_time_control.to_spec() == "900+60+0"


def test_db_record_store_append_requires_game_name_and_type_in_attributes(tmp_path) -> None:
    repo = SQLiteShogiDBFactory(tmp_path / "db.sqlite3").create()
    repo.create_tables()
    store = DBRecordStore(repo)
    invalid = rshogi.record.GameRecord.from_dict(
        {
            "metadata": {
                "black_player": "black",
                "white_player": "white",
                "start_date": "2026-01-01T00:00:00",
                "end_date": "2026-01-01T00:01:00",
                "black_time_control": "900+60+0",
                "white_time_control": "900+60+0",
                "attributes": {"updated_date": "2026-01-01T00:01:00"},
            },
            "init_position_sfen": InitialPosition.STANDARD.value,
            "moves": [],
            "result": {"result": GameResult.PAUSED.name, "ply_count": 0},
        }
    )

    with pytest.raises(ValueError):
        store.append([invalid])


def test_db_record_store_append_reads_end_info_from_result_info(tmp_path) -> None:
    repo = SQLiteShogiDBFactory(tmp_path / "db.sqlite3").create()
    repo.create_tables()
    store = DBRecordStore(repo)
    record = _make_record(game_name="game-result", end_time_ms=456, end_comment="final")
    store.append([record])
    loaded = store.load(game_name="game-result")
    assert loaded is not None
    assert loaded.result_info.end_time_ms == 456
    assert loaded.result_info.end_comment == "final"


def test_db_record_store_append_prefers_engine_info_timing_fields(tmp_path) -> None:
    repo = SQLiteShogiDBFactory(tmp_path / "db.sqlite3").create()
    repo.create_tables()
    store = DBRecordStore(repo)
    engine_info = rshogi.record.MoveEngineInfo(
        eval=15,
        wall_time_ms=130,
        latency_delta_ms=7,
        extras={"engine_wall_time_ms": 121, "probe": "keep"},
    )
    record = rshogi.record.GameRecord.from_dict(
        {
            "metadata": {
                "black_player": "black",
                "white_player": "white",
                "game_name": "game-timing-fields",
                "game_type": "arena",
                "start_date": "2026-01-01T00:00:00",
                "end_date": "2026-01-01T00:01:00",
                "updated_date": "2026-01-01T00:01:00",
                "black_time_control": "900+60+0",
                "white_time_control": "900+60+0",
                "attributes": {
                    "game_name": "game-timing-fields",
                    "game_type": "arena",
                    "updated_date": "2026-01-01T00:01:00",
                },
            },
            "init_position_sfen": InitialPosition.STANDARD.value,
            "moves": [
                {
                    "move": "7g7f",
                    "time_ms": 123,
                    "engine_info": engine_info,
                }
            ],
            "result": {"result": GameResult.BLACK_WIN.name, "ply_count": 1},
        },
        strict=True,
    )

    store.append([record])
    loaded = store.load(game_name="game-timing-fields")
    assert loaded is not None
    assert loaded.moves
    engine_info = loaded.moves[0].engine_info
    assert engine_info is not None
    assert engine_info.wall_time_ms == 130
    assert engine_info.latency_delta_ms == 7
    assert engine_info.extras["engine_wall_time_ms"] == 121


def test_db_record_store_persists_engine_wall_time_from_rebuilt_record(tmp_path) -> None:
    repo = SQLiteShogiDBFactory(tmp_path / "db.sqlite3").create()
    repo.create_tables()
    store = DBRecordStore(repo)
    record = _make_record(game_name="game-engine-wall")
    payload = record.to_dict()
    payload["moves"][0]["engine_info"]["extras"]["engine_wall_time_ms"] = 31
    record_with_engine_wall = rshogi.record.GameRecord.from_dict(payload, strict=True)

    store.append([record_with_engine_wall])
    loaded = store.load(game_name="game-engine-wall")

    assert loaded is not None
    assert loaded.moves[0].engine_info.extras["engine_wall_time_ms"] == 31


def test_create_tables_migrates_engine_wall_time_column(tmp_path) -> None:
    db_path = tmp_path / "compat.sqlite3"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE game_move (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                game_id INTEGER NOT NULL,
                ply SMALLINT NOT NULL,
                next_move SMALLINT NOT NULL,
                next_move_time_ms INTEGER,
                wall_time_ms INTEGER,
                latency_delta_ms INTEGER,
                next_move_comment TEXT,
                eval SMALLINT,
                depth SMALLINT,
                seldepth SMALLINT,
                nodes INTEGER
            )
            """
        )
        conn.commit()
    finally:
        conn.close()

    repo = SQLiteShogiDBFactory(db_path).create()
    repo.create_tables()

    conn = sqlite3.connect(db_path)
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(game_move)").fetchall()}
    finally:
        conn.close()
    assert "engine_wall_time_ms" in columns


def test_create_tables_rejects_legacy_result_code_schema(tmp_path) -> None:
    db_path = tmp_path / "legacy.sqlite3"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE game (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                game_type VARCHAR(16) NOT NULL,
                game_name VARCHAR(128) NOT NULL,
                result_code SMALLINT NOT NULL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()

    repo = SQLiteShogiDBFactory(db_path).create()
    with pytest.raises(RuntimeError, match="Unsupported shogidb schema detected"):
        repo.create_tables()


def test_create_tables_rejects_incomplete_game_result_schema(tmp_path) -> None:
    db_path = tmp_path / "incomplete.sqlite3"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE game (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                game_type VARCHAR(16) NOT NULL,
                game_name VARCHAR(128) NOT NULL,
                game_result VARCHAR(64) NOT NULL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()

    repo = SQLiteShogiDBFactory(db_path).create()
    with pytest.raises(RuntimeError, match="missing columns"):
        repo.create_tables()
