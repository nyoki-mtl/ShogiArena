from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, SmallInteger, String, Text, func
from sqlalchemy.dialects.mysql import SMALLINT, TINYINT
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


@compiles(TINYINT, "sqlite")
@compiles(SMALLINT, "sqlite")
def compile_sqlite_tinyint(type_: Any, compiler: Any, **kw: Any) -> str:
    return "SMALLINT"


class Base(DeclarativeBase):
    __table_args__ = {
        "mysql_engine": "InnoDB",
        "mysql_charset": "utf8mb4",
        "mysql_collate": "utf8mb4_bin",
    }


class Player(Base):
    __tablename__ = "player"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    game_type: Mapped[str] = mapped_column(String(16), nullable=False)
    player_name: Mapped[str] = mapped_column(String(64), nullable=False)

    def __repr__(self) -> str:
        return f"Player(id={self.id}, game_type={self.game_type}, player_name={self.player_name})"


class Game(Base):
    __tablename__ = "game"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    game_type: Mapped[str] = mapped_column(String(16), nullable=False)
    game_name: Mapped[str] = mapped_column(String(128), nullable=False)
    start_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    end_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    result_code: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    num_moves: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    black_player_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("player.id", onupdate="RESTRICT", ondelete="RESTRICT"), nullable=False
    )
    white_player_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("player.id", onupdate="RESTRICT", ondelete="RESTRICT"), nullable=False
    )
    time_control_black: Mapped[str | None] = mapped_column(String(128), nullable=True)
    time_control_white: Mapped[str | None] = mapped_column(String(128), nullable=True)
    init_position_sfen: Mapped[str] = mapped_column(String(144), nullable=False)
    updated_date: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    black_player: Mapped[Player] = relationship("Player", foreign_keys=[black_player_id])
    white_player: Mapped[Player] = relationship("Player", foreign_keys=[white_player_id])
    kifu: Mapped[list[Kifu]] = relationship(back_populates="game")

    def __repr__(self) -> str:
        return (
            "Game("
            f"id={self.id}, game_type={self.game_type}, game_name={self.game_name}, "
            f"start_date={self.start_date}, end_date={self.end_date}, "
            f"result_code={self.result_code}, num_moves={self.num_moves}, "
            f"black_player_id={self.black_player_id}, white_player_id={self.white_player_id}, "
            f"time_control_black={self.time_control_black}, time_control_white={self.time_control_white}, "
            f"init_position_sfen={self.init_position_sfen}, updated_date={self.updated_date})"
        )


class Kifu(Base):
    __tablename__ = "kifu"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    game_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("game.id", onupdate="RESTRICT", ondelete="CASCADE"), nullable=False
    )

    ply: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    next_move: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    next_move_time_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    wall_time_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_delta_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    next_move_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    eval: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    depth: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    seldepth: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    nodes: Mapped[int | None] = mapped_column(Integer, nullable=True)

    game: Mapped[Game] = relationship(back_populates="kifu")

    def __repr__(self) -> str:
        return (
            "Kifu("
            f"id={self.id}, game_id={self.game_id}, ply={self.ply}, next_move={self.next_move}, "
            f"next_move_time_ms={self.next_move_time_ms}, next_move_comment={self.next_move_comment}, "
            f"eval={self.eval})"
        )


Index("game_name_index", Game.game_name, unique=True)
Index("game_id_index", Kifu.game_id)


class EngineArtifact(Base):
    __tablename__ = "engine_artifact"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    logical_name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    artifact: Mapped[str | None] = mapped_column(String(256), nullable=True)
    binary_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    build_flags: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:
        return (
            "EngineArtifact("
            f"id={self.id}, logical_name={self.logical_name}, artifact={self.artifact}, "
            f"binary_path={self.binary_path})"
        )


class InstanceSpec(Base):
    __tablename__ = "instance_spec"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    instance_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    display_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    host_label: Mapped[str | None] = mapped_column(String(128), nullable=True)
    cpu_model: Mapped[str | None] = mapped_column(String(256), nullable=True)
    cpu_arch: Mapped[str | None] = mapped_column(String(64), nullable=True)
    cpu_cores: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    cpu_threads: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    memory_total_mb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    os_info: Mapped[str | None] = mapped_column(String(128), nullable=True)
    gpu_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    gpu_vendor: Mapped[str | None] = mapped_column(String(64), nullable=True)
    gpu_vram_mb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    gpu_count: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    tags: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    instance_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    extra: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:
        return f"InstanceSpec(id={self.id}, instance_id={self.instance_id}, display_name={self.display_name})"


class GameInstanceParticipation(Base):
    __tablename__ = "game_instance_participation"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    game_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("game.id", onupdate="CASCADE", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    engine_name: Mapped[str] = mapped_column(String(128), nullable=False)
    engine_display_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    engine_artifact_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("engine_artifact.id", onupdate="SET NULL", ondelete="SET NULL"), nullable=True
    )
    instance_id: Mapped[str | None] = mapped_column(
        String(128), ForeignKey("instance_spec.instance_id", onupdate="SET NULL", ondelete="SET NULL"), nullable=True
    )
    instance_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    binary_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    build_flags: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    run_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    extra: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    engine_artifact = relationship("EngineArtifact")
    instance_spec = relationship(
        "InstanceSpec", primaryjoin="InstanceSpec.instance_id==GameInstanceParticipation.instance_id"
    )
    game = relationship("Game")

    def __repr__(self) -> str:
        return (
            "GameInstanceParticipation("
            f"id={self.id}, game_id={self.game_id}, role={self.role}, engine={self.engine_name})"
        )


Index("gip_game_role_idx", GameInstanceParticipation.game_id, GameInstanceParticipation.role, unique=True)


__all__ = [
    "Base",
    "Player",
    "Game",
    "Kifu",
    "EngineArtifact",
    "InstanceSpec",
    "GameInstanceParticipation",
]
