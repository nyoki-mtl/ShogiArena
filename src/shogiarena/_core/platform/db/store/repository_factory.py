from __future__ import annotations

import asyncio
import logging
import sqlite3
import threading
from collections.abc import Hashable
from pathlib import Path
from typing import Protocol
from urllib.parse import quote

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, scoped_session, sessionmaker
from sqlalchemy.orm.scoping import ScopedSession

from .repository import ShogiRepository

logger = logging.getLogger(__name__)


class _DBAPICursor(Protocol):
    def execute(self, statement: str) -> object: ...

    def close(self) -> None: ...


class _DBAPIConnection(Protocol):
    def cursor(self) -> _DBAPICursor: ...


class BaseFactory:
    def __init__(
        self,
        engine: Engine,
        session_factory: ScopedSession[Session],
        *,
        is_read_only: bool = False,
    ) -> None:
        self._engine = engine
        self._session_factory = session_factory
        self._is_read_only = is_read_only

    def create(self) -> ShogiRepository:
        return ShogiRepository(
            self._engine,
            self._session_factory,
            is_read_only=self._is_read_only,
        )


def build_sqlite_read_only_uri(db_path: str | Path, *, immutable: bool = False) -> str:
    """SQLite が authority と解釈しない read-only file URI を組み立てる。"""

    resolved = Path(db_path).expanduser().resolve().as_posix()
    # UNC の先頭 ``//server/share`` をそのまま残すと SQLite URI の authority に
    # 解釈される。slash を percent-encode し、decode 後の filename だけを UNC にする。
    encoded = quote(resolved, safe=":")
    immutable_query = "&immutable=1" if immutable else ""
    return f"file:{encoded}?mode=ro{immutable_query}"


class SQLiteShogiDBFactory(BaseFactory):
    def __init__(
        self,
        db_path: str | Path = ":memory:",
        should_echo: bool = False,
        *,
        read_only: bool = False,
        immutable: bool = False,
    ) -> None:
        db_path_str = db_path.as_posix() if isinstance(db_path, Path) else db_path
        if read_only:
            if db_path_str == ":memory:":
                raise ValueError("read-only SQLite databases require a filesystem path")
            sqlite_uri = build_sqlite_read_only_uri(db_path_str, immutable=immutable)
            database_url = f"sqlite+pysqlite:///{sqlite_uri}&uri=true"
        else:
            database_url = f"sqlite+pysqlite:///{db_path_str}"
        engine = create_engine(database_url, echo=should_echo)
        _configure_sqlite_pragmas(engine, read_only=read_only)
        session_factory = scoped_session(
            sessionmaker(autocommit=False, autoflush=True, expire_on_commit=False, bind=engine),
            scopefunc=_session_scope_key,
        )
        super().__init__(engine, session_factory, is_read_only=read_only)


def _configure_sqlite_pragmas(engine: Engine, *, read_only: bool = False) -> None:
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection: _DBAPIConnection, _connection_record: object) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=5000")
            if read_only:
                return
            # WAL は書き込み性能のための最適化であり、read-only メディア上の
            # アーカイブ run では設定できない。設定できないことは閲覧の失敗理由に
            # ならないので、既定の journal mode のまま続行する。
            try:
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA synchronous=NORMAL")
            except sqlite3.OperationalError as exc:
                # 許容するのは「書き込めない媒体」だけ。ロックやディスクフルまで
                # 飲むと、書き込み run が遅い構成のまま無言で走り続けてしまう。
                # sqlite_errorcode は拡張コード（SQLITE_READONLY_DIRECTORY=1544 など）を
                # 返すため、下位 8bit の primary コードで判定する。
                if exc.sqlite_errorcode & 0xFF not in (sqlite3.SQLITE_READONLY, sqlite3.SQLITE_CANTOPEN):
                    raise
                logger.debug("Kept the default journal mode on a non-writable database: %s", exc)
        finally:
            cursor.close()


def _session_scope_key() -> Hashable:
    try:
        task = asyncio.current_task()
    except RuntimeError:
        task = None
    if task is not None:
        return ("asyncio", id(task))
    return ("thread", threading.get_ident())


__all__ = ["BaseFactory", "SQLiteShogiDBFactory", "build_sqlite_read_only_uri"]
