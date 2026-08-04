"""SPSA optimizer ledger のconnection factory。

ledger は run が権威とする provenance evidence である。書き込みは event loop 上で
同期的に commit され、dashboard と派生 JSON 投影は worker thread から read-only 接続で
同じファイルを読む。rollback journal ではこの 2 者が相互にブロックし、reader の裾と
loop の停止の両方を生むため、書き込み接続では WAL を使う(task 0062 / 0064)。
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from types import TracebackType
from urllib.parse import quote

from shogiarena._core.contexts.spsa.adapters.ledger_schema import (
    CURRENT_SPSA_LEDGER_SCHEMA_VERSION,
    SpsaLedgerSchemaError,
    create_canonical_schema,
    has_managed_tables,
    read_user_version,
    stamp_user_version,
    validate_canonical_schema,
)
from shogiarena._core.contexts.spsa.ports.ledger_ports import SPSA_LEDGER_RELATIVE_PATH

logger = logging.getLogger(__name__)


class SpsaLedger:
    """Validated SQLite connection owned by one SPSA run."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        path: Path,
        is_read_only: bool,
        uses_write_ahead_logging: bool = False,
    ) -> None:
        self._connection = connection
        self.path = path
        self.is_read_only = is_read_only
        self.uses_write_ahead_logging = uses_write_ahead_logging

    @property
    def connection(self) -> sqlite3.Connection:
        """Return the validated connection for ledger repositories."""

        return self._connection

    def checkpoint(self) -> None:
        """WAL を本体へ畳んで、これまでの commit を durable にする。

        ``synchronous=NORMAL`` では commit 単体は durable ではない。terminal commit を
        信号にする artifact を書く前に呼ぶ。rollback journal では no-op になる。
        """

        checkpoint_spsa_ledger(self._connection)

    def close(self) -> None:
        """Close the ledger connection explicitly。

        WAL を有効にした接続では、閉じる前に WAL を畳んで rollback journal へ戻す。
        run が動いていない ledger の at-rest 形式を変えないことで、read-only open が
        sidecar を作らず、アーカイブと read-only 媒体の互換も保たれる。
        """

        if self.uses_write_ahead_logging:
            checkpoint_spsa_ledger(self._connection)
            _restore_rollback_journal(self._connection, path=self.path)
        self._connection.close()

    def __enter__(self) -> SpsaLedger:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback
        self.close()


def open_spsa_ledger(
    run_dir: Path,
    *,
    read_only: bool = False,
    use_write_ahead_logging: bool = False,
) -> SpsaLedger:
    """Open/create and strictly validate ``spsa/ledger.sqlite3``。

    ``use_write_ahead_logging`` は run を所有する長命の writer だけが指定する。
    検証や recovery のための一時的な writable open は journal mode を変えない。
    棄却する ledger のバイト列に触れないためであり、read-only open が sidecar を
    作らないためでもある。
    """

    path = run_dir / SPSA_LEDGER_RELATIVE_PATH
    if read_only:
        if not path.is_file():
            raise SpsaLedgerSchemaError(f"SPSA ledger does not exist for read-only open: {path}")
        connection = _connect_read_only(path)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = _connect_writable(path)
    uses_wal = False
    try:
        _initialize_or_validate(connection, read_only=read_only)
        # journal mode の変換は DB ファイルを書き換える。棄却する ledger には触れないよう、
        # canonical schema の検証が通ったあとにだけ行う。
        if use_write_ahead_logging and not read_only:
            uses_wal = _apply_write_ahead_logging(connection, path=path)
    except BaseException:
        connection.close()
        raise
    return SpsaLedger(
        connection,
        path=path,
        is_read_only=read_only,
        uses_write_ahead_logging=uses_wal,
    )


def recover_spsa_ledger(run_dir: Path) -> None:
    """Recover and validate an existing current ledger before resume reads."""

    path = run_dir / SPSA_LEDGER_RELATIVE_PATH
    if not path.is_file():
        raise SpsaLedgerSchemaError(f"SPSA ledger does not exist for recovery: {path}")
    connection = _connect_writable(path)
    try:
        version = read_user_version(connection)
        if version != CURRENT_SPSA_LEDGER_SCHEMA_VERSION:
            raise SpsaLedgerSchemaError(
                "Unsupported SPSA ledger schema version "
                f"{version}; expected {CURRENT_SPSA_LEDGER_SCHEMA_VERSION}. "
                "Restore a compatible ledger or start a fresh run with --no-resume."
            )
        if not has_managed_tables(connection):
            raise SpsaLedgerSchemaError(
                "Current SPSA ledger has no canonical schema. "
                "Restore it from backup or start a fresh run with --no-resume."
            )
        validate_canonical_schema(connection)
    finally:
        connection.close()


def checkpoint_spsa_ledger(connection: sqlite3.Connection) -> None:
    """WAL を本体へ畳み、``-wal`` を切り詰める。

    ``synchronous=NORMAL`` では commit 単体は durable ではない。terminal commit のあと、
    それを信号にする artifact(``completed.flag`` など)を書く前に呼ぶこと。
    delete journal の ledger では no-op になる。
    """

    try:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except sqlite3.Error as exc:  # pragma: no cover - defensive; ledger stays authoritative
        logger.debug("SPSA ledger WAL checkpoint was skipped: %s", exc)


def _connect_writable(path: Path) -> sqlite3.Connection:
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(path, timeout=30.0)
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection
    except sqlite3.DatabaseError as exc:
        if connection is not None:
            connection.close()
        raise SpsaLedgerSchemaError(
            f"Unable to open SPSA ledger {path}: {_sqlite_error_label(exc)}. "
            "Restore it from backup or start a fresh run with --no-resume."
        ) from exc


def _apply_write_ahead_logging(connection: sqlite3.Connection, *, path: Path) -> bool:
    """run を所有する writer に WAL を設定し、実際に有効化できたかを返す。

    ``synchronous=NORMAL`` を併せて設定する。喪失モードは「電源断で checkpoint 以降の
    commit 接尾辞が失われる」だが、resume は連続する committed prefix から再計画するため
    既存のクラッシュ時の振る舞いと同型である。
    """

    try:
        row = connection.execute("PRAGMA journal_mode=WAL").fetchone()
        connection.execute("PRAGMA synchronous=NORMAL")
    except sqlite3.OperationalError as exc:
        # 許容するのは「書き込めない媒体」だけ。ロックやディスクフルまで飲むと、
        # 相互ブロッキングの残った構成のまま無言で走り続けてしまう。
        if exc.sqlite_errorcode & 0xFF not in (sqlite3.SQLITE_READONLY, sqlite3.SQLITE_CANTOPEN):
            raise
        logger.debug("Kept the default journal mode on a non-writable SPSA ledger %s: %s", path, exc)
        return False
    # journal_mode は例外ではなく現在の mode を返して失敗することがある。
    if row is None or str(row[0]).lower() != "wal":
        logger.warning("SPSA ledger %s stayed in %s journal mode", path, "unknown" if row is None else row[0])
        return False
    return True


def _restore_rollback_journal(connection: sqlite3.Connection, *, path: Path) -> None:
    """at-rest の journal mode を rollback journal へ戻す(best-effort)。

    SQLite は他の接続が 1 つでも残っていると WAL から出られない。dashboard を有効にした
    run では teardown の時点で reader が残っていることがあり、その場合 at-rest は WAL の
    ままになる。正しさは損なわれない: ``-wal`` は checkpoint 済みで長さ 0 なので、
    本体だけのコピーも read-only 媒体での open も通る。次に reader の居ない状態で
    clean close されたときに rollback journal へ収束する。
    """

    try:
        connection.commit()
        row = connection.execute("PRAGMA journal_mode=DELETE").fetchone()
    except sqlite3.Error as exc:
        logger.debug("SPSA ledger %s kept WAL at rest: %s", path, exc)
        return
    if row is None or str(row[0]).lower() != "delete":
        logger.debug("SPSA ledger %s kept WAL at rest while another connection is open", path)


def _connect_read_only(path: Path) -> sqlite3.Connection:
    uri = f"file:{quote(str(path.resolve()), safe='/:')}?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True, timeout=2.0, check_same_thread=False)
        # dashboard と派生 JSON 投影は「最終的に追いつくこと」しか要求しない。
        # WAL では writer と競合しないが、万一待つ場合でも次のポーリングに回す方がよい。
        connection.execute("PRAGMA busy_timeout=2000")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection
    except sqlite3.DatabaseError as exc:
        raise SpsaLedgerSchemaError(
            f"Unable to open SPSA ledger read-only {path}: {_sqlite_error_label(exc)}. "
            "A ledger copied together with its -wal sidecar cannot be opened from read-only "
            "media; copy it to a writable location or open it in the original run directory. "
            "Otherwise restore it from backup or use a compatible archive."
        ) from exc


def _initialize_or_validate(connection: sqlite3.Connection, *, read_only: bool) -> None:
    version = read_user_version(connection)
    if version not in {0, CURRENT_SPSA_LEDGER_SCHEMA_VERSION}:
        raise SpsaLedgerSchemaError(
            "Unsupported SPSA ledger schema version "
            f"{version}; expected {CURRENT_SPSA_LEDGER_SCHEMA_VERSION}. "
            "Restore a compatible ledger or start a fresh run with --no-resume."
        )
    has_tables = has_managed_tables(connection)
    if not has_tables:
        if read_only:
            raise SpsaLedgerSchemaError("Read-only SPSA ledger has no canonical schema")
        if version != 0:
            raise SpsaLedgerSchemaError(
                "Versioned SPSA ledger has no canonical schema. "
                "Restore it from backup or start a fresh run with --no-resume."
            )
        create_canonical_schema(connection)
    validate_canonical_schema(connection)
    if version == 0 and not read_only:
        stamp_user_version(connection)


def _sqlite_error_label(exc: sqlite3.DatabaseError) -> str:
    error_name = getattr(exc, "sqlite_errorname", None)
    return error_name if isinstance(error_name, str) else str(exc)


__all__ = ["SpsaLedger", "checkpoint_spsa_ledger", "open_spsa_ledger", "recover_spsa_ledger"]
