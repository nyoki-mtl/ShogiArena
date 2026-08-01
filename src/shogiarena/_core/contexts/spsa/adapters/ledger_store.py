"""SPSA optimizer ledger のconnection factory。"""

from __future__ import annotations

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


class SpsaLedger:
    """Validated SQLite connection owned by one SPSA run."""

    def __init__(self, connection: sqlite3.Connection, *, path: Path, is_read_only: bool) -> None:
        self._connection = connection
        self.path = path
        self.is_read_only = is_read_only

    @property
    def connection(self) -> sqlite3.Connection:
        """Return the validated connection for ledger repositories."""

        return self._connection

    def close(self) -> None:
        """Close the ledger connection explicitly."""

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
) -> SpsaLedger:
    """Open/create and strictly validate ``spsa/ledger.sqlite3``."""

    path = run_dir / SPSA_LEDGER_RELATIVE_PATH
    if read_only:
        if not path.is_file():
            raise SpsaLedgerSchemaError(f"SPSA ledger does not exist for read-only open: {path}")
        connection = _connect_read_only(path)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = _connect_writable(path)
    try:
        _initialize_or_validate(connection, read_only=read_only)
    except BaseException:
        connection.close()
        raise
    return SpsaLedger(connection, path=path, is_read_only=read_only)


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


def _connect_writable(path: Path) -> sqlite3.Connection:
    try:
        connection = sqlite3.connect(path, timeout=30.0)
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection
    except sqlite3.DatabaseError as exc:
        raise SpsaLedgerSchemaError(
            f"Unable to open SPSA ledger {path}: {_sqlite_error_label(exc)}. "
            "Restore it from backup or start a fresh run with --no-resume."
        ) from exc


def _connect_read_only(path: Path) -> sqlite3.Connection:
    uri = f"file:{quote(str(path.resolve()), safe='/:')}?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True, timeout=30.0, check_same_thread=False)
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection
    except sqlite3.DatabaseError as exc:
        raise SpsaLedgerSchemaError(
            f"Unable to open SPSA ledger read-only {path}: {_sqlite_error_label(exc)}. "
            "Restore it from backup or use a compatible archive."
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


__all__ = ["SpsaLedger", "open_spsa_ledger", "recover_spsa_ledger"]
