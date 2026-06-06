"""SQLite-backed reader for offline result summaries."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select

from shogiarena._core.contexts.game_session.domain.result_summary_models import ResultSummaryGameRow
from shogiarena._core.platform.db.store.entities import Game, Player
from shogiarena._core.platform.db.store.repository_factory import SQLiteShogiDBFactory
from shogiarena._core.shared.kernel.game_results import parse_game_result_name


class SQLiteResultSummaryReader:
    """`game.db` から完了済み対局行を読み取る。"""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path

    def read_games(self) -> tuple[ResultSummaryGameRow, ...]:
        repository = SQLiteShogiDBFactory(self._db_path).create()
        try:
            black_player = Player.__table__.alias("black_player")
            white_player = Player.__table__.alias("white_player")
            query = (
                select(
                    Game.game_name,
                    black_player.c.player_name,
                    white_player.c.player_name,
                    Game.game_result,
                )
                .join(black_player, Game.black_player_id == black_player.c.id)
                .join(white_player, Game.white_player_id == white_player.c.id)
                .order_by(Game.id.asc())
            )
            rows: list[ResultSummaryGameRow] = []
            for game_name, black_name, white_name, raw_result in repository.session.execute(query).all():
                raw_result_text = str(raw_result)
                rows.append(
                    ResultSummaryGameRow(
                        game_name=str(game_name),
                        black_player=str(black_name),
                        white_player=str(white_name),
                        result=parse_game_result_name(raw_result_text),
                        raw_result=raw_result_text,
                    )
                )
            return tuple(rows)
        finally:
            repository.close_db()


__all__ = ["SQLiteResultSummaryReader"]
