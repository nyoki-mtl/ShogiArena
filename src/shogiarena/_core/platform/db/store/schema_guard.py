from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

_COMPATIBLE_ADDITIVE_COLUMNS: dict[str, dict[str, str]] = {
    "game_move": {
        "engine_wall_time_ms": "INTEGER",
    },
}


def _apply_compatible_additive_columns(engine: Engine, table_name: str, missing_columns: list[str]) -> bool:
    column_defs = _COMPATIBLE_ADDITIVE_COLUMNS.get(table_name, {})
    if not missing_columns or any(column not in column_defs for column in missing_columns):
        return False
    with engine.begin() as connection:
        for column_name in missing_columns:
            connection.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_defs[column_name]}"))
    return True


def ensure_canonical_store_schema(engine: Engine) -> None:
    """Reject existing managed tables whose schema diverges from canonical metadata."""

    from .entities import Base

    inspector = inspect(engine)
    for table in Base.metadata.sorted_tables:
        if not inspector.has_table(table.name):
            continue

        actual_columns = {column["name"] for column in inspector.get_columns(table.name)}
        expected_columns = {column.name for column in table.columns}
        missing_columns = sorted(expected_columns - actual_columns)
        unexpected_columns = sorted(actual_columns - expected_columns)
        if not unexpected_columns and _apply_compatible_additive_columns(engine, table.name, missing_columns):
            continue
        if actual_columns == expected_columns:
            continue

        details: list[str] = []
        if missing_columns:
            details.append(f"missing columns: {', '.join(missing_columns)}")
        if unexpected_columns:
            details.append(f"unexpected columns: {', '.join(unexpected_columns)}")
        detail_suffix = "; ".join(details)
        raise RuntimeError(
            f"Unsupported shogidb schema detected for table '{table.name}': {detail_suffix}. "
            "Delete and recreate the DB so it matches the canonical store schema."
        )


__all__ = ["ensure_canonical_store_schema"]
