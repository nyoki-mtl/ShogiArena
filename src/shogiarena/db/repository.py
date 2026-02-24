from __future__ import annotations

from typing import TypeVar

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session
from sqlalchemy.orm.scoping import ScopedSession

from shogiarena.db.models import Base, Player

ModelT = TypeVar("ModelT", bound=Base)


class ShogiRepository:
    """Repository facade over the arena database models."""

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
        # Fresh create; no migration/compat logic is kept.
        Base.metadata.create_all(self._engine)
        self.session.commit()

    def get_record(
        self, table: type[ModelT], column: str, value: str, register: bool = False, **kwargs: object
    ) -> tuple[ModelT | None, bool]:
        """Retrieve or optionally create a record using a unique column."""
        response = self.session.execute(select(table).where(getattr(table, column) == value)).fetchone()
        if response is None:
            if not register:
                return None, False
            record = table(**{column: value}, **kwargs)
            self.session.add(record)
            return record, True
        else:
            (record,) = response
            return record, False

    def get_player_from_player_name(
        self, player_name: str, game_type: str, register: bool = False
    ) -> tuple[Player | None, bool]:
        player, is_new = self.get_record(Player, "player_name", player_name, register=register, game_type=game_type)
        return player, is_new
