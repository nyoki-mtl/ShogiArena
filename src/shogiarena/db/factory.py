from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, scoped_session, sessionmaker
from sqlalchemy.orm.scoping import ScopedSession

from .repository import ShogiRepository


class BaseFactory:
    def __init__(self, engine: Engine, session_factory: ScopedSession[Session]) -> None:
        self._engine = engine
        self._session_factory = session_factory

    @property
    def engine(self) -> Engine:
        return self._engine

    @property
    def session_factory(self) -> ScopedSession[Session]:
        return self._session_factory

    def create(self) -> ShogiRepository:
        return ShogiRepository(self._engine, self._session_factory)


class SQLiteShogiDBFactory(BaseFactory):
    def __init__(self, db_path: str | Path = ":memory:", echo: bool = False) -> None:
        db_path_str = db_path.as_posix() if isinstance(db_path, Path) else db_path
        engine = create_engine(f"sqlite+pysqlite:///{db_path_str}", echo=echo)
        session_factory = scoped_session(sessionmaker(autocommit=False, autoflush=True, bind=engine))
        super().__init__(engine, session_factory)


__all__ = ["BaseFactory", "SQLiteShogiDBFactory"]
