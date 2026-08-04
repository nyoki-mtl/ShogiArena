"""SPSA ledger の journal mode と、reader/writer の相互ブロッキングに関する回帰テスト。

ledger は event loop 上の writer と worker thread 上の read-only reader が同時に触る。
rollback journal では両者が相互にブロックし、dashboard の応答遅延と event loop の停止を
生む(task 0062)。ここではロックの「有無」を決定的に固定する。壁時計の閾値は使わない。
"""

from __future__ import annotations

import shutil
import sqlite3
import stat
from pathlib import Path

import pytest

from shogiarena._core.contexts.spsa.adapters.ledger_store import (
    SpsaLedger,
    open_spsa_ledger,
    recover_spsa_ledger,
)
from shogiarena._core.contexts.spsa.ports.ledger_ports import SPSA_LEDGER_RELATIVE_PATH

_CONTRACT_COLUMNS = (
    "run_id, contract_schema, resume_hash, space_digest, rng_schema, "
    "sealed_run_seed, status, contract_json, created_at, updated_at"
)
_CONTRACT_VALUES = (
    "run-1",
    "v1",
    "resume",
    "space",
    "rng",
    "seed",
    "running",
    "{}",
    "2026-01-01T00:00:00+00:00",
    "2026-01-01T00:00:00+00:00",
)


def _seed_contract(connection: sqlite3.Connection) -> None:
    connection.execute(
        f"INSERT INTO run_contract ({_CONTRACT_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        _CONTRACT_VALUES,
    )
    connection.commit()


def _journal_mode(connection: sqlite3.Connection) -> str:
    row = connection.execute("PRAGMA journal_mode").fetchone()
    return str(row[0]).lower()


def _sidecars(run_dir: Path) -> list[str]:
    ledger_path = run_dir / SPSA_LEDGER_RELATIVE_PATH
    return sorted(
        path.name
        for path in ledger_path.parent.iterdir()
        if path.name.startswith(ledger_path.name) and path.name != ledger_path.name
    )


def test_run_owned_writer_uses_write_ahead_logging_and_restores_it_at_rest(tmp_path: Path) -> None:
    """run 実行中は WAL、clean close 後は rollback journal に戻ること。

    競合は run 実行中にしか存在しない。at-rest の形式を変えないことで、
    アーカイブ・read-only 媒体・既存の整合性テストとの互換が保たれる。
    """

    with open_spsa_ledger(tmp_path, use_write_ahead_logging=True) as ledger:
        _seed_contract(ledger.connection)
        assert _journal_mode(ledger.connection) == "wal"
        # synchronous=NORMAL は 1。
        assert ledger.connection.execute("PRAGMA synchronous").fetchone() == (1,)
        assert "ledger.sqlite3-wal" in _sidecars(tmp_path)

    assert _sidecars(tmp_path) == []
    with open_spsa_ledger(tmp_path) as reopened:
        assert _journal_mode(reopened.connection) == "delete"


def test_wal_stays_at_rest_when_a_reader_is_still_open_and_converges_later(tmp_path: Path) -> None:
    """reader が残っていると at-rest は WAL のままだが、次の clean close で収束すること。

    dashboard を有効にした run では teardown 時に reader が残ることがある。close は
    失敗させず、``-wal`` は checkpoint 済み(長さ 0)なので本体だけのコピーも安全。
    """

    writer = open_spsa_ledger(tmp_path, use_write_ahead_logging=True)
    _seed_contract(writer.connection)
    reader = open_spsa_ledger(tmp_path, read_only=True)
    reader.connection.execute("SELECT run_id FROM run_contract").fetchall()
    writer.close()
    reader.close()

    # writer が閉じた時点で checkpoint は済んでおり、残る "-wal" は長さ 0。
    assert (tmp_path / "spsa" / "ledger.sqlite3-wal").stat().st_size == 0
    with open_spsa_ledger(tmp_path) as inspected:
        assert _journal_mode(inspected.connection) == "wal"

    # reader の居ない状態で run が回れば at-rest は rollback journal へ戻る。
    with open_spsa_ledger(tmp_path, use_write_ahead_logging=True):
        pass
    with open_spsa_ledger(tmp_path) as converged:
        assert _journal_mode(converged.connection) == "delete"
    assert _sidecars(tmp_path) == []


def test_validation_open_does_not_change_the_journal_mode(tmp_path: Path) -> None:
    """検証や recovery のための writable open は journal mode を変えないこと。"""

    with open_spsa_ledger(tmp_path, use_write_ahead_logging=True) as ledger:
        _seed_contract(ledger.connection)

    before = (tmp_path / SPSA_LEDGER_RELATIVE_PATH).read_bytes()
    recover_spsa_ledger(tmp_path)
    with open_spsa_ledger(tmp_path) as validated:
        assert _journal_mode(validated.connection) == "delete"
    assert (tmp_path / SPSA_LEDGER_RELATIVE_PATH).read_bytes() == before
    assert _sidecars(tmp_path) == []


def test_read_only_open_leaves_no_sidecars_at_rest(tmp_path: Path) -> None:
    """at-rest の ledger を read-only で開いても sidecar を残さないこと。

    read-only 接続は自分が作った ``-shm`` / ``-wal`` を消せないため、
    at-rest が WAL のままだと「棄却時に tree を書き換えない」不変条件が崩れる。
    """

    with open_spsa_ledger(tmp_path, use_write_ahead_logging=True) as ledger:
        _seed_contract(ledger.connection)
    before = {path.name: path.read_bytes() for path in (tmp_path / "spsa").iterdir()}

    with open_spsa_ledger(tmp_path, read_only=True) as reader:
        assert reader.connection.execute("SELECT run_id FROM run_contract").fetchall() == [("run-1",)]

    assert {path.name: path.read_bytes() for path in (tmp_path / "spsa").iterdir()} == before


def test_legacy_delete_journal_ledger_is_accepted_by_the_run_writer(tmp_path: Path) -> None:
    """delete journal の既存 run が resume で WAL 化され、内容が保たれること。"""

    with open_spsa_ledger(tmp_path) as ledger:
        _seed_contract(ledger.connection)
        assert _journal_mode(ledger.connection) == "delete"

    recover_spsa_ledger(tmp_path)

    with open_spsa_ledger(tmp_path, use_write_ahead_logging=True) as reopened:
        assert _journal_mode(reopened.connection) == "wal"
        assert reopened.connection.execute("SELECT run_id FROM run_contract").fetchall() == [("run-1",)]
    assert _sidecars(tmp_path) == []


def test_read_only_open_succeeds_on_read_only_files_after_clean_close(tmp_path: Path) -> None:
    """clean close 後の ledger は read-only 属性のままでも開けること。"""

    with open_spsa_ledger(tmp_path) as ledger:
        _seed_contract(ledger.connection)
    ledger_path = tmp_path / SPSA_LEDGER_RELATIVE_PATH
    ledger_path.chmod(stat.S_IREAD)
    try:
        with open_spsa_ledger(tmp_path, read_only=True) as reader:
            assert reader.connection.execute("SELECT run_id FROM run_contract").fetchall() == [("run-1",)]
    finally:
        ledger_path.chmod(stat.S_IREAD | stat.S_IWRITE)


def test_hand_copied_database_without_wal_sees_the_checkpointed_prefix_only(tmp_path: Path) -> None:
    """本体だけを手コピーすると checkpoint 済みの prefix しか見えないこと(仕様の固定)。"""

    source = tmp_path / "source"
    ledger = open_spsa_ledger(source, use_write_ahead_logging=True)
    try:
        _seed_contract(ledger.connection)
        ledger.connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        ledger.connection.execute(
            "INSERT INTO event_revisions (run_id, revision, event_type, payload_json, created_at) "
            "VALUES ('run-1', 1, 'after_checkpoint', '{}', '2026-01-01T00:00:00+00:00')"
        )
        ledger.connection.commit()

        target = tmp_path / "copy"
        (target / "spsa").mkdir(parents=True)
        shutil.copy2(source / SPSA_LEDGER_RELATIVE_PATH, target / SPSA_LEDGER_RELATIVE_PATH)
    finally:
        ledger.close()

    with open_spsa_ledger(target, read_only=True) as copied:
        revisions = copied.connection.execute("SELECT revision FROM event_revisions").fetchall()
    assert revisions == [], "checkpoint 後の commit は -wal 側にあり、本体だけのコピーには含まれない"


def test_recovery_preserves_committed_rows_left_in_a_hot_wal(tmp_path: Path) -> None:
    """``-wal`` を伴う run dir を丸ごとコピーすれば commit 済みの行が残ること。"""

    source = tmp_path / "source"
    ledger = open_spsa_ledger(source, use_write_ahead_logging=True)
    try:
        _seed_contract(ledger.connection)
        ledger.connection.execute(
            "INSERT INTO event_revisions (run_id, revision, event_type, payload_json, created_at) "
            "VALUES ('run-1', 1, 'in_wal', '{}', '2026-01-01T00:00:00+00:00')"
        )
        ledger.connection.commit()
        assert "ledger.sqlite3-wal" in _sidecars(source)

        target = tmp_path / "copy"
        shutil.copytree(source, target)
    finally:
        ledger.close()

    recover_spsa_ledger(target)
    with open_spsa_ledger(target, read_only=True) as copied:
        revisions = copied.connection.execute("SELECT revision FROM event_revisions").fetchall()
    assert revisions == [(1,)]


def _open_reader_holding_a_shared_lock(run_dir: Path) -> SpsaLedger:
    reader = open_spsa_ledger(run_dir, read_only=True)
    # deferred transaction は SELECT を発行するまでロックを取らない。
    reader.connection.execute("BEGIN")
    reader.connection.execute("SELECT run_id FROM run_contract").fetchall()
    return reader


def test_write_ahead_logging_lets_a_reader_and_a_writer_proceed_together(tmp_path: Path) -> None:
    """WAL では reader が読んでいる最中でも writer が commit できること。

    ledger writer は event loop 上で commit するため、reader に待たされることは
    そのまま event loop の停止になる。busy_timeout=0 で「待ちの有無」を決定的に見る。
    """

    ledger = open_spsa_ledger(tmp_path, use_write_ahead_logging=True)
    try:
        _seed_contract(ledger.connection)
        reader = _open_reader_holding_a_shared_lock(tmp_path)
        try:
            ledger.connection.execute("PRAGMA busy_timeout=0")
            ledger.connection.execute("UPDATE run_contract SET revision = revision + 1")
            ledger.connection.commit()

            # 逆方向: writer が未 commit の書き込みを保持していても reader は読める。
            ledger.connection.execute("UPDATE run_contract SET revision = revision + 1")
            assert reader.connection.execute("SELECT run_id FROM run_contract").fetchall() == [("run-1",)]
            ledger.connection.commit()
        finally:
            reader.connection.rollback()
            reader.close()
    finally:
        ledger.close()


def test_rollback_journal_makes_a_reader_and_a_writer_block_each_other(tmp_path: Path) -> None:
    """delete journal では相互にブロックすること(上のテストが機能していることの対照)。"""

    ledger = open_spsa_ledger(tmp_path)
    try:
        _seed_contract(ledger.connection)
        ledger.connection.execute("PRAGMA journal_mode=DELETE")
        reader = _open_reader_holding_a_shared_lock(tmp_path)
        try:
            ledger.connection.execute("PRAGMA busy_timeout=0")
            ledger.connection.execute("UPDATE run_contract SET revision = revision + 1")
            with pytest.raises(sqlite3.OperationalError, match="locked|busy"):
                ledger.connection.commit()
            ledger.connection.rollback()
        finally:
            reader.connection.rollback()
            reader.close()
    finally:
        ledger.close()
