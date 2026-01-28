from __future__ import annotations

from pathlib import Path

from omegaconf import OmegaConf
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, scoped_session, sessionmaker
from sqlalchemy.orm.scoping import ScopedSession

from .repository import ShogiDB


class BaseFactory:
    def __init__(self, engine: Engine, session_factory: ScopedSession[Session]):
        self._engine = engine
        self._session_factory = session_factory

    @property
    def engine(self) -> Engine:
        return self._engine

    @property
    def session_factory(self) -> ScopedSession[Session]:
        return self._session_factory

    def create(self) -> ShogiDB:
        return ShogiDB(self._engine, self._session_factory)


class SQLiteShogiDBFactory(BaseFactory):
    def __init__(self, db_path: str | Path = ":memory:", echo: bool = False):
        if isinstance(db_path, Path):
            db_path_str = db_path.as_posix()
        else:
            db_path_str = db_path
        engine = create_engine(f"sqlite+pysqlite:///{db_path_str}", echo=echo)
        session_factory = scoped_session(sessionmaker(autocommit=False, autoflush=True, bind=engine))
        super().__init__(engine, session_factory)


class LocalMySQLShogiDBFactory(BaseFactory):
    def __init__(self, inifile_path: str | Path, host: str = "localhost", echo: bool = False) -> None:
        inifile = OmegaConf.load(Path(inifile_path))
        db_user = inifile.DB_USER
        db_passwd = inifile.DB_PASS
        db_name = inifile.DB_NAME

        engine = create_engine(f"mysql://{db_user}:{db_passwd}@{host}/{db_name}", echo=echo)
        session_factory = scoped_session(sessionmaker(autocommit=False, autoflush=True, bind=engine))
        super().__init__(engine, session_factory)


__all__ = ["BaseFactory", "SQLiteShogiDBFactory", "LocalMySQLShogiDBFactory"]
