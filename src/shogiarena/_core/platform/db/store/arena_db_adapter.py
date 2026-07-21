from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC
from types import TracebackType

import rsshogi
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session, aliased

from shogiarena._core.shared.kernel.game_results import GameResult
from shogiarena._core.shared.kernel.json_coercion import to_json_object
from shogiarena._core.shared.kernel.json_types import JsonValue
from shogiarena._core.shared.kernel.participation_records import (
    EngineArtifactSnapshot,
    GameParticipationRecord,
    InstanceSnapshot,
)
from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_game_result
from shogiarena._core.shared.kernel.service_ports import GameRecordPlayers

from .entities import EngineArtifact, Game, GameInstanceParticipation, InstanceSpec, Player
from .record_store import DBRecordStore
from .repository import ShogiRepository
from .repository_factory import BaseFactory


class ArenaDBAdapter:
    """Service layer for DB operations used by run orchestration."""

    def __init__(self, factory: BaseFactory) -> None:
        self.factory = factory
        self._db: ShogiRepository | None = None
        self._record_store: DBRecordStore | None = None

    def _get_db(self) -> ShogiRepository:
        if self._db is None:
            self._db = self.factory.create()
            self._db.create_tables()
        return self._db

    def _get_record_store(self) -> DBRecordStore:
        if self._record_store is None:
            self._record_store = DBRecordStore(self._get_db())
        return self._record_store

    @staticmethod
    def _coerce_game_result(raw: GameResult | JsonValue | None) -> GameResult:
        return coerce_game_result(raw, is_strict=True)

    def get_games_with_players(self, *, game_type: str) -> list[GameRecordPlayers]:
        db = self._get_db()
        with db.operation() as session:
            black_player = aliased(Player)
            white_player = aliased(Player)

            stmt = (
                select(
                    Game.id,
                    Game.game_name,
                    black_player.player_name.label("black_player"),
                    white_player.player_name.label("white_player"),
                    Game.game_result,
                    Game.initial_position_sfen,
                )
                .join(black_player, Game.black_player_id == black_player.id)
                .join(white_player, Game.white_player_id == white_player.id)
                .where(Game.game_type == game_type)
                .order_by(Game.id.asc())
            )
            result = session.execute(stmt)

            games: list[GameRecordPlayers] = []
            for game_id, game_name, black_name, white_name, raw_result, initial_sfen in result:
                game_result = self._coerce_game_result(raw_result)
                games.append(
                    {
                        "game_id": game_id,
                        "game_name": game_name,
                        "black_player": black_name,
                        "white_player": white_name,
                        "result": game_result,
                        "initial_sfen": initial_sfen,
                    }
                )
            return games

    def ensure_schema(self) -> None:
        """Create the database tables if they do not yet exist."""
        db = self._get_db()
        db.create_tables()

    def get_game_id_by_name(self, game_name: str) -> int | None:
        db = self._get_db()
        stmt = select(Game.id).where(Game.game_name == game_name)
        with db.operation() as session:
            return session.execute(stmt).scalar_one_or_none()

    def load_record(
        self,
        *,
        game_id: int | None = None,
        game_name: str | None = None,
    ) -> rsshogi.record.Record | None:
        return self._get_record_store().load(game_id=game_id, game_name=game_name)

    def append_record_list(
        self,
        record_list: Iterable[rsshogi.record.Record | None],
        *,
        should_update: bool = False,
    ) -> None:
        self._get_record_store().append(record_list, should_update=should_update)

    def upsert_engine_artifact(self, snapshot: EngineArtifactSnapshot | None) -> EngineArtifact | None:
        if snapshot is None:
            return None
        db = self._get_db()
        with db.operation(commit=True) as session:
            return self._upsert_engine_artifact(session, snapshot)

    @staticmethod
    def _upsert_engine_artifact(session: Session, snapshot: EngineArtifactSnapshot) -> EngineArtifact:
        existing = session.execute(
            select(EngineArtifact).where(EngineArtifact.logical_name == snapshot.logical_name)
        ).scalar_one_or_none()
        build_flags = to_json_object(snapshot.build_flags) if snapshot.build_flags is not None else None
        metadata_payload = to_json_object(snapshot.metadata) if snapshot.metadata is not None else None
        if existing is None:
            entity = EngineArtifact(
                logical_name=snapshot.logical_name,
                artifact=snapshot.artifact,
                binary_path=snapshot.binary_path,
                build_flags=build_flags,
                metadata_json=metadata_payload,
            )
            session.add(entity)
            session.flush()
            return entity

        existing.artifact = snapshot.artifact
        existing.binary_path = snapshot.binary_path
        existing.build_flags = build_flags
        existing.metadata_json = metadata_payload
        return existing

    def upsert_instance_spec(self, snapshot: InstanceSnapshot | None) -> InstanceSpec | None:
        if snapshot is None:
            return None
        db = self._get_db()
        with db.operation(commit=True) as session:
            return self._upsert_instance_spec(session, snapshot)

    @staticmethod
    def _upsert_instance_spec(session: Session, snapshot: InstanceSnapshot) -> InstanceSpec:
        existing = session.execute(
            select(InstanceSpec).where(InstanceSpec.instance_id == snapshot.instance_id)
        ).scalar_one_or_none()

        tags = list(snapshot.tags) if snapshot.tags else None
        extra = to_json_object(snapshot.extra) if snapshot.extra is not None else None

        def _apply(entity: InstanceSpec) -> None:
            entity.display_name = snapshot.display_name
            entity.host_label = snapshot.host_label
            entity.cpu_model = snapshot.cpu_model
            entity.cpu_arch = snapshot.cpu_arch
            entity.cpu_cores = snapshot.cpu_cores
            entity.cpu_threads = snapshot.cpu_threads
            entity.memory_total_mb = snapshot.memory_total_mb
            entity.os_info = snapshot.os_info
            entity.gpu_model = snapshot.gpu_model
            entity.gpu_vendor = snapshot.gpu_vendor
            entity.gpu_vram_mb = snapshot.gpu_vram_mb
            entity.gpu_count = snapshot.gpu_count
            entity.instance_type = snapshot.instance_type
            entity.tags = tags
            entity.extra = extra

        if existing is None:
            entity = InstanceSpec(instance_id=snapshot.instance_id)
            _apply(entity)
            session.add(entity)
            session.flush()
            return entity

        _apply(existing)
        return existing

    def record_game_participation(self, *, game_id: int, participation: Iterable[object]) -> None:
        db = self._get_db()
        with db.operation(commit=True) as session:
            self._record_game_participation(session, game_id=game_id, participation=participation)

    def _record_game_participation(
        self,
        session: Session,
        *,
        game_id: int,
        participation: Iterable[object],
    ) -> None:
        for raw_record in participation:
            if isinstance(raw_record, GameParticipationRecord):
                record = raw_record
            else:
                try:
                    record = GameParticipationRecord.model_validate(raw_record)
                except ValidationError as exc:
                    raise TypeError("participation entries must be GameParticipationRecord-compatible") from exc

            artifact_entity = (
                self._upsert_engine_artifact(session, record.engine_artifact)
                if record.engine_artifact is not None
                else None
            )
            if record.instance is not None:
                self._upsert_instance_spec(session, record.instance)
            instance_name = None
            instance_id = None
            if record.instance is not None:
                instance_name = record.instance.display_name or record.instance.instance_id
                instance_id = record.instance.instance_id
            build_flags = to_json_object(record.build_flags) if record.build_flags is not None else None
            extra = to_json_object(record.extra) if record.extra is not None else None
            existing = session.execute(
                select(GameInstanceParticipation)
                .where(GameInstanceParticipation.game_id == game_id)
                .where(GameInstanceParticipation.role == record.role)
            ).scalar_one_or_none()

            started_at = record.started_at
            completed_at = record.completed_at
            if started_at is not None and started_at.tzinfo is not None:
                started_at = started_at.astimezone(UTC).replace(tzinfo=None)
            if completed_at is not None and completed_at.tzinfo is not None:
                completed_at = completed_at.astimezone(UTC).replace(tzinfo=None)

            if existing is None:
                entity = GameInstanceParticipation(
                    game_id=game_id,
                    role=record.role,
                    engine_name=record.engine_name,
                    engine_display_name=record.engine_display_name,
                    engine_artifact_id=artifact_entity.id if artifact_entity else None,
                    instance_id=instance_id,
                    instance_name=instance_name,
                    binary_path=record.binary_path,
                    build_flags=build_flags,
                    started_at=started_at,
                    completed_at=completed_at,
                    run_id=record.run_id,
                    extra=extra,
                )
                session.add(entity)
            else:
                existing.engine_name = record.engine_name
                existing.engine_display_name = record.engine_display_name
                existing.engine_artifact_id = artifact_entity.id if artifact_entity else None
                existing.instance_id = instance_id
                existing.instance_name = instance_name
                existing.binary_path = record.binary_path
                existing.build_flags = build_flags
                existing.started_at = started_at
                existing.completed_at = completed_at
                existing.run_id = record.run_id
                existing.extra = extra

    def close(self) -> None:
        if self._db is not None:
            self._db.close_db()
            self._db = None

    def __enter__(self) -> ArenaDBAdapter:
        return self

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc_val: BaseException | None,
        _exc_tb: TracebackType | None,
    ) -> None:
        self.close()


__all__ = ["ArenaDBAdapter"]
