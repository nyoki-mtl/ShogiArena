from __future__ import annotations

import pytest
import rshogi
from rshogi.initial_positions import InitialPosition

from shogiarena.db.factory import SQLiteShogiDBFactory
from shogiarena.records.storage.db_store import DBRecordStore
from shogiarena.utils.types.types import GameResult


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
        extras={"probe": "keep"},
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
