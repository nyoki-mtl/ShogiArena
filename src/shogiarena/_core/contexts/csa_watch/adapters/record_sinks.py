"""Where a verified CSA game is put: a ``.csa`` file, and the run database."""

from __future__ import annotations

import logging
from pathlib import Path

from rsshogi.record import Record
from sqlalchemy.exc import OperationalError

from shogiarena._core.contexts.csa_watch.ports.record_sink_ports import (
    CSA_EXPORT_VERSION,
    CsaRecordStoreRetryableError,
)
from shogiarena._core.platform.db.store.record_store import DBRecordStore
from shogiarena._core.platform.db.store.repository_factory import SQLiteShogiDBFactory

logger = logging.getLogger(__name__)


class CsaRecordFileWriter:
    """Writes CSA V3.0 text, declaring the encoding it actually wrote."""

    def write(self, record: Record, path: Path) -> int:
        path.parent.mkdir(parents=True, exist_ok=True)
        text = record.to_csa(version=CSA_EXPORT_VERSION)
        data = text.encode("utf-8")
        # `write_bytes` rather than `write_text`: the `'CSA encoding=UTF-8` line
        # the export emits has to match the bytes on disk, and a platform newline
        # translation would also change the byte count reported back.
        path.write_bytes(data)
        return len(data)

    @property
    def version(self) -> str:
        return CSA_EXPORT_VERSION


class SqliteCsaRecordStore:
    """Appends finished games to an arena ``game.db`` without widening its schema."""

    def __init__(self, db_path: Path) -> None:
        self._repository = SQLiteShogiDBFactory(db_path).create()
        self._repository.create_tables()
        self._store = DBRecordStore(self._repository)

    def persist(self, record: Record) -> None:
        try:
            self._store.append([record], should_update=True)
        except OperationalError as exc:
            detail = str(exc).lower()
            if "database is locked" in detail or "database is busy" in detail:
                raise CsaRecordStoreRetryableError("SQLite database is locked") from exc
            raise

    def close(self) -> None:
        self._repository.close_db()


__all__ = ["CsaRecordFileWriter", "SqliteCsaRecordStore"]
