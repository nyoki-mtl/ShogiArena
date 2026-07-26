"""Production-path DB compatibility evidence required for the 1.1.0 release."""

from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest
import rsshogi
from rsshogi.initial_positions import InitialPosition

from shogiarena._core.contexts.dashboard.adapters.result_summary_reader import SQLiteResultSummaryReader
from shogiarena._core.platform.db.store.arena_db_adapter import ArenaDBAdapter
from shogiarena._core.platform.db.store.repository_factory import (
    SQLiteShogiDBFactory,
    build_sqlite_read_only_uri,
)
from shogiarena._core.shared.kernel.game_results import GameResult

_RESULTS_CLI_SCRIPT = """\
import sys
from shogiarena.cli import main

main(["results", "summary", sys.argv[1], "--format", "json"])
"""

_DB_SERVICE_HOLDER_SCRIPT = """\
import sys
import time
from pathlib import Path

from shogiarena._core.platform.db.store.arena_db_adapter import ArenaDBAdapter
from shogiarena._core.platform.db.store.repository_factory import SQLiteShogiDBFactory

db_path = Path(sys.argv[1])
ready_path = Path(sys.argv[2])
release_path = Path(sys.argv[3])
adapter = ArenaDBAdapter(SQLiteShogiDBFactory(db_path))
try:
    adapter.ensure_schema()
    adapter.get_games_with_players(game_type="arena")
    ready_path.write_text("ready", encoding="utf-8")
    while not release_path.exists():
        time.sleep(0.01)
finally:
    adapter.close()
"""

_LIVE_WRITER_SCRIPT = """\
import sqlite3
import sys
import time
from pathlib import Path

db_path = Path(sys.argv[1])
ready_path = Path(sys.argv[2])
release_path = Path(sys.argv[3])
connection = sqlite3.connect(db_path, timeout=5.0, isolation_level=None)
try:
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA wal_autocheckpoint=0")
    black_id = connection.execute(
        "SELECT id FROM player WHERE player_name = 'live-black'"
    ).fetchone()[0]
    white_id = connection.execute(
        "SELECT id FROM player WHERE player_name = 'live-white'"
    ).fetchone()[0]
    counter = 0
    while not release_path.exists():
        counter += 1
        connection.execute("BEGIN IMMEDIATE")
        try:
            connection.execute(
                \"\"\"
                INSERT INTO game (
                    game_type,
                    game_name,
                    game_result,
                    num_moves,
                    black_player_id,
                    white_player_id,
                    initial_position_sfen,
                    updated_date
                ) VALUES ('arena', ?, 'BLACK_WIN', 1, ?, ?, 'startpos', ?)
                \"\"\",
                (f"live-{counter}", black_id, white_id, f"2026-01-01 00:00:{counter % 60:02d}"),
            )
            connection.execute("COMMIT")
        except BaseException:
            connection.execute("ROLLBACK")
            raise
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        ready_path.touch()
        time.sleep(0.01)
finally:
    connection.close()
"""


def _make_record() -> rsshogi.record.Record:
    return rsshogi.record.Record.from_dict(
        {
            "metadata": {
                "black_player": "legacy-black",
                "white_player": "legacy-white",
                "game_name": "legacy-game",
                "game_type": "arena",
                "start_date": "2026-01-01T00:00:00",
                "end_date": "2026-01-01T00:01:00",
                "updated_date": "2026-01-01T00:01:00",
                "black_time_control": "900+60+0",
                "white_time_control": "900+60+0",
                "attributes": {
                    "game_name": "legacy-game",
                    "game_type": "arena",
                    "updated_date": "2026-01-01T00:01:00",
                },
            },
            "init_position_sfen": InitialPosition.STANDARD.value,
            "moves": [{"move": "7g7f", "time_ms": 123}],
            "result": {
                "result": GameResult.BLACK_WIN.name,
                "ply_count": 1,
                "end_time_ms": 123,
                "end_comment": "done",
            },
        }
    )


def _run_results_cli(db_path: Path) -> dict[str, object]:
    result = subprocess.run(
        [sys.executable, "-c", _RESULTS_CLI_SCRIPT, str(db_path)],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    payload = json.loads(result.stdout)
    assert isinstance(payload, dict)
    return payload


def _wait_for_marker(process: subprocess.Popen[str], marker: Path, *, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if marker.exists():
            return
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            raise AssertionError(f"DB holder exited before opening the database: stdout={stdout!r} stderr={stderr!r}")
        time.sleep(0.01)
    raise AssertionError(f"DB holder did not open the database within {timeout} seconds")


def _build_legacy_wal_archive(root: Path) -> Path:
    """現行 service で seed し、旧版相当の additive-table なし WAL archive を作る。"""

    source_path = root / "source.db"
    adapter = ArenaDBAdapter(SQLiteShogiDBFactory(source_path))
    adapter.ensure_schema()
    adapter.append_record_list([_make_record()])
    adapter.close()

    connection = sqlite3.connect(source_path)
    try:
        journal_mode = connection.execute("PRAGMA journal_mode=WAL").fetchone()
        assert journal_mode is not None and str(journal_mode[0]).lower() == "wal"
        connection.execute("PRAGMA wal_autocheckpoint=0")
        connection.execute("DROP TABLE game_timeout_attribution")
        connection.execute("PRAGMA user_version=0")
        connection.execute("CREATE TABLE legacy_probe (id INTEGER PRIMARY KEY, payload TEXT NOT NULL)")
        connection.execute("INSERT INTO legacy_probe (payload) VALUES ('from-wal')")
        connection.commit()

        archive_dir = root / "archive"
        archive_dir.mkdir()
        archive_path = archive_dir / "game.db"
        for suffix in ("", "-wal", "-shm"):
            source_file = Path(f"{source_path}{suffix}")
            assert source_file.exists(), f"expected WAL archive component: {source_file}"
            shutil.copy2(source_file, Path(f"{archive_path}{suffix}"))
    finally:
        connection.close()
    (archive_dir / "completed.flag").touch()
    (archive_dir / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "status": "provenance_sealed",
                "shogiarena_version": "1.0.2",
            }
        ),
        encoding="utf-8",
    )
    return archive_path


def _build_current_wal_archive(root: Path) -> Path:
    """現行 schema と terminal commit を持つ WAL artifact を作る。"""

    source_path = root / "current-source.db"
    adapter = ArenaDBAdapter(SQLiteShogiDBFactory(source_path))
    adapter.ensure_schema()
    adapter.append_record_list([_make_record()])
    adapter.close()

    connection = sqlite3.connect(source_path)
    try:
        journal_mode = connection.execute("PRAGMA journal_mode=WAL").fetchone()
        assert journal_mode is not None and str(journal_mode[0]).lower() == "wal"
        connection.execute("PRAGMA wal_autocheckpoint=0")
        connection.execute("CREATE TABLE current_probe (id INTEGER PRIMARY KEY, payload TEXT NOT NULL)")
        connection.execute("INSERT INTO current_probe (payload) VALUES ('from-wal')")
        connection.commit()

        archive_dir = root / "current-archive"
        archive_dir.mkdir()
        archive_path = archive_dir / "game.db"
        for suffix in ("", "-wal", "-shm"):
            source_file = Path(f"{source_path}{suffix}")
            assert source_file.exists(), f"expected WAL artifact component: {source_file}"
            shutil.copy2(source_file, Path(f"{archive_path}{suffix}"))
    finally:
        connection.close()
    (archive_dir / "completion_status.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "status": "clean",
                "termination_reason": "schedule-complete",
                "is_provisional": False,
            }
        ),
        encoding="utf-8",
    )
    (archive_dir / "completed.flag").touch()
    return archive_path


def test_current_db_service_and_public_reader_can_open_the_same_db_in_two_processes(tmp_path: Path) -> None:
    """runner-side DB service を保持中に、別 process の public results CLI が同じ DB を読む。"""

    db_path = tmp_path / "game.db"
    adapter = ArenaDBAdapter(SQLiteShogiDBFactory(db_path))
    adapter.ensure_schema()
    adapter.close()

    ready_path = tmp_path / "holder.ready"
    release_path = tmp_path / "holder.release"
    holder = subprocess.Popen(
        [sys.executable, "-c", _DB_SERVICE_HOLDER_SCRIPT, str(db_path), str(ready_path), str(release_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    try:
        _wait_for_marker(holder, ready_path)
        assert holder.poll() is None
        summary = _run_results_cli(db_path)
        assert summary["completed_games"] == 0
    finally:
        release_path.touch()
        try:
            stdout, stderr = holder.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            holder.kill()
            stdout, stderr = holder.communicate()
            pytest.fail(f"DB holder did not close: stdout={stdout!r} stderr={stderr!r}")

    assert holder.returncode == 0, f"DB holder failed: stdout={stdout!r} stderr={stderr!r}"
    renamed_path = tmp_path / "game.released.db"
    db_path.rename(renamed_path)
    renamed_path.rename(db_path)


def test_public_reader_preserves_every_byte_of_a_writable_legacy_wal_archive(tmp_path: Path) -> None:
    """writable な source でも DB/WAL/SHM を checkpoint・stamp・変更しないこと。"""

    db_path = _build_legacy_wal_archive(tmp_path)
    archive_dir = db_path.parent
    paths = tuple(sorted(archive_dir.iterdir()))
    assert {path.name for path in paths} == {
        "completed.flag",
        "game.db",
        "game.db-shm",
        "game.db-wal",
        "manifest.json",
    }
    before = {path.name: path.read_bytes() for path in paths}

    summary = _run_results_cli(db_path)

    after_paths = tuple(sorted(archive_dir.iterdir()))
    after = {path.name: path.read_bytes() for path in after_paths}
    assert summary["completed_games"] == 1
    assert after == before

    with sqlite3.connect(db_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (0,)
        table_names = {str(row[0]) for row in connection.execute("SELECT name FROM sqlite_master")}
    assert "game_timeout_attribution" not in table_names
    assert "legacy_probe" in table_names


def test_public_reader_preserves_every_byte_of_a_committed_current_wal_artifact(tmp_path: Path) -> None:
    """現行 terminal commit の DB family も source を一切変更しないこと。"""

    db_path = _build_current_wal_archive(tmp_path)
    archive_dir = db_path.parent
    before = {path.name: path.read_bytes() for path in sorted(archive_dir.iterdir())}

    summary = _run_results_cli(db_path)

    after = {path.name: path.read_bytes() for path in sorted(archive_dir.iterdir())}
    assert summary["completed_games"] == 1
    assert after == before


@pytest.mark.parametrize("manifest_version", [None, "1.1.0"])
def test_untrusted_legacy_marker_falls_back_to_online_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    manifest_version: str | None,
) -> None:
    """marker 単独や未知版 manifest を静的コピーの根拠にしないこと。"""

    db_path = _build_legacy_wal_archive(tmp_path)
    manifest_path = db_path.parent / "manifest.json"
    if manifest_version is None:
        manifest_path.unlink()
    else:
        manifest_path.write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "status": "provenance_sealed",
                    "shogiarena_version": manifest_version,
                }
            ),
            encoding="utf-8",
        )

    def reject_file_copy(self: object, snapshot_path: Path) -> None:
        del self, snapshot_path
        raise AssertionError("untrusted legacy artifact must not use file-family copy")

    monkeypatch.setattr(
        "shogiarena._core.contexts.dashboard.adapters.result_summary_reader.SQLiteResultSummaryReader"
        "._copy_file_family",
        reject_file_copy,
    )

    games = SQLiteResultSummaryReader(db_path).read_games()

    assert len(games) == 1


def test_live_writer_and_checkpoint_produce_consistent_online_backup_snapshots(tmp_path: Path) -> None:
    """別 process の commit/checkpoint 中も public reader が一貫した snapshot を読む。"""

    db_path = tmp_path / "live.db"
    adapter = ArenaDBAdapter(SQLiteShogiDBFactory(db_path))
    adapter.ensure_schema()
    adapter.close()
    with sqlite3.connect(db_path) as connection:
        connection.execute("INSERT INTO player (game_type, player_name) VALUES ('arena', 'live-black')")
        connection.execute("INSERT INTO player (game_type, player_name) VALUES ('arena', 'live-white')")
        connection.commit()

    ready_path = tmp_path / "writer.ready"
    release_path = tmp_path / "writer.release"
    writer = subprocess.Popen(
        [sys.executable, "-c", _LIVE_WRITER_SCRIPT, str(db_path), str(ready_path), str(release_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    summaries: list[dict[str, object]] = []
    try:
        _wait_for_marker(writer, ready_path)
        for _ in range(3):
            summaries.append(_run_results_cli(db_path))
    finally:
        release_path.touch()
        try:
            stdout, stderr = writer.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            writer.kill()
            stdout, stderr = writer.communicate()
            pytest.fail(f"live writer did not close: stdout={stdout!r} stderr={stderr!r}")

    assert writer.returncode == 0, f"live writer failed: stdout={stdout!r} stderr={stderr!r}"
    completed = [int(summary["completed_games"]) for summary in summaries]
    assert completed == sorted(completed)
    assert completed[0] >= 1
    for summary, count in zip(summaries, completed, strict=True):
        assert summary["raw_result_counts"] == {"BLACK_WIN": count}


@pytest.mark.skipif(sys.platform != "win32", reason="UNC path semantics are Windows-specific")
def test_read_only_sqlite_uri_percent_encodes_unc_leading_slashes() -> None:
    """UNC server 名を SQLite URI authority にしないこと。"""

    uri = build_sqlite_read_only_uri(Path(r"\\server\share\archive\game.db"))

    assert uri == "file:%2F%2Fserver%2Fshare%2Farchive%2Fgame.db?mode=ro"
    assert not uri.startswith("file://")
