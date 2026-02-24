"""Database service for arena async operations using ShogiRepository ORM."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime, timezone
from typing import TypeAlias

import rshogi
from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import aliased

from shogiarena.arena.services.persistence.records import (
    EngineArtifactSnapshot,
    GameParticipationRecord,
    InstanceSnapshot,
)
from shogiarena.db import ShogiRepository
from shogiarena.db.factory import BaseFactory
from shogiarena.db.models import (
    EngineArtifact,
    Game,
    GameInstanceParticipation,
    InstanceSpec,
    Player,
)
from shogiarena.records.storage.db_store import DBRecordStore
from shogiarena.utils.types.coerce import coerce_game_result
from shogiarena.utils.types.types import (
    GameRecordEnginesDict,
    GameRecordPlayersDict,
    GameResult,
    JsonObject,
)

GameHistoryEntry: TypeAlias = JsonObject


class ArenaDBService:
    """Service layer for database operations in arena tournaments.

    Provides ORM-based access to game statistics, completed games,
    and rating data without direct SQLite usage.
    """

    def __init__(self, factory: BaseFactory) -> None:
        """Initialize database service.

        Args:
            factory: Database factory for creating sessions
        """
        self.factory = factory
        self._db: ShogiRepository | None = None
        self._record_store: DBRecordStore | None = None

    def _get_db(self) -> ShogiRepository:
        """Get database connection, creating if needed."""
        if self._db is None:
            self._db = self.factory.create()
            # Ensure tables exist
            self._db.create_tables()
        return self._db

    def _get_record_store(self) -> DBRecordStore:
        """DBRecordStore を提供するヘルパー。"""
        if self._record_store is None:
            self._record_store = DBRecordStore(self._get_db())
        return self._record_store

    @staticmethod
    def _coerce_game_result(raw: object) -> GameResult:
        return coerce_game_result(raw, strict=True)

    def get_game_result_counts(self, game_type: str = "arena") -> dict[GameResult, int]:
        """Get game result counts by result code.

        Args:
            game_type: Type of games to query

        Returns:
            Dictionary mapping result codes to counts
        """
        db = self._get_db()
        stmt = select(Game.result_code, func.count()).where(Game.game_type == game_type).group_by(Game.result_code)
        result = db.session.execute(stmt)
        counts: dict[GameResult, int] = {}
        for raw_code, total in result:
            game_result = self._coerce_game_result(raw_code)
            counts[game_result] = int(total)
        return counts

    def get_games_with_players(self, game_type: str = "arena") -> list[GameRecordPlayersDict]:
        """Get games with player information for rating calculations.

        Args:
            game_type: Type of games to query

        Returns:
            List of game records with player names and results
        """
        db = self._get_db()
        BlackPlayer = aliased(Player)
        WhitePlayer = aliased(Player)

        stmt = (
            select(
                Game.id,
                Game.game_name,
                BlackPlayer.player_name.label("black_player"),
                WhitePlayer.player_name.label("white_player"),
                Game.result_code,
                Game.init_position_sfen,
            )
            .join(BlackPlayer, Game.black_player_id == BlackPlayer.id)
            .join(WhitePlayer, Game.white_player_id == WhitePlayer.id)
            .where(Game.game_type == game_type)
            .order_by(Game.id.asc())
        )
        result = db.session.execute(stmt)

        games: list[GameRecordPlayersDict] = []
        for game_id, game_name, black_player, white_player, raw_result, initial_sfen in result:
            game_result = self._coerce_game_result(raw_result)
            games.append(
                {
                    "game_id": game_id,
                    "game_name": game_name,
                    "black_player": black_player,
                    "white_player": white_player,
                    "result": game_result,
                    "initial_sfen": initial_sfen,
                }
            )
        return games

    def get_all_games(
        self,
        game_type: str = "arena",
        *,
        offset: int = 0,
        limit: int | None = None,
        search_query: str | None = None,
        player_names: Sequence[str] | None = None,
        result_filter: GameResult | None = None,
    ) -> list[GameRecordEnginesDict]:
        """Return all games for dashboard APIs.

        Returns a list of dicts with keys:
          - black_engine: str
          - white_engine: str
          - result: GameResult
          - game_name: str
          - initial_sfen: str
        """
        db = self._get_db()
        BlackPlayer = aliased(Player)
        WhitePlayer = aliased(Player)
        stmt = (
            select(
                Game.game_name,
                BlackPlayer.player_name.label("black"),
                WhitePlayer.player_name.label("white"),
                Game.result_code,
                Game.init_position_sfen,
            )
            .join(BlackPlayer, Game.black_player_id == BlackPlayer.id)
            .join(WhitePlayer, Game.white_player_id == WhitePlayer.id)
            .where(Game.game_type == game_type)
            .order_by(Game.id.asc())
        )
        if offset < 0:
            raise ValueError("offset must be >= 0")
        if limit is not None and limit <= 0:
            raise ValueError("limit must be positive")
        if result_filter is not None:
            stmt = stmt.where(Game.result_code == result_filter.value)
        if player_names:
            names = [str(name) for name in player_names if str(name)]
            if names:
                stmt = stmt.where(or_(BlackPlayer.player_name.in_(names), WhitePlayer.player_name.in_(names)))
        if search_query:
            pattern = f"%{search_query}%"
            stmt = stmt.where(
                or_(
                    Game.game_name.ilike(pattern),
                    BlackPlayer.player_name.ilike(pattern),
                    WhitePlayer.player_name.ilike(pattern),
                )
            )
        if offset:
            stmt = stmt.offset(int(offset))
        if limit is not None:
            stmt = stmt.limit(int(limit))
        rows = db.session.execute(stmt)
        records: list[GameRecordEnginesDict] = []
        for game_name, black_engine, white_engine, raw_result, initial_sfen in rows:
            game_result = self._coerce_game_result(raw_result)
            records.append(
                {
                    "game_name": game_name,
                    "black_engine": black_engine,
                    "white_engine": white_engine,
                    "result": game_result,
                    "initial_sfen": initial_sfen,
                }
            )
        return records

    def ensure_schema_compatibility(self) -> None:
        """Ensure database schema exists and is ready for arena usage."""
        db = self._get_db()
        db.create_tables()

    def get_shogidb(self) -> ShogiRepository:
        """Expose the underlying ShogiRepository instance for read-heavy services."""

        return self._get_db()

    def get_game_id_by_name(self, game_name: str) -> int | None:
        """Return the database identifier for a stored game by name."""

        db = self._get_db()
        stmt = select(Game.id).where(Game.game_name == game_name)
        return db.session.execute(stmt).scalar_one_or_none()

    def get_color_statistics(self, game_type: str = "arena") -> dict[str, int]:
        """Get accurate color-based win statistics from database.

        Args:
            game_type: Type of games to query

        Returns:
            Dictionary with black_wins, white_wins, and draws counts
        """
        db = self._get_db()
        stats = {"black_wins": 0, "white_wins": 0, "draws": 0}

        stmt = select(Game.result_code).where(Game.game_type == game_type)
        result = db.session.execute(stmt)

        for (raw_result,) in result:
            gr = self._coerce_game_result(raw_result)
            if gr.is_black_win():
                stats["black_wins"] += 1
            elif gr.is_white_win():
                stats["white_wins"] += 1
            elif gr.is_draw():
                stats["draws"] += 1
        return stats

    def append_record_list(
        self, record_list: Iterable[rshogi.record.GameRecord | None], *, update: bool = False
    ) -> None:
        """Append list of record objects to database.

        Args:
            record_list: List of record objects to append
        """
        self._get_record_store().append(record_list, update=update)

    def delete_games_by_ids(self, game_ids: Iterable[int]) -> int:
        """Delete games by database identifiers.

        Returns:
            Number of deleted rows.
        """
        ids = [int(gid) for gid in game_ids]
        if not ids:
            return 0
        db = self._get_db()
        stmt = select(Game.id).where(Game.id.in_(ids))
        existing = db.session.execute(stmt).scalars().all()
        if not existing:
            return 0
        db.session.execute(delete(Game).where(Game.id.in_(existing)))
        db.session.commit()
        return len(existing)

    def delete_games_by_names(self, game_names: Iterable[str], *, game_type: str | None = None) -> int:
        """Delete games by game_name, optionally scoped to game_type."""
        names = [str(name) for name in game_names if str(name)]
        if not names:
            return 0
        db = self._get_db()
        stmt = select(Game.id).where(Game.game_name.in_(names))
        if game_type is not None:
            stmt = stmt.where(Game.game_type == game_type)
        existing = db.session.execute(stmt).scalars().all()
        if not existing:
            return 0
        db.session.execute(delete(Game).where(Game.id.in_(existing)))
        db.session.commit()
        return len(existing)

    # ------------------------------------------------------------------
    # Engine / instance participation helpers
    # ------------------------------------------------------------------

    def upsert_engine_artifact(self, snapshot: EngineArtifactSnapshot | None) -> EngineArtifact | None:
        """Insert or update engine artifact metadata and return the ORM entity."""

        if snapshot is None:
            return None
        db = self._get_db()
        session = db.session
        existing = session.execute(
            select(EngineArtifact).where(EngineArtifact.logical_name == snapshot.logical_name)
        ).scalar_one_or_none()
        build_flags = dict(snapshot.build_flags) if snapshot.build_flags is not None else None
        metadata_payload = dict(snapshot.metadata) if snapshot.metadata is not None else None
        if existing is None:
            entity = EngineArtifact(
                logical_name=snapshot.logical_name,
                artifact=snapshot.artifact,
                binary_path=snapshot.binary_path,
                build_flags=build_flags,
                metadata_json=metadata_payload,
            )
            session.add(entity)
            session.commit()
            session.refresh(entity)
            return entity

        existing.artifact = snapshot.artifact
        existing.binary_path = snapshot.binary_path
        existing.build_flags = build_flags
        existing.metadata_json = metadata_payload
        session.commit()
        return existing

    def upsert_instance_spec(self, snapshot: InstanceSnapshot | None) -> InstanceSpec | None:
        """Insert or update instance specification details and return the ORM entity."""

        if snapshot is None:
            return None
        db = self._get_db()
        session = db.session
        existing = session.execute(
            select(InstanceSpec).where(InstanceSpec.instance_id == snapshot.instance_id)
        ).scalar_one_or_none()

        tags = list(snapshot.tags) if snapshot.tags else None
        extra = dict(snapshot.extra) if snapshot.extra is not None else None

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
            session.commit()
            session.refresh(entity)
            return entity

        _apply(existing)
        session.commit()
        return existing

    def record_game_participation(
        self,
        *,
        game_id: int,
        participation: Iterable[GameParticipationRecord],
    ) -> None:
        """Persist per-role participation metadata for a completed game."""

        db = self._get_db()
        session = db.session
        for record in participation:
            artifact_entity = self.upsert_engine_artifact(record.engine_artifact)
            self.upsert_instance_spec(record.instance)
            instance_name = None
            instance_id = None
            if record.instance is not None:
                instance_name = record.instance.display_name or record.instance.instance_id
                instance_id = record.instance.instance_id
            build_flags = dict(record.build_flags) if record.build_flags is not None else None
            extra = dict(record.extra) if record.extra is not None else None
            existing = session.execute(
                select(GameInstanceParticipation)
                .where(GameInstanceParticipation.game_id == game_id)
                .where(GameInstanceParticipation.role == record.role)
            ).scalar_one_or_none()

            started_at = record.started_at
            completed_at = record.completed_at
            # Normalize datetimes to naive UTC for SQLite compatibility
            if started_at is not None and started_at.tzinfo is not None:
                started_at = started_at.astimezone(timezone.utc).replace(tzinfo=None)
            if completed_at is not None and completed_at.tzinfo is not None:
                completed_at = completed_at.astimezone(timezone.utc).replace(tzinfo=None)

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
        session.commit()

    def get_instance_game_history(
        self,
        instance_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> list[GameHistoryEntry]:
        """Return recent completed games for a given instance."""

        db = self._get_db()
        gip = GameInstanceParticipation
        BlackPlayer = aliased(Player)
        WhitePlayer = aliased(Player)
        stmt = (
            select(
                gip.game_id.label("game_id"),
                Game.game_name.label("game_name"),
                Game.result_code.label("result_code"),
                Game.start_date.label("start_date"),
                Game.end_date.label("end_date"),
                BlackPlayer.player_name.label("black_player"),
                WhitePlayer.player_name.label("white_player"),
                gip.role.label("role"),
                gip.engine_name.label("engine_name"),
                gip.engine_display_name.label("engine_display_name"),
                gip.binary_path.label("binary_path"),
                gip.build_flags.label("build_flags"),
                gip.started_at.label("started_at"),
                gip.completed_at.label("completed_at"),
                gip.run_id.label("run_id"),
                gip.extra.label("extra"),
                gip.instance_name.label("instance_name"),
                gip.engine_artifact_id.label("engine_artifact_id"),
            )
            .where(gip.instance_id == instance_id)
            .join(Game, Game.id == gip.game_id)
            .join(BlackPlayer, Game.black_player_id == BlackPlayer.id)
            .join(WhitePlayer, Game.white_player_id == WhitePlayer.id)
            .order_by(gip.completed_at.desc().nullslast(), gip.game_id.desc())
            .limit(max(1, limit) * 2)
            .offset(max(0, offset))
        )
        rows = db.session.execute(stmt).mappings().all()

        grouped: dict[int, GameHistoryEntry] = {}
        for row in rows:
            gid = int(row["game_id"])
            entry = grouped.setdefault(
                gid,
                {
                    "game_id": gid,
                    "game_name": row["game_name"],
                    "result_code": row["result_code"],
                    "start_date": row["start_date"],
                    "end_date": row["end_date"],
                    "black_engine": row["black_player"],
                    "white_engine": row["white_player"],
                    "run_id": row["run_id"],
                    "roles": [],
                    "started_at": None,
                    "completed_at": None,
                },
            )
            role_payload = {
                "role": row["role"],
                "engine_name": row["engine_name"],
                "engine_display_name": row["engine_display_name"],
                "binary_path": row["binary_path"],
                "build_flags": row["build_flags"],
                "started_at": row["started_at"],
                "completed_at": row["completed_at"],
                "extra": row["extra"],
                "instance_name": row["instance_name"],
                "engine_artifact_id": row["engine_artifact_id"],
            }
            roles_list = entry["roles"]
            if isinstance(roles_list, list):
                roles_list.append(role_payload)

            start_val = row["started_at"]
            if start_val is not None:
                current_start = entry["started_at"]
                if current_start is None or start_val < current_start:
                    entry["started_at"] = start_val
            end_val = row["completed_at"]
            if end_val is not None:
                current_end = entry["completed_at"]
                if current_end is None or end_val > current_end:
                    entry["completed_at"] = end_val

        def _sort_key(item: GameHistoryEntry) -> tuple[datetime, int]:
            completed = item.get("completed_at")
            if not isinstance(completed, datetime):
                completed = datetime.min
            game_id = item.get("game_id")
            return (completed, int(game_id) if isinstance(game_id, int | str) else 0)

        ordered = sorted(grouped.values(), key=_sort_key, reverse=True)
        return ordered[: max(1, limit)]

    def close(self) -> None:
        """Close database connections."""
        if self._db is not None:
            self._db.close_db()
            self._db = None

    def __enter__(self) -> ArenaDBService:
        """Context manager entry."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object | None,
    ) -> None:
        """Context manager exit."""
        self.close()
