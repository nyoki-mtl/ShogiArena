from __future__ import annotations

import asyncio
import sqlite3
import stat
from pathlib import Path

import pytest
import rsshogi
from rsshogi.initial_positions import InitialPosition
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

from shogiarena._core.contexts.dashboard.adapters.result_summary_reader import SQLiteResultSummaryReader
from shogiarena._core.contexts.spsa.domain.participation_identity import (
    SpsaParticipationIdentity,
    attach_spsa_participation_identity,
)
from shogiarena._core.platform.db.store.arena_db_adapter import ArenaDBAdapter
from shogiarena._core.platform.db.store.entities import Base
from shogiarena._core.platform.db.store.record_store import DBRecordStore
from shogiarena._core.platform.db.store.repository_factory import SQLiteShogiDBFactory
from shogiarena._core.platform.db.store.schema_guard import (
    _TOLERATED_ADDITIVE_TABLES,
    CURRENT_STORE_SCHEMA_VERSION,
    StoreSchemaError,
)
from shogiarena._core.shared.kernel.game_results import GameResult
from shogiarena._core.shared.kernel.participation_records import (
    EngineArtifactSnapshot,
    GameParticipationRecord,
    InstanceSnapshot,
)
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


def test_spsa_record_and_participation_commit_atomically_and_are_readable(tmp_path: Path) -> None:
    adapter = ArenaDBAdapter(SQLiteShogiDBFactory(tmp_path / "game.db"))
    identity = SpsaParticipationIdentity(
        run_id="run-1",
        update_idx=1,
        pair_id="pair-1",
        attempt_id="attempt-1",
        observation_kind="SPSA",
    )
    participation = attach_spsa_participation_identity(
        (
            GameParticipationRecord(role="black", engine_name="black"),
            GameParticipationRecord(role="white", engine_name="white"),
        ),
        identity=identity,
    )

    game_db_id = adapter.append_record_with_participation(
        _make_record(game_name="spsa-game-1", game_type="spsa"),
        participation=participation,
    )

    assert adapter.get_game_id_by_name("spsa-game-1") == game_db_id
    records = adapter.get_spsa_game_database_records(run_id="run-1")
    assert len(records) == 1
    assert records[0].game_id == "spsa-game-1"
    assert records[0].participation_extras[0]["spsa_identity"] == identity.model_dump(mode="json")
    adapter.close()


def test_spsa_record_rolls_back_when_participation_is_invalid(tmp_path: Path) -> None:
    adapter = ArenaDBAdapter(SQLiteShogiDBFactory(tmp_path / "game.db"))

    with pytest.raises(TypeError, match="participation entries"):
        adapter.append_record_with_participation(
            _make_record(game_name="spsa-invalid", game_type="spsa"),
            participation=(
                GameParticipationRecord(role="black", engine_name="black"),
                {"role": "invalid"},
            ),
        )

    assert adapter.get_game_id_by_name("spsa-invalid") is None
    adapter.close()


def _create_db_without_tolerated_tables(db_path: Path, *, version: int | None = None) -> None:
    """許容対象の additive table を持たない、それ以外は canonical な DB を作る。

    その table を追加する前のソフトが生成した DB を再現する（task 0049）。
    """

    engine = create_engine(f"sqlite+pysqlite:///{db_path.as_posix()}")
    try:
        tables = [table for table in Base.metadata.sorted_tables if table.name not in _TOLERATED_ADDITIVE_TABLES]
        Base.metadata.create_all(engine, tables=tables)
    finally:
        engine.dispose()
    if version is not None:
        conn = sqlite3.connect(db_path)
        try:
            conn.execute(f"PRAGMA user_version={version}")
            conn.commit()
        finally:
            conn.close()


def _table_names(db_path: Path) -> set[str]:
    conn = sqlite3.connect(db_path)
    try:
        return {
            str(row[0])
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
            if not str(row[0]).startswith("sqlite_")
        }
    finally:
        conn.close()


def _schema_version(db_path: Path) -> int:
    conn = sqlite3.connect(db_path)
    try:
        return int(conn.execute("PRAGMA user_version").fetchone()[0])
    finally:
        conn.close()


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
    with pytest.raises(RuntimeError, match="partial legacy schema has missing managed tables") as exc_info:
        repo.create_tables()

    # 許容対象の additive table は「欠けていて当然」なので、欠落一覧に混ぜてはならない。
    for tolerated in _TOLERATED_ADDITIVE_TABLES:
        assert tolerated not in str(exc_info.value)
    assert _table_names(db_path) == {"player"}
    assert _schema_version(db_path) == 0


def test_repository_close_is_terminal_and_idempotent(tmp_path) -> None:
    """dispose 後に同じ repository が暗黙再接続しないこと。"""

    repo = SQLiteShogiDBFactory(tmp_path / "closed.sqlite3").create()
    repo.create_tables()

    repo.close_db()
    repo.close_db()

    with pytest.raises(RuntimeError, match="ShogiRepository is closed"):
        _ = repo.session
    with pytest.raises(RuntimeError, match="ShogiRepository is closed"):
        _ = repo.engine
    with pytest.raises(RuntimeError, match="ShogiRepository is closed"):
        repo.create_tables()
    with pytest.raises(RuntimeError, match="ShogiRepository is closed"):
        with repo.operation():
            pass


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


def test_arena_adapter_exposes_timeout_origin_with_games(tmp_path) -> None:
    db_path = tmp_path / "db.sqlite3"
    adapter = ArenaDBAdapter(SQLiteShogiDBFactory(db_path))
    adapter.ensure_schema()
    timed_out = _make_record(game_name="game-timeout")
    timed_out.set_metadata_attribute("timeout_origin", "orchestrator_stall")
    adapter.append_record_list([timed_out, _make_record(game_name="game-plain")])

    games = {str(game["game_name"]): game for game in adapter.get_games_with_players(game_type="arena")}

    assert games["game-timeout"].get("timeout_origin") == "orchestrator_stall"
    assert "timeout_origin" not in games["game-plain"]


def test_arena_adapter_degrades_when_timeout_attribution_table_is_absent(tmp_path) -> None:
    """table を持たない read-only DB でも、origin なしで一覧を読めること（task 0049）。"""

    db_path = tmp_path / "legacy-shape.sqlite3"
    _create_db_without_tolerated_tables(db_path, version=CURRENT_STORE_SCHEMA_VERSION)
    seed = SQLiteShogiDBFactory(db_path).create()
    DBRecordStore(seed).append([_make_record(game_name="game-legacy")])
    seed.close_db()
    db_path.chmod(stat.S_IREAD)
    try:
        games = ArenaDBAdapter(SQLiteShogiDBFactory(db_path)).get_games_with_players(game_type="arena")
    finally:
        db_path.chmod(stat.S_IWRITE | stat.S_IREAD)

    assert [game["game_name"] for game in games] == ["game-legacy"]
    assert "timeout_origin" not in games[0]


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


def _timeout_attribution_rows(db_path: Path) -> list[tuple[object, ...]]:
    conn = sqlite3.connect(db_path)
    try:
        return [
            tuple(row)
            for row in conn.execute(
                "SELECT g.game_name, a.origin FROM game_timeout_attribution a JOIN game g ON g.id = a.game_id"
            ).fetchall()
        ]
    finally:
        conn.close()


def test_db_record_store_projects_timeout_origin_and_keeps_it_in_metadata_blob(tmp_path) -> None:
    """timeout origin は集計用に列へ投影しつつ、blob 側にも残して roundtrip を保つこと（task 0049）。"""

    db_path = tmp_path / "db.sqlite3"
    repo = SQLiteShogiDBFactory(db_path).create()
    repo.create_tables()
    store = DBRecordStore(repo)
    record = _make_record(game_name="game-timeout")
    record.set_metadata_attribute("timeout_origin", "orchestrator_stall")

    store.append([record])

    assert _timeout_attribution_rows(db_path) == [("game-timeout", "orchestrator_stall")]
    loaded = store.load(game_name="game-timeout")
    assert loaded is not None
    assert loaded.metadata.attributes["timeout_origin"] == "orchestrator_stall"


def test_db_record_store_omits_timeout_attribution_row_without_origin(tmp_path) -> None:
    db_path = tmp_path / "db.sqlite3"
    repo = SQLiteShogiDBFactory(db_path).create()
    repo.create_tables()

    DBRecordStore(repo).append([_make_record(game_name="game-plain")])

    assert _timeout_attribution_rows(db_path) == []


def test_db_record_store_update_replaces_timeout_attribution_row(tmp_path) -> None:
    """更新時に古い投影が残らないこと（game 行の削除に追随する）。"""

    db_path = tmp_path / "db.sqlite3"
    repo = SQLiteShogiDBFactory(db_path).create()
    repo.create_tables()
    store = DBRecordStore(repo)
    first = _make_record(game_name="game-timeout-update")
    first.set_metadata_attribute("timeout_origin", "orchestrator_stall")
    store.append([first])

    payload = _make_record(game_name="game-timeout-update").to_dict()
    payload["metadata"]["updated_date"] = "2026-01-01T00:02:00"
    payload["metadata"]["attributes"]["updated_date"] = "2026-01-01T00:02:00"
    payload["metadata"]["attributes"]["timeout_origin"] = "engine_deadline"
    store.append([rsshogi.record.Record.from_dict(payload, strict=True)], should_update=True)

    assert _timeout_attribution_rows(db_path) == [("game-timeout-update", "engine_deadline")]


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


def test_write_path_backfills_missing_tolerated_table_and_keeps_data(tmp_path) -> None:
    """許容対象 table を持たない現行版 DB は、書き込み経路で補完されデータが保全されること。"""

    db_path = tmp_path / "backfill.sqlite3"
    _create_db_without_tolerated_tables(db_path, version=CURRENT_STORE_SCHEMA_VERSION)
    seed = SQLiteShogiDBFactory(db_path).create()
    DBRecordStore(seed).append([_make_record(game_name="pre-existing")])
    seed.close_db()

    repo = SQLiteShogiDBFactory(db_path).create()
    repo.create_tables()

    assert _TOLERATED_ADDITIVE_TABLES <= _table_names(db_path)
    assert _schema_version(db_path) == CURRENT_STORE_SCHEMA_VERSION
    assert DBRecordStore(repo).load(game_name="pre-existing") is not None


def test_write_path_accepts_read_only_database_without_tolerated_table(tmp_path) -> None:
    """read-only 媒体では、許容対象 table を作れなくても書き込み経路が成功すること。

    dashboard は archived run も `create_tables()` 経由で開くため、ここで失敗すると閲覧できなくなる。
    """

    db_path = tmp_path / "read-only-current.sqlite3"
    _create_db_without_tolerated_tables(db_path, version=CURRENT_STORE_SCHEMA_VERSION)
    db_path.chmod(stat.S_IREAD)
    try:
        repo = SQLiteShogiDBFactory(db_path).create()
        repo.create_tables()
        games = repo.session.execute(text("SELECT COUNT(*) FROM game")).scalar_one()
    finally:
        db_path.chmod(stat.S_IWRITE | stat.S_IREAD)

    assert games == 0
    assert not (_TOLERATED_ADDITIVE_TABLES & _table_names(db_path))


def test_query_path_reads_database_without_tolerated_table_and_does_not_create_it(tmp_path) -> None:
    """読み取り経路は許容対象 table を作らず、欠落したまま読めること。"""

    db_path = tmp_path / "read-current.sqlite3"
    _create_db_without_tolerated_tables(db_path, version=CURRENT_STORE_SCHEMA_VERSION)

    assert list(SQLiteResultSummaryReader(db_path).read_games()) == []
    assert not (_TOLERATED_ADDITIVE_TABLES & _table_names(db_path))


def test_offline_reader_does_not_stamp_unversioned_database_without_tolerated_table(tmp_path) -> None:
    """offline results reader は、書き込み可能な場所でも archived DB を刻印しない。"""

    db_path = tmp_path / "legacy-shape.sqlite3"
    _create_db_without_tolerated_tables(db_path)

    assert list(SQLiteResultSummaryReader(db_path).read_games()) == []
    assert _schema_version(db_path) == 0
    assert not (_TOLERATED_ADDITIVE_TABLES & _table_names(db_path))


def test_unmanaged_extra_table_is_accepted_on_both_paths(tmp_path) -> None:
    """管理外の余分な table があっても両経路で受理されること。

    「旧ソフトが新 DB を開ける」という additive table 方式の前提そのものを固定する。
    """

    db_path = tmp_path / "extra-table.sqlite3"
    _create_unversioned_canonical_db(db_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("CREATE TABLE future_feature (id INTEGER PRIMARY KEY AUTOINCREMENT, payload TEXT)")
        conn.commit()
    finally:
        conn.close()

    repo = SQLiteShogiDBFactory(db_path).create()
    repo.create_tables()

    assert list(SQLiteResultSummaryReader(db_path).read_games()) == []
    assert "future_feature" in _table_names(db_path)


def test_malformed_tolerated_table_is_rejected_on_both_paths(tmp_path) -> None:
    """許容対象 table が存在する場合は、形状不正を fail closed で拒否すること。"""

    db_path = tmp_path / "malformed-tolerated.sqlite3"
    _create_unversioned_canonical_db(db_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("DROP INDEX game_timeout_attribution_game_idx")
        conn.commit()
    finally:
        conn.close()

    repo = SQLiteShogiDBFactory(db_path).create()
    with pytest.raises(StoreSchemaError, match="missing or incompatible index: game_timeout_attribution_game_idx"):
        repo.create_tables()
    with pytest.raises(StoreSchemaError, match="missing or incompatible index: game_timeout_attribution_game_idx"):
        SQLiteResultSummaryReader(db_path).read_games()


def test_concurrent_tolerated_table_creation_revalidates(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """別プロセスが先に table を作った競合下でも、再検証されて成功すること。"""

    db_path = tmp_path / "concurrent.sqlite3"
    _create_db_without_tolerated_tables(db_path, version=CURRENT_STORE_SCHEMA_VERSION)
    original_create_all = Base.metadata.create_all

    def create_then_conflict(bind, **kwargs) -> None:
        # 「有無の確認と CREATE の間に別プロセスが作り終えた」状況を決定的に再現する。
        original_create_all(bind, **kwargs)
        raise OperationalError("CREATE TABLE", {}, Exception("table game_timeout_attribution already exists"))

    monkeypatch.setattr(Base.metadata, "create_all", create_then_conflict)
    repo = SQLiteShogiDBFactory(db_path).create()
    repo.create_tables()
    monkeypatch.undo()

    assert _TOLERATED_ADDITIVE_TABLES <= _table_names(db_path)
    assert _schema_version(db_path) == CURRENT_STORE_SCHEMA_VERSION


def test_concurrent_legacy_schema_creation_revalidates(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """未 version DB の初期化でも、競合時に再検証されて成功すること。"""

    db_path = tmp_path / "concurrent-legacy.sqlite3"
    original_create_all = Base.metadata.create_all

    def create_then_conflict(bind, **kwargs) -> None:
        original_create_all(bind, **kwargs)
        raise OperationalError("CREATE TABLE", {}, Exception("table game already exists"))

    monkeypatch.setattr(Base.metadata, "create_all", create_then_conflict)
    repo = SQLiteShogiDBFactory(db_path).create()
    repo.create_tables()
    monkeypatch.undo()

    assert _TOLERATED_ADDITIVE_TABLES <= _table_names(db_path)
    assert _schema_version(db_path) == CURRENT_STORE_SCHEMA_VERSION


def test_create_tables_rejects_database_with_only_a_tolerated_table(tmp_path) -> None:
    """許容対象 table しか持たない DB は partial legacy として拒否されること。"""

    db_path = tmp_path / "tolerated-only.sqlite3"
    engine = create_engine(f"sqlite+pysqlite:///{db_path.as_posix()}")
    try:
        Base.metadata.create_all(
            engine,
            tables=[table for table in Base.metadata.sorted_tables if table.name in _TOLERATED_ADDITIVE_TABLES],
        )
    finally:
        engine.dispose()

    repo = SQLiteShogiDBFactory(db_path).create()
    with pytest.raises(StoreSchemaError, match="partial legacy schema has missing managed tables"):
        repo.create_tables()


def test_write_path_accepts_read_only_unversioned_canonical_database(tmp_path) -> None:
    """canonical だが read-only な未 version DB を、書き込み経路が未刻印のまま受理すること。

    dashboard は archived run も `create_tables()` 経由で開くため、ここで失敗すると閲覧できなくなる。
    """

    db_path = tmp_path / "read-only-canonical.sqlite3"
    _create_unversioned_canonical_db(db_path)
    db_path.chmod(stat.S_IREAD)
    try:
        repo = SQLiteShogiDBFactory(db_path).create()
        repo.create_tables()
        games = repo.session.execute(text("SELECT COUNT(*) FROM game")).scalar_one()
    finally:
        db_path.chmod(stat.S_IWRITE | stat.S_IREAD)

    assert games == 0
    assert _schema_version(db_path) == 0


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


# --- Additive table の error 分類（task 0052 / review finding M1） -----------------


class _FakeSqliteError(Exception):
    """``sqlite_errorname`` を持つ driver 例外の代役。"""

    def __init__(self, error_name: str, error_code: int = 1) -> None:
        super().__init__(error_name)
        self.sqlite_errorname = error_name
        self.sqlite_errorcode = error_code


def _operational_error(error_name: str | None) -> OperationalError:
    orig: Exception = (
        Exception("driver without sqlite error codes") if error_name is None else _FakeSqliteError(error_name)
    )
    return OperationalError("CREATE TABLE", {}, orig)


@pytest.mark.parametrize(
    "error_name",
    [
        "SQLITE_BUSY",
        "SQLITE_LOCKED",
        "SQLITE_IOERR",
        "SQLITE_IOERR_WRITE",
        "SQLITE_FULL",
        "SQLITE_CORRUPT",
        "SQLITE_ERROR",
        None,  # driver が error code を公開しない場合は分類不能として fail closed
    ],
)
def test_writable_backfill_failure_is_fail_closed(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    error_name: str | None,
) -> None:
    """read-only 以外の理由で additive table を作れなかった DB を成功扱いにしないこと。"""

    db_path = tmp_path / f"backfill-{error_name or 'unclassified'}.sqlite3"
    _create_db_without_tolerated_tables(db_path, version=CURRENT_STORE_SCHEMA_VERSION)

    def failing_create_all(bind, **kwargs) -> None:
        del bind, kwargs
        raise _operational_error(error_name)

    monkeypatch.setattr(Base.metadata, "create_all", failing_create_all)
    repo = SQLiteShogiDBFactory(db_path).create()
    with pytest.raises(StoreSchemaError, match="could not be created"):
        repo.create_tables()
    monkeypatch.undo()

    assert not (_TOLERATED_ADDITIVE_TABLES & _table_names(db_path))


def test_readonly_backfill_failure_still_degrades(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``SQLITE_READONLY`` 系だけは欠落のまま閲覧を許すこと。"""

    db_path = tmp_path / "backfill-readonly.sqlite3"
    _create_db_without_tolerated_tables(db_path, version=CURRENT_STORE_SCHEMA_VERSION)

    def failing_create_all(bind, **kwargs) -> None:
        del bind, kwargs
        raise _operational_error("SQLITE_READONLY_DBMOVED")

    monkeypatch.setattr(Base.metadata, "create_all", failing_create_all)
    repo = SQLiteShogiDBFactory(db_path).create()
    repo.create_tables()
    monkeypatch.undo()

    assert not (_TOLERATED_ADDITIVE_TABLES & _table_names(db_path))


def test_legacy_initialization_failure_is_fail_closed(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """未 version DB の初期化でも、busy などで additive table が欠けたまま成功させないこと。"""

    db_path = tmp_path / "legacy-busy.sqlite3"
    _create_db_without_tolerated_tables(db_path)
    original_create_all = Base.metadata.create_all

    def partial_create_all(bind, **kwargs) -> None:
        del bind, kwargs
        raise _operational_error("SQLITE_BUSY")

    monkeypatch.setattr(Base.metadata, "create_all", partial_create_all)
    repo = SQLiteShogiDBFactory(db_path).create()
    with pytest.raises(StoreSchemaError, match="SQLITE_BUSY"):
        repo.create_tables()
    monkeypatch.setattr(Base.metadata, "create_all", original_create_all)

    assert _schema_version(db_path) == 0


def test_schema_version_stamp_failure_is_fail_closed(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """version 刻印の失敗も同じ分類器を使い、busy を未刻印成功にしないこと。"""

    from shogiarena._core.platform.db.store import schema_guard

    db_path = tmp_path / "stamp-busy.sqlite3"
    _create_unversioned_canonical_db(db_path)

    def failing_stamp(engine) -> None:
        del engine
        raise _operational_error("SQLITE_BUSY")

    monkeypatch.setattr(schema_guard, "_write_schema_version", failing_stamp)
    repo = SQLiteShogiDBFactory(db_path).create()
    with pytest.raises(StoreSchemaError, match="schema version could not be stamped"):
        repo.create_tables()
    monkeypatch.undo()


def test_update_on_a_database_without_the_attribution_table_does_not_touch_it(tmp_path) -> None:
    """table を持たない旧 DB で ``should_update=True`` を実行しても、存在しない table を参照しないこと。"""

    db_path = tmp_path / "update-without-tolerated.sqlite3"
    _create_db_without_tolerated_tables(db_path, version=CURRENT_STORE_SCHEMA_VERSION)
    repo = SQLiteShogiDBFactory(db_path).create()
    store = DBRecordStore(repo)

    first = _make_record(game_name="g-update")
    first.set_metadata_attribute("timeout_origin", "engine_deadline")
    store.append([first], should_update=True)

    second = _make_record(game_name="g-update")
    second.set_metadata_attribute("timeout_origin", "orchestrator_stall")
    second.update_metadata({"updated_date": "2026-01-02T00:00:00"})
    store.append([second], should_update=True)

    assert not (_TOLERATED_ADDITIVE_TABLES & _table_names(db_path))
    loaded = store.load(game_name="g-update")
    assert loaded is not None
    # 列へ投影できなくても、origin は metadata_attributes_json 側に保たれる。
    assert loaded.metadata.attributes.get("timeout_origin") == "orchestrator_stall"
