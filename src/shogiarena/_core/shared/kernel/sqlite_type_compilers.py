from __future__ import annotations

from sqlalchemy.dialects.mysql import SMALLINT, TINYINT
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.compiler import SQLCompiler
from sqlalchemy.sql.type_api import TypeEngine


@compiles(TINYINT, "sqlite")
@compiles(SMALLINT, "sqlite")
def compile_sqlite_tinyint(
    _type: TypeEngine,
    _compiler: SQLCompiler,
    **_kw: str | int | float | bool | None,
) -> str:
    return "SMALLINT"


def ensure_sqlite_type_compilers_registered() -> None:
    """Import-time registration hook for SQLite type compilers."""
    return


__all__ = ["ensure_sqlite_type_compilers_registered"]
