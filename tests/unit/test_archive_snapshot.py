"""アーカイブ DB の hot WAL / hot journal を、原本を変えずに読めることの回帰テスト。"""

from __future__ import annotations

import hashlib
import sqlite3
import stat
import sys
from pathlib import Path
from shutil import copyfile

import pytest

from shogiarena._core.contexts.dashboard.adapters import archive_snapshot
from shogiarena._core.contexts.dashboard.adapters.archive_snapshot import resolve_archive_databases
from shogiarena._core.contexts.dashboard.ports.archive_snapshot_ports import (
    ArchiveDatabaseLivenessError,
)
from shogiarena._core.platform.db.store.repository_factory import build_sqlite_read_only_uri

GAME_DB = Path("game.db")
LEDGER_DB = Path("spsa") / "ledger.sqlite3"
ROW_COUNT = 50


def _tree_identity(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _write_hot_wal_archive(root: Path) -> Path:
    """schema だけを本体に持ち、全行が un-checkpointed WAL にあるアーカイブを作る。

    実際の crash を再現するには writer を kill するしかないが、``-wal`` を開いたまま
    family を複製すれば同じディスク状態が決定的に得られる
    (``test_release_db_compat_production.py`` と同じ手法)。
    """

    archive = root / "archive"
    archive.mkdir(parents=True, exist_ok=True)
    source = root / "source.db"
    connection = sqlite3.connect(source, isolation_level=None)
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA wal_autocheckpoint=0")
        connection.execute("CREATE TABLE game (id INTEGER PRIMARY KEY, name TEXT)")
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        for index in range(ROW_COUNT):
            connection.execute("INSERT INTO game (name) VALUES (?)", (f"game-{index}",))
        for suffix in ("", "-wal", "-shm"):
            copyfile(Path(f"{source}{suffix}"), Path(f"{archive / GAME_DB}{suffix}"))
    finally:
        connection.close()
    return archive


def _write_clean_archive(root: Path) -> Path:
    archive = root / "archive"
    archive.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(archive / GAME_DB, isolation_level=None)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("CREATE TABLE game (id INTEGER PRIMARY KEY, name TEXT)")
    for index in range(ROW_COUNT):
        connection.execute("INSERT INTO game (name) VALUES (?)", (f"game-{index}",))
    connection.close()
    return archive


def _count_rows_as_immutable(db_path: Path) -> int:
    connection = sqlite3.connect(build_sqlite_read_only_uri(db_path, immutable=True), uri=True)
    try:
        return int(connection.execute("SELECT count(*) FROM game").fetchone()[0])
    finally:
        connection.close()


def test_immutable_open_drops_rows_that_live_in_an_un_checkpointed_wal(tmp_path: Path) -> None:
    """修正対象の欠陥そのものを固定する。これが落ちたら fixture が壊れている。"""

    archive = _write_hot_wal_archive(tmp_path)

    assert _count_rows_as_immutable(archive / GAME_DB) == 0


def test_resolved_snapshot_exposes_every_committed_row(tmp_path: Path) -> None:
    archive = _write_hot_wal_archive(tmp_path)

    with resolve_archive_databases(archive, relative_database_paths=[GAME_DB]) as snapshot:
        assert snapshot.materialized_database_paths == (GAME_DB,)
        assert _count_rows_as_immutable(snapshot.database_path_for(GAME_DB)) == ROW_COUNT


def test_resolution_leaves_every_byte_of_the_archive_unchanged(tmp_path: Path) -> None:
    archive = _write_hot_wal_archive(tmp_path)
    before = _tree_identity(archive)

    with resolve_archive_databases(archive, relative_database_paths=[GAME_DB]):
        pass

    assert _tree_identity(archive) == before


def test_resolution_copies_nothing_when_no_sidecar_needs_recovery(tmp_path: Path) -> None:
    archive = _write_clean_archive(tmp_path)
    before = _tree_identity(archive)

    with resolve_archive_databases(archive, relative_database_paths=[GAME_DB]) as snapshot:
        assert snapshot.materialized_database_paths == ()
        assert snapshot.database_path_for(GAME_DB) == archive / GAME_DB

    assert _tree_identity(archive) == before


def test_resolution_copies_nothing_for_a_zero_length_wal(tmp_path: Path) -> None:
    """長さ 0 の ``-wal`` には回復すべきフレームが無いので複製しない。"""

    archive = _write_clean_archive(tmp_path)
    Path(f"{archive / GAME_DB}-wal").write_bytes(b"")

    with resolve_archive_databases(archive, relative_database_paths=[GAME_DB]) as snapshot:
        assert snapshot.materialized_database_paths == ()


def test_resolution_folds_a_hot_rollback_journal(tmp_path: Path) -> None:
    archive = _write_clean_archive(tmp_path)
    journal_path = Path(f"{archive / GAME_DB}-journal")
    journal_path.write_bytes(b"\x00" * 512)
    before = _tree_identity(archive)

    with resolve_archive_databases(archive, relative_database_paths=[GAME_DB]) as snapshot:
        assert snapshot.materialized_database_paths == (GAME_DB,)
        resolved = snapshot.database_path_for(GAME_DB)
        assert _count_rows_as_immutable(resolved) == ROW_COUNT
        assert not Path(f"{resolved}-journal").exists()

    assert _tree_identity(archive) == before


def test_resolution_materializes_only_the_hot_database(tmp_path: Path) -> None:
    """冷えている DB は複製せず原本を指す(数 GB の game.db を巻き込まないため)。"""

    archive = _write_hot_wal_archive(tmp_path)
    (archive / LEDGER_DB).parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(archive / LEDGER_DB, isolation_level=None)
    connection.execute("CREATE TABLE updates (id INTEGER PRIMARY KEY)")
    connection.close()

    with resolve_archive_databases(archive, relative_database_paths=[GAME_DB, LEDGER_DB]) as snapshot:
        assert snapshot.materialized_database_paths == (GAME_DB,)
        assert snapshot.database_path_for(LEDGER_DB) == archive / LEDGER_DB
        assert snapshot.database_path_for(GAME_DB) != archive / GAME_DB


def test_resolution_ignores_databases_that_do_not_exist(tmp_path: Path) -> None:
    archive = _write_hot_wal_archive(tmp_path)

    with resolve_archive_databases(archive, relative_database_paths=[GAME_DB, LEDGER_DB]) as snapshot:
        assert snapshot.database_path_for(LEDGER_DB) == archive / LEDGER_DB
        assert _count_rows_as_immutable(snapshot.database_path_for(GAME_DB)) == ROW_COUNT


def test_resolution_reads_an_archive_whose_files_are_not_writable(tmp_path: Path) -> None:
    """read-only 媒体のアーカイブでも通ること。原本を SQLite で開かないため成立する。"""

    archive = _write_hot_wal_archive(tmp_path)
    members = [path for path in archive.rglob("*") if path.is_file()]
    original_modes = {path: stat.S_IMODE(path.stat().st_mode) for path in members}
    for path in members:
        path.chmod(stat.S_IREAD)
    try:
        with resolve_archive_databases(archive, relative_database_paths=[GAME_DB]) as snapshot:
            assert _count_rows_as_immutable(snapshot.database_path_for(GAME_DB)) == ROW_COUNT
    finally:
        for path, mode in original_modes.items():
            path.chmod(mode)


def test_snapshot_close_removes_the_temporary_copy(tmp_path: Path) -> None:
    archive = _write_hot_wal_archive(tmp_path)

    snapshot = resolve_archive_databases(archive, relative_database_paths=[GAME_DB])
    snapshot_dir = snapshot.database_dir_for(GAME_DB)
    assert snapshot_dir.exists()
    snapshot.close()

    assert not snapshot_dir.exists()
    assert snapshot.materialized_database_paths == ()


@pytest.mark.skipif(sys.platform != "win32", reason="deny-write share modes are Windows-specific")
def test_resolution_refuses_a_database_a_writer_still_holds(tmp_path: Path) -> None:
    """実行中の run を無音の部分的な結果として供給しないこと。"""

    archive = _write_hot_wal_archive(tmp_path)
    writer = sqlite3.connect(archive / GAME_DB, isolation_level=None)
    try:
        with pytest.raises(ArchiveDatabaseLivenessError, match="still executing"):
            resolve_archive_databases(archive, relative_database_paths=[GAME_DB])
    finally:
        writer.close()


def test_portable_fallback_detects_a_database_that_changes_while_it_is_copied(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """deny-write handle が使えない OS では前後比較で writer を検出する。"""

    monkeypatch.setattr(archive_snapshot.sys, "platform", "linux")
    archive = _write_hot_wal_archive(tmp_path)
    wal_path = Path(f"{archive / GAME_DB}-wal")
    real_copyfile = archive_snapshot.copyfile

    def copy_then_advance_the_writer(source: str | Path, destination: str | Path) -> object:
        result = real_copyfile(source, destination)
        if str(source).endswith("-wal"):
            with wal_path.open("ab") as handle:
                handle.write(b"\x00" * 32)
        return result

    monkeypatch.setattr(archive_snapshot, "copyfile", copy_then_advance_the_writer)

    with pytest.raises(ArchiveDatabaseLivenessError, match="still executing"):
        resolve_archive_databases(archive, relative_database_paths=[GAME_DB])
