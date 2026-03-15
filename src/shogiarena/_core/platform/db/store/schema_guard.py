from __future__ import annotations

from sqlalchemy import inspect
from sqlalchemy.engine import Engine


def ensure_canonical_store_schema(engine: Engine) -> None:
    """Reject existing managed tables whose schema diverges from canonical metadata."""

    from .entities import Base

    inspector = inspect(engine)
    for table in Base.metadata.sorted_tables:
        if not inspector.has_table(table.name):
            continue

        actual_columns = {column["name"] for column in inspector.get_columns(table.name)}
        expected_columns = {column.name for column in table.columns}
        if actual_columns == expected_columns:
            continue

        missing_columns = sorted(expected_columns - actual_columns)
        unexpected_columns = sorted(actual_columns - expected_columns)
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
