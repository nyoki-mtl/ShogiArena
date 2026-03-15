from __future__ import annotations

from typing import Protocol, runtime_checkable

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session
from sqlalchemy.orm.scoping import ScopedSession

from .entities import Base, ModelT, Player
from .schema_guard import ensure_canonical_store_schema


@runtime_checkable
class ShogiRepositoryPort(Protocol):
    @property
    def session(self) -> Session: ...

    @property
    def engine(self) -> Engine: ...

    def close_db(self) -> None: ...
    def create_tables(self) -> None: ...

    def get_record(
        self,
        table: type[ModelT],
        column: str,
        value: str,
        should_register: bool = False,
        **kwargs: object,
    ) -> tuple[ModelT | None, bool]: ...

    def get_player_from_player_name(
        self,
        player_name: str,
        game_type: str,
        should_register: bool = False,
    ) -> tuple[Player | None, bool]: ...


class ShogiRepository:
    """Repository facade over SQLAlchemy models."""

    def __init__(self, engine: Engine, session_factory: ScopedSession[Session]) -> None:
        self._engine = engine
        self._session_factory = session_factory

    @property
    def session(self) -> Session:
        return self._session_factory()

    @property
    def engine(self) -> Engine:
        return self._engine

    def close_db(self) -> None:
        """Dispose the current scoped session."""

        self._session_factory.remove()

    def create_tables(self) -> None:
        ensure_canonical_store_schema(self._engine)
        Base.metadata.create_all(self._engine)
        self.session.commit()

    def get_record(
        self,
        table: type[ModelT],
        column: str,
        value: str,
        should_register: bool = False,
        **kwargs: object,
    ) -> tuple[ModelT | None, bool]:
        response = self.session.execute(select(table).where(getattr(table, column) == value)).fetchone()
        if response is None:
            if not should_register:
                return None, False
            record = table(**{column: value}, **kwargs)
            self.session.add(record)
            return record, True
        (record,) = response
        return record, False

    def get_player_from_player_name(
        self,
        player_name: str,
        game_type: str,
        should_register: bool = False,
    ) -> tuple[Player | None, bool]:
        player, is_new = self.get_record(
            Player,
            "player_name",
            player_name,
            should_register=should_register,
            game_type=game_type,
        )
        return player, is_new


__all__ = ["ShogiRepository", "ShogiRepositoryPort"]
