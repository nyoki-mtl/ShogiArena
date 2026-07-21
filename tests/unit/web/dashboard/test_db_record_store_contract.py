from __future__ import annotations

import asyncio
import sqlite3
import stat
from pathlib import Path

import pytest
import rsshogi
from rsshogi.initial_positions import InitialPosition
from sqlalchemy import create_engine, text

from shogiarena._core.contexts.dashboard.adapters.result_summary_reader import SQLiteResultSummaryReader
from shogiarena._core.platform.db.store.arena_db_adapter import ArenaDBAdapter
from shogiarena._core.platform.db.store.entities import Base
from shogiarena._core.platform.db.store.record_store import DBRecordStore
from shogiarena._core.platform.db.store.repository_factory import SQLiteShogiDBFactory
from shogiarena._core.platform.db.store.schema_guard import CURRENT_STORE_SCHEMA_VERSION, StoreSchemaError
from shogiarena._core.shared.kernel.game_results import GameResult
from shogiarena._core.shared.kernel.participation_records import EngineArtifactSnapshot, InstanceSnapshot
from shogiarena._core.shared.kernel.schedule_metadata import extract_schedule_metadata, serialize_schedule_metadata


def _make_record(
    *,
    game_name: str = "game-db",
    game_type: str = "arena",
    black_player: str = "black",
    white_player: str = "white",
    time_control_black: str = "900+60+0",
    time_control_white: str = "900+60+0",
    end_time_ms: int | None = 123,
    end_comment: str | None = "done",
) -> rsshogi.record.Record:
    result_payload: dict[str, object] = {"result": GameResult.BLACK_WIN.name, "ply_count": 1}
    if end_time_ms is not None:
        result_payload["end_time_ms"] = end_time_ms
    if end_comment is not None:
        result_payload["end_comment"] = end_comment
    return rsshogi.record.Record.from_dict(
        {
            "metadata": {
                "black_player": black_player,
                "white_player": white_player,
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


def _create_unversioned_canonical_db(db_path: Path) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{db_path.as_posix()}")
    try:
        Base.metadata.create_all(engine)
    finally:
        engine.dispose()


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


def test_sqlite_factory_enables_foreign_keys_for_sessions(tmp_path) -> None:
    repo = SQLiteShogiDBFactory(tmp_path / "db.sqlite3").create()
    repo.create_tables()

    foreign_keys_enabled = repo.session.execute(text("PRAGMA foreign_keys")).scalar_one()

    assert foreign_keys_enabled == 1


def test_sqlite_factory_sets_wal_and_busy_timeout(tmp_path) -> None:
    repo = SQLiteShogiDBFactory(tmp_path / "db.sqlite3").create()
    repo.create_tables()

    journal_mode = repo.session.execute(text("PRAGMA journal_mode")).scalar_one()
    busy_timeout = repo.session.execute(text("PRAGMA busy_timeout")).scalar_one()

    assert str(journal_mode).lower() == "wal"
    assert busy_timeout >= 5000


def test_create_tables_stamps_new_database_schema_version(tmp_path) -> None:
    db_path = tmp_path / "db.sqlite3"
    repo = SQLiteShogiDBFactory(db_path).create()

    repo.create_tables()

    conn = sqlite3.connect(db_path)
    try:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()
    assert version == CURRENT_STORE_SCHEMA_VERSION


def test_query_stamps_only_fully_compatible_unversioned_database(tmp_path) -> None:
    db_path = tmp_path / "legacy.sqlite3"
    _create_unversioned_canonical_db(db_path)
    repo = SQLiteShogiDBFactory(db_path).create()

    assert repo.session.execute(text("SELECT COUNT(*) FROM game")).scalar_one() == 0

    conn = sqlite3.connect(db_path)
    try:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()
    assert version == CURRENT_STORE_SCHEMA_VERSION


def test_offline_reader_rejects_unversioned_database_with_missing_index(tmp_path) -> None:
    db_path = tmp_path / "missing-index.sqlite3"
    _create_unversioned_canonical_db(db_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("DROP INDEX game_name_index")
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(RuntimeError, match="missing or incompatible index: game_name_index"):
        SQLiteResultSummaryReader(db_path).read_games()

    conn = sqlite3.connect(db_path)
    try:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()
    assert version == 0


def test_create_tables_does_not_recreate_and_stamp_missing_legacy_index(tmp_path) -> None:
    db_path = tmp_path / "missing-index.sqlite3"
    _create_unversioned_canonical_db(db_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("DROP INDEX game_id_index")
        conn.commit()
    finally:
        conn.close()

    repo = SQLiteShogiDBFactory(db_path).create()
    with pytest.raises(RuntimeError, match="missing or incompatible index: game_id_index"):
        repo.create_tables()

    conn = sqlite3.connect(db_path)
    try:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()
    assert version == 0


def test_create_tables_rejects_partial_legacy_schema_before_filling_missing_tables(tmp_path) -> None:
    db_path = tmp_path / "partial.sqlite3"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "CREATE TABLE player (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "game_type VARCHAR(16) NOT NULL, player_name VARCHAR(64) NOT NULL)"
        )
        conn.commit()
    finally:
        conn.close()

    repo = SQLiteShogiDBFactory(db_path).create()
    with pytest.raises(RuntimeError, match="partial legacy schema has missing managed tables"):
        repo.create_tables()

    conn = sqlite3.connect(db_path)
    try:
        tables = {
            str(row[0])
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
            if not str(row[0]).startswith("sqlite_")
        }
        version = conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()
    assert tables == {"player"}
    assert version == 0


@pytest.mark.asyncio
async def test_sqlite_factory_scopes_sessions_by_asyncio_task(tmp_path) -> None:
    repo = SQLiteShogiDBFactory(tmp_path / "db.sqlite3").create()
    repo.create_tables()
    task_sessions: list[object] = []
    both_sessions_ready = asyncio.Event()

    async def collect_session_id() -> int:
        session = repo.session
        task_sessions.append(session)
        if len(task_sessions) == 2:
            both_sessions_ready.set()
        await both_sessions_ready.wait()
        try:
            return id(session)
        finally:
            repo.close_db()

    left_session_id, right_session_id = await asyncio.gather(collect_session_id(), collect_session_id())
    repo.close_db()

    assert left_session_id != right_session_id


@pytest.mark.asyncio
async def test_record_store_failure_rolls_back_and_removes_worker_task_session(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed worker operation must not contaminate the next DB operation."""

    db_path = tmp_path / "db.sqlite3"
    factory = SQLiteShogiDBFactory(db_path)
    repo = factory.create()
    adapter = ArenaDBAdapter(factory)
    adapter.ensure_schema()
    failed_record = _make_record(
        game_name="failed-game",
        black_player="orphan-black",
        white_player="orphan-white",
    )

    from shogiarena._core.platform.db.store import record_store as record_store_module

    original_push = record_store_module._push_record_move

    def reject_move(*_args: object) -> object:
        raise ValueError("simulated legal-move persistence failure")

    async def fail_in_worker() -> tuple[object, object]:
        scoped_session = repo.session
        monkeypatch.setattr(record_store_module, "_push_record_move", reject_move)
        with pytest.raises(ValueError, match="legal move payload"):
            adapter.append_record_list([failed_record])
        monkeypatch.setattr(record_store_module, "_push_record_move", original_push)
        replacement_session = repo.session
        repo.close_db()
        return scoped_session, replacement_session

    failed_session, replacement_session = await asyncio.create_task(fail_in_worker())
    assert replacement_session is not failed_session

    async def persist_success() -> None:
        adapter.append_record_list([_make_record(game_name="successful-game")])

    await asyncio.create_task(persist_success())

    conn = sqlite3.connect(db_path)
    try:
        players = {row[0] for row in conn.execute("SELECT player_name FROM player").fetchall()}
        games = {row[0] for row in conn.execute("SELECT game_name FROM game").fetchall()}
    finally:
        conn.close()

    assert players == {"black", "white"}
    assert games == {"successful-game"}


def test_arena_adapter_upserts_return_readable_detached_entities(tmp_path) -> None:
    adapter = ArenaDBAdapter(SQLiteShogiDBFactory(tmp_path / "db.sqlite3"))

    artifact = adapter.upsert_engine_artifact(EngineArtifactSnapshot(logical_name="engine-a", artifact="v1"))
    instance = adapter.upsert_instance_spec(InstanceSnapshot(instance_id="local", display_name="Local"))

    assert artifact is not None
    assert artifact.logical_name == "engine-a"
    assert instance is not None
    assert instance.instance_id == "local"


def test_db_record_store_update_deletes_stale_move_rows(tmp_path) -> None:
    db_path = tmp_path / "db.sqlite3"
    repo = SQLiteShogiDBFactory(db_path).create()
    repo.create_tables()
    store = DBRecordStore(repo)
    first_record = _make_record(game_name="game-update")
    second_payload = _make_record(game_name="game-update").to_dict()
    second_payload["metadata"]["updated_date"] = "2026-01-01T00:02:00"
    second_payload["metadata"]["attributes"]["updated_date"] = "2026-01-01T00:02:00"
    second_payload["moves"][0]["move"] = "2g2f"
    second_payload["moves"][0]["time_ms"] = 456
    second_record = rsshogi.record.Record.from_dict(second_payload, strict=True)

    store.append([first_record])
    store.append([second_record], should_update=True)

    conn = sqlite3.connect(db_path)
    try:
        stored_moves = conn.execute("SELECT next_move_time_ms FROM game_move ORDER BY id").fetchall()
    finally:
        conn.close()
    loaded = store.load(game_name="game-update")
    assert loaded is not None
    assert len(stored_moves) == 2
    assert [row[0] for row in stored_moves] == [456, 123]
    assert loaded.moves[0].time_ms == 456


def test_db_record_store_roundtrips_arena_schedule_metadata(tmp_path) -> None:
    db_path = tmp_path / "db.sqlite3"
    repo = SQLiteShogiDBFactory(db_path).create()
    repo.create_tables()
    store = DBRecordStore(repo)
    record = _make_record(game_name="game-schedule")
    schedule_metadata = {
        "schema_version": 1,
        "display_order": 3,
        "round_num": 2,
        "pair_key": "black|white|slot-1",
        "pair_slot": 1,
        "matchup_key": "black|white",
        "initial_sfen": InitialPosition.STANDARD.value,
    }
    record.set_metadata_attribute("_arena_schedule", serialize_schedule_metadata(schedule_metadata))

    store.append([record])
    loaded = store.load(game_name="game-schedule")

    assert loaded is not None
    extracted = extract_schedule_metadata(loaded)
    assert extracted is not None
    assert extracted["display_order"] == 3
    assert extracted["pair_key"] == "black|white|slot-1"
    conn = sqlite3.connect(db_path)
    try:
        raw_attrs = conn.execute("SELECT metadata_attributes_json FROM game WHERE game_name = ?", ("game-schedule",))
        stored = raw_attrs.fetchone()[0]
    finally:
        conn.close()
    assert "_arena_schedule" in stored
    assert '"game_name"' not in stored
    assert '"updated_date"' not in stored


def test_db_record_store_append_requires_game_name_and_type_in_attributes(tmp_path) -> None:
    repo = SQLiteShogiDBFactory(tmp_path / "db.sqlite3").create()
    repo.create_tables()
    store = DBRecordStore(repo)
    invalid = rsshogi.record.Record.from_dict(
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
    engine_info = rsshogi.record.EngineInfo(
        eval=15,
        wall_time_ms=130,
        latency_delta_ms=7,
        extras={"engine_wall_time_ms": 121, "move_source": "book", "book_hit": True, "probe": "keep"},
    )
    record = rsshogi.record.Record.from_dict(
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
    assert engine_info.extras["move_source"] == "book"
    assert engine_info.extras["book_hit"] == 1


def test_db_record_store_preserves_unknown_book_hit_as_unmeasured(tmp_path) -> None:
    repo = SQLiteShogiDBFactory(tmp_path / "db.sqlite3").create()
    repo.create_tables()
    store = DBRecordStore(repo)
    record = _make_record(game_name="game-unknown-book-hit")
    payload = record.to_dict()
    payload["moves"][0]["engine_info"]["extras"]["book_hit"] = "unknown"
    record_with_unknown_book_hit = rsshogi.record.Record.from_dict(payload, strict=True)

    store.append([record_with_unknown_book_hit])
    loaded = store.load(game_name="game-unknown-book-hit")

    assert loaded is not None
    assert "book_hit" not in loaded.moves[0].engine_info.extras


def test_db_record_store_persists_engine_wall_time_from_rebuilt_record(tmp_path) -> None:
    repo = SQLiteShogiDBFactory(tmp_path / "db.sqlite3").create()
    repo.create_tables()
    store = DBRecordStore(repo)
    record = _make_record(game_name="game-engine-wall")
    payload = record.to_dict()
    payload["moves"][0]["engine_info"]["extras"]["engine_wall_time_ms"] = 31
    record_with_engine_wall = rsshogi.record.Record.from_dict(payload, strict=True)

    store.append([record_with_engine_wall])
    loaded = store.load(game_name="game-engine-wall")

    assert loaded is not None
    assert loaded.moves[0].engine_info.extras["engine_wall_time_ms"] == 31


def test_create_tables_migrates_engine_wall_time_column(tmp_path) -> None:
    db_path = tmp_path / "compat.sqlite3"
    _create_unversioned_canonical_db(db_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("ALTER TABLE game_move DROP COLUMN engine_wall_time_ms")
        conn.execute("ALTER TABLE game_move DROP COLUMN move_source")
        conn.execute("ALTER TABLE game_move DROP COLUMN book_hit")
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
    assert "move_source" in columns
    assert "book_hit" in columns


def test_create_tables_migrates_game_metadata_attributes_column(tmp_path) -> None:
    db_path = tmp_path / "compat-game.sqlite3"
    _create_unversioned_canonical_db(db_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("ALTER TABLE game DROP COLUMN metadata_attributes_json")
        conn.commit()
    finally:
        conn.close()

    repo = SQLiteShogiDBFactory(db_path).create()
    repo.create_tables()

    conn = sqlite3.connect(db_path)
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(game)").fetchall()}
    finally:
        conn.close()
    assert "metadata_attributes_json" in columns


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
    with pytest.raises(RuntimeError, match="partial legacy schema has missing managed tables"):
        repo.create_tables()


def test_create_tables_rejects_newer_than_supported_schema_version(tmp_path) -> None:
    db_path = tmp_path / "future.sqlite3"
    _create_unversioned_canonical_db(db_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(f"PRAGMA user_version={CURRENT_STORE_SCHEMA_VERSION + 1}")
        conn.commit()
    finally:
        conn.close()

    repo = SQLiteShogiDBFactory(db_path).create()
    with pytest.raises(StoreSchemaError, match="schema version is 2, expected 1"):
        repo.create_tables()


def test_offline_reader_rejects_newer_than_supported_schema_version(tmp_path) -> None:
    db_path = tmp_path / "future-read.sqlite3"
    _create_unversioned_canonical_db(db_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(f"PRAGMA user_version={CURRENT_STORE_SCHEMA_VERSION + 1}")
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(StoreSchemaError, match="schema version is 2, expected 1"):
        SQLiteResultSummaryReader(db_path).read_games()


def test_corrupt_database_file_reports_repair_guidance(tmp_path) -> None:
    db_path = tmp_path / "corrupt.sqlite3"
    db_path.write_bytes(b"this is not a sqlite database" * 16)

    repo = SQLiteShogiDBFactory(db_path).create()
    with pytest.raises(StoreSchemaError, match="the database file could not be read"):
        repo.create_tables()


def test_query_path_reads_unversioned_database_on_read_only_media(tmp_path) -> None:
    """read-only な媒体の legacy DB は、version 刻印に失敗しても読み取れること。"""

    db_path = tmp_path / "read-only.sqlite3"
    _create_unversioned_canonical_db(db_path)
    db_path.chmod(stat.S_IREAD)
    try:
        games = SQLiteResultSummaryReader(db_path).read_games()
    finally:
        db_path.chmod(stat.S_IWRITE | stat.S_IREAD)

    assert list(games) == []

    conn = sqlite3.connect(db_path)
    try:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()
    assert version == 0
