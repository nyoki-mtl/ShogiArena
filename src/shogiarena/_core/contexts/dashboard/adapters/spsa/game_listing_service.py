"""SPSA game listing service for dashboard game queries."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import cast

from sqlalchemy import func, or_, select
from sqlalchemy.orm import aliased

from shogiarena._core.contexts.dashboard.adapters.db_repository import open_dashboard_repository
from shogiarena._core.contexts.dashboard.application.spsa.variant_resolution import (
    extract_variant_from_game_id,
    resolve_variant_id,
)
from shogiarena._core.contexts.dashboard.ports.spsa_payloads import GameListEntry
from shogiarena._core.contexts.dashboard.ports.spsa_service_ports import DashboardSpsaUpdateQueryPort
from shogiarena._core.contexts.spsa.application.dashboard.game_listing import (
    build_games_from_db_rows,
    build_games_from_event_entries,
    deduplicate_game_id_entries,
)
from shogiarena._core.platform.db.store.entities import Game, Player
from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_int

logger = logging.getLogger(__name__)


class SpsaGameListingService:
    """Service for listing SPSA games from database or event fallback."""

    def __init__(
        self,
        *,
        db_path: Path,
        update_query_service: DashboardSpsaUpdateQueryPort,
    ) -> None:
        self._db_path = db_path
        self._update_query = update_query_service

    def list_games(
        self,
        offset: int,
        limit: int,
        search_query: str,
    ) -> tuple[list[GameListEntry], int]:
        repository = open_dashboard_repository(self._db_path)
        if repository is not None:
            try:
                session = repository.session
                Black = aliased(Player)
                White = aliased(Player)

                count_stmt = (
                    select(func.count())
                    .select_from(Game)
                    .join(Black, Game.black_player)
                    .join(White, Game.white_player)
                    .where(Game.game_type == "spsa")
                )
                if search_query:
                    like = f"%{search_query}%"
                    count_stmt = count_stmt.where(
                        or_(
                            Game.game_name.like(like),
                            Black.player_name.like(like),
                            White.player_name.like(like),
                        )
                    )

                total = session.execute(count_stmt).scalar_one()
                if total:
                    data_stmt = (
                        select(
                            Game.game_name,
                            Black.player_name.label("black_player"),
                            White.player_name.label("white_player"),
                            Game.game_result,
                            Game.num_moves,
                            Game.end_date,
                            Game.init_position_sfen,
                            Game.time_control_black,
                            Game.time_control_white,
                        )
                        .join(Black, Game.black_player)
                        .join(White, Game.white_player)
                        .where(Game.game_type == "spsa")
                    )
                    if search_query:
                        like = f"%{search_query}%"
                        data_stmt = data_stmt.where(
                            or_(
                                Game.game_name.like(like),
                                Black.player_name.like(like),
                                White.player_name.like(like),
                            )
                        )
                    data_stmt = (
                        data_stmt.order_by(Game.end_date.desc().nullslast(), Game.id.desc()).limit(limit).offset(offset)
                    )

                    rows = session.execute(data_stmt).all()
                    games = build_games_from_db_rows(
                        rows=rows,
                        game_snapshot_loader=lambda gid: self._update_query.get_game_event_snapshot(gid),
                        resolve_variant_id=resolve_variant_id,
                        extract_variant_from_game_id=extract_variant_from_game_id,
                    )
                    return cast(list[GameListEntry], games), coerce_int(total) or 0
            finally:
                repository.close_db()

        # Database unavailable or empty -> fall back to events
        game_entries = self._update_query.collect_game_id_entries()
        if not game_entries:
            return [], 0

        ordered_ids = deduplicate_game_id_entries(game_entries)
        records = build_games_from_event_entries(
            ordered_ids=ordered_ids,
            search_query=search_query,
            game_snapshot_loader=lambda gid: self._update_query.get_game_event_snapshot(gid),
            resolve_variant_id=resolve_variant_id,
            extract_variant_from_game_id=extract_variant_from_game_id,
        )

        records.sort(key=lambda item: item.get("timestamp") or 0, reverse=True)
        total = len(records)
        paginated = records[offset : offset + limit]
        for item in paginated:
            item.pop("timestamp", None)

        return cast(list[GameListEntry], paginated), total
