from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from typing import Protocol, runtime_checkable

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session
from sqlalchemy.orm.scoping import ScopedSession

from .entities import ModelT, Player
from .schema_guard import ensure_store_schema_for_query, initialize_store_schema


@runtime_checkable
class ShogiRepositoryPort(Protocol):
    @property
    def session(self) -> Session: ...

    @property
    def engine(self) -> Engine: ...

    def operation(self, *, commit: bool = False) -> AbstractContextManager[Session]: ...
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

    def __init__(
        self,
        engine: Engine,
        session_factory: ScopedSession[Session],
        *,
        is_read_only: bool = False,
    ) -> None:
        self._engine = engine
        self._session_factory = session_factory
        self._is_read_only = is_read_only
        self._is_schema_ready = False
        self._open_operation_sessions: set[Session] = set()
        self._operation_guard_lock = threading.Lock()
        self._is_closed = False

    def _ensure_open(self) -> None:
        if self._is_closed:
            raise RuntimeError("ShogiRepository is closed")

    def _ensure_schema_ready(self) -> None:
        self._ensure_open()
        if not self._is_schema_ready:
            ensure_store_schema_for_query(
                self._engine,
                should_stamp_schema_version=not self._is_read_only,
            )
            self._is_schema_ready = True

    @property
    def session(self) -> Session:
        self._ensure_schema_ready()
        return self._session_factory()

    @property
    def engine(self) -> Engine:
        self._ensure_schema_ready()
        return self._engine

    @contextmanager
    def operation(self, *, commit: bool = False) -> Iterator[Session]:
        """Provide an isolated session boundary for one repository operation.

        The scoped session is removed even when callers run in short-lived
        asyncio tasks.  This prevents a later task whose object id is reused
        from inheriting pending or failed transaction state.

        入れ子にはできない。scoped session なので内側は外側と同じ Session を返し、
        内側の commit が外側の未確定分まで確定させ、内側の remove が外側の
        commit / rollback を no-op にする。原子性が静かに壊れるため、fail fast にする。

        入れ子の判定は yield する Session そのもので行う。scoped session は
        thread / asyncio task ごとに別の Session を返すので、この単位で見れば
        「同じ Session を二重に開いた」ときだけ検出できる。repository を共有する
        別スレッドからの同時呼び出しは、そもそも別の Session なので影響しない。
        """

        self._ensure_schema_ready()
        session = self._session_factory()
        with self._operation_guard_lock:
            if session in self._open_operation_sessions:
                raise RuntimeError(
                    "ShogiRepository.operation() cannot be nested: the inner boundary would commit the outer "
                    "transaction and silence the outer rollback. Pass the yielded session down instead."
                )
            self._open_operation_sessions.add(session)
        try:
            yield session
            if commit:
                session.commit()
        except BaseException:
            session.rollback()
            raise
        finally:
            with self._operation_guard_lock:
                self._open_operation_sessions.discard(session)
            self._session_factory.remove()

    def close_db(self) -> None:
        """現在の session と connection pool を閉じ、repository を終端状態にする。"""

        if self._is_closed:
            return
        self._is_closed = True
        self._session_factory.remove()
        self._engine.dispose()

    def create_tables(self) -> None:
        self._ensure_open()
        initialize_store_schema(self._engine)
        self._is_schema_ready = True

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
