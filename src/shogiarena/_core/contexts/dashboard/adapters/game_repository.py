"""Database helpers for dashboard game-oriented APIs."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path

import rsshogi
from sqlalchemy import func, or_, select
from sqlalchemy.orm import aliased

from shogiarena._core.contexts.dashboard.adapters.db_repository import open_dashboard_repository
from shogiarena._core.contexts.dashboard.application.tournament.payload_signatures import (
    current_timestamp_iso,
    hash_games_payload,
)
from shogiarena._core.platform.db.store.entities import Game, Player
from shogiarena._core.platform.db.store.record_store import DBRecordStore
from shogiarena._core.shared.kernel.game_record_types import GameRecordEnginesDict
from shogiarena._core.shared.kernel.json_types import JsonValue
from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_game_result


def _server_game_id(raw_attributes: str | None) -> str | None:
    if not raw_attributes:
        return None
    try:
        attributes = json.loads(raw_attributes)
    except (TypeError, ValueError):
        return None
    if not isinstance(attributes, dict):
        return None
    value = attributes.get("csa_server_game_id")
    return value.strip() if isinstance(value, str) and value.strip() else None


def load_games_for_dashboard(
    db_path: Path,
    *,
    game_type: str = "arena",
    immutable: bool = False,
) -> list[GameRecordEnginesDict]:
    """Load game rows in the same shape expected by dashboard endpoints."""

    repository = open_dashboard_repository(db_path, immutable=immutable)
    if repository is None:
        return []
    try:
        session = repository.session
        black_player = aliased(Player)
        white_player = aliased(Player)
        stmt = (
            select(
                Game.game_name,
                black_player.player_name.label("black"),
                white_player.player_name.label("white"),
                Game.game_result,
                Game.initial_position_sfen,
            )
            .join(black_player, Game.black_player_id == black_player.id)
            .join(white_player, Game.white_player_id == white_player.id)
            .where(Game.game_type == game_type)
            .order_by(Game.id.asc())
        )
        rows = session.execute(stmt)
        records: list[GameRecordEnginesDict] = []
        for game_name, black_engine, white_engine, raw_result, initial_sfen in rows:
            game_result = coerce_game_result(raw_result, is_strict=True)
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
    finally:
        repository.close_db()


def load_game_record(
    db_path: Path,
    *,
    game_name: str | None = None,
    game_id: int | None = None,
    immutable: bool = False,
) -> rsshogi.record.Record | None:
    """Load a single game record from the dashboard DB."""

    repository = open_dashboard_repository(db_path, immutable=immutable)
    if repository is None:
        return None
    try:
        return DBRecordStore(repository).load(game_name=game_name, game_id=game_id)
    finally:
        repository.close_db()


def build_games_list_raw_payload(
    db_path: Path,
    *,
    limit: int,
    offset: int,
    search_query: str | None,
    immutable: bool = False,
) -> dict[str, object]:
    """Build the raw tournament games payload from the dashboard DB."""

    repository = open_dashboard_repository(db_path, immutable=immutable)
    if repository is None:
        return {"games": [], "total": 0, "offset": offset, "limit": limit}
    try:
        session = repository.session
        black_player = aliased(Player)
        white_player = aliased(Player)

        count_stmt = (
            select(func.count())
            .select_from(Game)
            .join(black_player, Game.black_player)
            .join(white_player, Game.white_player)
        )
        if search_query:
            like = f"%{search_query}%"
            count_stmt = count_stmt.where(
                or_(
                    black_player.player_name.like(like),
                    white_player.player_name.like(like),
                    Game.game_name.like(like),
                )
            )

        total = session.execute(count_stmt).scalar_one()

        data_stmt = (
            select(
                Game.game_name,
                black_player.player_name.label("black_player"),
                white_player.player_name.label("white_player"),
                Game.game_result,
                Game.num_moves,
                Game.start_date,
                Game.end_date,
                Game.initial_position_sfen,
                Game.time_control_black,
                Game.time_control_white,
                Game.metadata_attributes_json,
            )
            .join(black_player, Game.black_player)
            .join(white_player, Game.white_player)
        )
        if search_query:
            like = f"%{search_query}%"
            data_stmt = data_stmt.where(
                or_(
                    black_player.player_name.like(like),
                    white_player.player_name.like(like),
                    Game.game_name.like(like),
                )
            )
        data_stmt = data_stmt.order_by(Game.start_date.desc(), Game.id.desc()).limit(limit).offset(offset)

        rows = session.execute(data_stmt).all()
        games: list[dict[str, object]] = []
        for (
            game_name,
            row_black_player,
            row_white_player,
            game_result,
            num_moves,
            start_date,
            end_date,
            init_sfen,
            time_control_black,
            time_control_white,
            metadata_attributes_json,
        ) in rows:
            game_id = str(game_name)
            black_name = str(row_black_player) if row_black_player is not None else ""
            white_name = str(row_white_player) if row_white_player is not None else ""
            total_plies = int(num_moves) if isinstance(num_moves, int | float) else None
            games.append(
                {
                    "game_id": game_id,
                    "server_game_id": _server_game_id(metadata_attributes_json),
                    "black_player": black_name,
                    "white_player": white_name,
                    "game_result": str(game_result) if isinstance(game_result, str) else None,
                    "total_plies": total_plies,
                    "start_time": start_date.isoformat() if start_date else None,
                    "end_time": end_date.isoformat() if end_date else None,
                    "initial_sfen": str(init_sfen) if isinstance(init_sfen, str) else None,
                    "time_control_black": str(time_control_black) if isinstance(time_control_black, str) else None,
                    "time_control_white": str(time_control_white) if isinstance(time_control_white, str) else None,
                }
            )

        return {
            "games": games,
            "total": int(total),
            "offset": offset,
            "limit": limit,
        }
    finally:
        repository.close_db()


def build_match_history_raw_payload(
    db_path: Path,
    *,
    limit: int,
    offset: int,
    immutable: bool = False,
) -> dict[str, object]:
    """Build the raw tournament match history payload from the dashboard DB."""

    games_payload: list[dict[str, object]] = []
    total_count = 0
    repository = open_dashboard_repository(db_path, immutable=immutable)

    if repository is not None:
        try:
            session = repository.session
            black_player = aliased(Player)
            white_player = aliased(Player)

            count_stmt = select(func.count()).where(Game.game_type == "arena")
            total_count = int(session.execute(count_stmt).scalar_one())

            data_stmt = (
                select(
                    Game.game_name.label("game_id"),
                    black_player.player_name.label("black_player"),
                    white_player.player_name.label("white_player"),
                    Game.game_result,
                    Game.num_moves.label("total_plies"),
                    Game.end_date.label("end_time"),
                    Game.initial_position_sfen.label("initial_sfen"),
                    Game.time_control_black,
                    Game.time_control_white,
                )
                .join(black_player, Game.black_player)
                .join(white_player, Game.white_player)
                .where(Game.game_type == "arena")
                .order_by(Game.end_date.desc(), Game.id.desc())
                .limit(limit)
                .offset(offset)
            )

            rows = session.execute(data_stmt).all()
            for row in rows:
                games_payload.append(
                    _format_game_row(
                        {
                            "game_id": row.game_id,
                            "black_player": row.black_player,
                            "white_player": row.white_player,
                            "game_result": row.game_result,
                            "total_plies": row.total_plies,
                            "end_time": row.end_time,
                            "initial_sfen": row.initial_sfen,
                            "time_control_black": row.time_control_black,
                            "time_control_white": row.time_control_white,
                        }
                    )
                )
        finally:
            repository.close_db()

    signature = hash_games_payload(games_payload)
    source = "db" if repository is not None else "unavailable"

    return {
        "games": games_payload,
        "limit": limit,
        "offset": offset,
        "total": total_count,
        "signature": signature,
        "source": source,
        "fetched_at": current_timestamp_iso(),
    }


def _format_game_row(row: Mapping[str, JsonValue]) -> dict[str, object]:
    game_id_raw = row.get("game_id")
    game_id = str(game_id_raw) if game_id_raw is not None else ""
    black_raw = row.get("black_player")
    black_player = str(black_raw) if black_raw is not None else ""
    white_raw = row.get("white_player")
    white_player = str(white_raw) if white_raw is not None else ""
    end_time = row.get("end_time")
    if isinstance(end_time, datetime):
        end_time_iso = end_time.isoformat()
    elif isinstance(end_time, str):
        end_time_iso = end_time
    else:
        end_time_iso = None
    game_result_raw = row.get("game_result")
    total_plies_raw = row.get("total_plies")
    total_plies = int(total_plies_raw) if isinstance(total_plies_raw, int | float) else None
    initial_sfen_raw = row.get("initial_sfen")
    time_control_black_raw = row.get("time_control_black")
    time_control_white_raw = row.get("time_control_white")
    return {
        "game_id": game_id,
        "black_player": black_player,
        "white_player": white_player,
        "game_result": str(game_result_raw) if isinstance(game_result_raw, str) else None,
        "total_plies": total_plies,
        "end_time": end_time_iso,
        "initial_sfen": str(initial_sfen_raw) if isinstance(initial_sfen_raw, str) else None,
        "time_control_black": str(time_control_black_raw) if isinstance(time_control_black_raw, str) else None,
        "time_control_white": str(time_control_white_raw) if isinstance(time_control_white_raw, str) else None,
    }
