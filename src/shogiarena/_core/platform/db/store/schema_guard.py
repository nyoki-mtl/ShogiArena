from __future__ import annotations

import logging
from collections.abc import Mapping

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DatabaseError, OperationalError
from sqlalchemy.sql.schema import Column, Table

logger = logging.getLogger(__name__)

CURRENT_STORE_SCHEMA_VERSION = 1

_COMPATIBLE_ADDITIVE_COLUMNS: dict[str, dict[str, str]] = {
    "game": {
        "metadata_attributes_json": "TEXT",
    },
    "game_move": {
        "engine_wall_time_ms": "INTEGER",
        "move_source": "VARCHAR(16)",
        "book_hit": "SMALLINT",
    },
}

_SCHEMA_REPAIR_GUIDANCE = (
    "Restore a compatible database from backup, or start a fresh run with a new database path. "
    "ShogiArena does not automatically rewrite incompatible databases."
)


class StoreSchemaError(RuntimeError):
    """game DB の schema 契約違反。呼び出し側が型で分岐できるようにする。"""


def _schema_error(detail: str) -> StoreSchemaError:
    return StoreSchemaError(f"Unsupported shogidb schema detected: {detail}. {_SCHEMA_REPAIR_GUIDANCE}")


def _read_schema_version(engine: Engine) -> int:
    # 破損ファイルや非 SQLite ファイルは生の DatabaseError になる。復旧手順を伝えるため包む。
    try:
        with engine.connect() as connection:
            return int(connection.execute(text("PRAGMA user_version")).scalar_one())
    except DatabaseError as exc:
        raise _schema_error(f"the database file could not be read ({exc.orig or exc})") from exc


def _write_schema_version(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(text(f"PRAGMA user_version={CURRENT_STORE_SCHEMA_VERSION}"))


def _managed_table_names() -> set[str]:
    from .entities import Base

    return {table.name for table in Base.metadata.sorted_tables}


def _normalize_type(value: object) -> str:
    return "".join(str(value).upper().split())


def _expected_type(engine: Engine, column: Column[object]) -> str:
    return _normalize_type(column.type.compile(dialect=engine.dialect))


def _column_contract_errors(engine: Engine, table: Table) -> list[str]:
    inspector = inspect(engine)
    actual_columns = {str(column["name"]): column for column in inspector.get_columns(table.name)}
    expected_columns = {column.name: column for column in table.columns}
    errors: list[str] = []

    missing = sorted(expected_columns.keys() - actual_columns.keys())
    unexpected = sorted(actual_columns.keys() - expected_columns.keys())
    if missing:
        errors.append(f"missing columns: {', '.join(missing)}")
    if unexpected:
        errors.append(f"unexpected columns: {', '.join(unexpected)}")

    for name in sorted(expected_columns.keys() & actual_columns.keys()):
        expected = expected_columns[name]
        actual = actual_columns[name]
        actual_type = _normalize_type(actual["type"])
        expected_type = _expected_type(engine, expected)
        if actual_type != expected_type:
            errors.append(f"column {name} type is {actual_type}, expected {expected_type}")
        if bool(actual["nullable"]) != expected.nullable:
            errors.append(f"column {name} nullable is {bool(actual['nullable'])}, expected {expected.nullable}")
    expected_primary_key = tuple(column.name for column in table.primary_key.columns)
    reflected_primary_key = inspector.get_pk_constraint(table.name).get("constrained_columns")
    actual_primary_key = tuple(reflected_primary_key or [])
    if actual_primary_key != expected_primary_key:
        errors.append(f"primary key is {actual_primary_key}, expected {expected_primary_key}")
    return errors


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list | tuple):
        return ()
    return tuple(str(item) for item in value)


def _foreign_key_signature(payload: Mapping[str, object]) -> tuple[tuple[str, ...], str, tuple[str, ...], str, str]:
    options = payload.get("options")
    option_mapping = options if isinstance(options, Mapping) else {}
    return (
        _string_tuple(payload.get("constrained_columns")),
        str(payload.get("referred_table") or ""),
        _string_tuple(payload.get("referred_columns")),
        str(option_mapping.get("onupdate") or "").upper(),
        str(option_mapping.get("ondelete") or "").upper(),
    )


def _expected_foreign_keys(table: Table) -> set[tuple[tuple[str, ...], str, tuple[str, ...], str, str]]:
    expected: set[tuple[tuple[str, ...], str, tuple[str, ...], str, str]] = set()
    for constraint in table.foreign_key_constraints:
        elements = list(constraint.elements)
        expected.add(
            (
                tuple(element.parent.name for element in elements),
                elements[0].column.table.name,
                tuple(element.column.name for element in elements),
                str(constraint.onupdate or "").upper(),
                str(constraint.ondelete or "").upper(),
            )
        )
    return expected


def _relationship_contract_errors(engine: Engine, table: Table) -> list[str]:
    inspector = inspect(engine)
    errors: list[str] = []
    actual_foreign_keys = {_foreign_key_signature(item) for item in inspector.get_foreign_keys(table.name)}
    expected_foreign_keys = _expected_foreign_keys(table)
    if actual_foreign_keys != expected_foreign_keys:
        errors.append("foreign key contract differs from canonical schema")

    actual_indexes = {
        str(item["name"]): (tuple(str(column) for column in item["column_names"]), bool(item["unique"]))
        for item in inspector.get_indexes(table.name)
        if item.get("name") is not None
    }
    for index in table.indexes:
        expected_index = (tuple(column.name for column in index.columns), bool(index.unique))
        if actual_indexes.get(index.name) != expected_index:
            errors.append(f"missing or incompatible index: {index.name}")

    actual_unique_columns = {
        tuple(str(column) for column in item["column_names"]) for item in inspector.get_unique_constraints(table.name)
    }
    for column in table.columns:
        if column.unique and (column.name,) not in actual_unique_columns:
            errors.append(f"missing unique constraint: {column.name}")
    return errors


def _validate_full_schema(engine: Engine) -> None:
    from .entities import Base

    inspector = inspect(engine)
    actual_tables = set(inspector.get_table_names())
    expected_tables = _managed_table_names()
    missing_tables = sorted(expected_tables - actual_tables)
    if missing_tables:
        raise _schema_error(f"missing managed tables: {', '.join(missing_tables)}")

    for table in Base.metadata.sorted_tables:
        errors = _column_contract_errors(engine, table)
        errors.extend(_relationship_contract_errors(engine, table))
        if errors:
            raise _schema_error(f"table '{table.name}' has {'; '.join(errors)}")


def _apply_compatible_additive_columns(engine: Engine) -> None:
    from .entities import Base

    inspector = inspect(engine)
    for table in Base.metadata.sorted_tables:
        if not inspector.has_table(table.name):
            continue
        actual_columns = {str(column["name"]) for column in inspector.get_columns(table.name)}
        expected_columns = {column.name for column in table.columns}
        missing_columns = sorted(expected_columns - actual_columns)
        column_defs = _COMPATIBLE_ADDITIVE_COLUMNS.get(table.name, {})
        compatible_columns = [column for column in missing_columns if column in column_defs]
        if compatible_columns:
            with engine.begin() as connection:
                for column_name in compatible_columns:
                    connection.execute(
                        text(f"ALTER TABLE {table.name} ADD COLUMN {column_name} {column_defs[column_name]}")
                    )
            inspector = inspect(engine)


def ensure_store_schema_for_query(engine: Engine) -> None:
    """Validate and, for a fully compatible legacy DB, stamp the schema version.

    読み取り経路なので、version の刻印に失敗しても読み取り自体は失敗させない。
    アーカイブ済み run や read-only メディア上の DB を閲覧する用途があり、
    そこでは書き込みできないことが正常である。
    """

    version = _read_schema_version(engine)
    if version not in {0, CURRENT_STORE_SCHEMA_VERSION}:
        raise _schema_error(f"schema version is {version}, expected {CURRENT_STORE_SCHEMA_VERSION}")
    _validate_full_schema(engine)
    if version == 0:
        try:
            _write_schema_version(engine)
        except OperationalError as exc:
            logger.debug("Skipped schema version stamp on a non-writable database: %s", exc.orig or exc)


def initialize_store_schema(engine: Engine) -> None:
    """Initialize a new DB or validate a supported existing DB without destructive migration."""

    from .entities import Base

    version = _read_schema_version(engine)
    if version not in {0, CURRENT_STORE_SCHEMA_VERSION}:
        raise _schema_error(f"schema version is {version}, expected {CURRENT_STORE_SCHEMA_VERSION}")
    if version == CURRENT_STORE_SCHEMA_VERSION:
        _validate_full_schema(engine)
        return

    actual_tables = set(inspect(engine).get_table_names())
    managed_tables = _managed_table_names()
    present_managed_tables = actual_tables & managed_tables
    if present_managed_tables and present_managed_tables != managed_tables:
        missing_tables = sorted(managed_tables - present_managed_tables)
        raise _schema_error(f"partial legacy schema has missing managed tables: {', '.join(missing_tables)}")
    try:
        if present_managed_tables:
            _apply_compatible_additive_columns(engine)
        Base.metadata.create_all(engine)
    except OperationalError as exc:
        # 同じ game.db を runner と dashboard が同時に開くと、テーブル有無の確認と
        # 作成の間に別プロセスが先に作り終えることがある。結果が正しければ競合は無害。
        logger.debug("Concurrent schema initialization detected, revalidating: %s", exc.orig or exc)
        _validate_full_schema(engine)
    else:
        _validate_full_schema(engine)
    _write_schema_version(engine)


__all__ = [
    "CURRENT_STORE_SCHEMA_VERSION",
    "StoreSchemaError",
    "ensure_store_schema_for_query",
    "initialize_store_schema",
]
