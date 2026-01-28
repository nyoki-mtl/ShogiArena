from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

import cshogi
from sqlalchemy import delete, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session
from sqlalchemy.orm.scoping import ScopedSession

from shogiarena.db.models import Base, Game, Kifu, Player
from shogiarena.records import GameInfo
from shogiarena.utils.common.constants import MOVE_END
from shogiarena.utils.types.types import GameResult

logger = logging.getLogger(__name__)


class ShogiDB:
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

    def execute_sql(self, sql: str, fetchone: bool = False) -> Any:
        """Execute raw SQL against the underlying engine."""

        response = self.session.execute(text(sql))
        if fetchone:
            return response.fetchone()
        else:
            return response

    def create_tables(self) -> None:
        # Fresh create; no migration/compat logic is kept.
        Base.metadata.create_all(self._engine)
        self.session.commit()

    def drop_table(self, table_name: str) -> None:
        self.execute_sql(f"DROP TABLE {table_name}")

    def get_record(
        self, table: Any, column: str, value: str, register: bool = False, **kwargs: Any
    ) -> tuple[Any, bool]:
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

    def get_player_from_player_name(self, player_name: str, game_type: str, register: bool = False) -> tuple[Any, bool]:
        player, is_new = self.get_record(Player, "player_name", player_name, register=register, game_type=game_type)
        return player, is_new

    def append_game_info_list(self, game_info_list: Iterable[GameInfo | None], update: bool = False) -> None:
        """Persist a sequence of game records, replacing older entries when requested."""
        board = cshogi.Board()

        for game_info in game_info_list:
            if game_info is None:
                continue

            if not game_info.moves:
                raise ValueError("GameInfo must contain at least one move entry")
            if game_info.moves[-1] != MOVE_END:
                raise ValueError("GameInfo.moves must terminate with MOVE_END")
            if game_info.game_name is None:
                raise ValueError("GameInfo.game_name must be defined")
            if game_info.game_type is None:
                raise ValueError("GameInfo.game_type must be defined")
            if game_info.black_player_name is None or game_info.white_player_name is None:
                raise ValueError("GameInfo player names must be defined")
            response = self.session.execute(select(Game.id).where(Game.game_name == game_info.game_name)).fetchone()
            if response is not None:
                # すでに登録済み
                if not update:
                    continue
                else:
                    # 更新日時を比較する
                    (game_id,) = response
                    updated_date = self.session.execute(
                        select(Game.updated_date).where(Game.id == game_id)
                    ).scalar_one_or_none()
                    # (None, datetime), (datetime, None)は更新
                    # (None, None)は更新しない
                    if updated_date is None or game_info.updated_date is None:
                        if updated_date is None and game_info.updated_date is None:
                            continue
                    # 登録されているもののほうが新しい場合は更新しない
                    elif updated_date >= game_info.updated_date:
                        continue
                    # Gameを削除すると関連するKifuも削除される
                    self.session.execute(delete(Game).where(Game.id == game_id))
                    logger.info(f"Update game {game_info.game_name} {updated_date} -> {game_info.updated_date}")

            num_moves = len(game_info.moves) - 1
            black_player, _ = self.get_player_from_player_name(game_info.black_player_name, game_info.game_type, True)
            white_player, _ = self.get_player_from_player_name(game_info.white_player_name, game_info.game_type, True)
            game = Game(
                game_type=game_info.game_type,
                game_name=game_info.game_name,
                start_date=game_info.start_date,
                end_date=game_info.end_date,
                result_code=int(game_info.game_result) if game_info.game_result is not None else 0,
                num_moves=num_moves,
                time_control_black=getattr(game_info, "black_time_control", None),
                time_control_white=getattr(game_info, "white_time_control", None),
                init_position_sfen=game_info.init_position_sfen,
                updated_date=game_info.updated_date,
                black_player=black_player,
                white_player=white_player,
            )
            self.session.add(game)

            board.set_sfen(game_info.init_position_sfen)
            nodes_values = game_info.nodes_values
            depth_values = game_info.depth_values
            seldepth_values = game_info.seldepth_values
            move_times_ms = game_info.move_times_ms
            wall_times_ms = getattr(game_info, "wall_times_ms", [None] * len(move_times_ms))
            latency_deltas_ms = getattr(game_info, "latency_deltas_ms", [None] * len(move_times_ms))

            for idx, next_move in enumerate(game_info.moves):
                next_move_comment = game_info.move_comments[idx]
                eval_value = game_info.eval_values[idx]
                ms_val = move_times_ms[idx]
                wall_val = wall_times_ms[idx] if idx < len(wall_times_ms) else None
                latency_val = latency_deltas_ms[idx] if idx < len(latency_deltas_ms) else None
                next_move16 = MOVE_END if next_move == MOVE_END else cshogi.move16(next_move)
                kifu = Kifu(
                    ply=board.move_number - 1,
                    next_move=next_move16,
                    next_move_time_ms=ms_val,
                    wall_time_ms=wall_val,
                    latency_delta_ms=latency_val,
                    game=game,
                    next_move_comment=next_move_comment,
                    eval=eval_value,
                    depth=depth_values[idx],
                    seldepth=seldepth_values[idx],
                    nodes=nodes_values[idx],
                )
                self.session.add(kifu)

                if next_move == MOVE_END:
                    break

                assert board.is_legal(next_move)
                board.push(next_move)

            self.session.commit()

    def export_to_game_info(self, game_id: int | None = None, game_name: str | None = None) -> GameInfo | None:
        """Reconstruct a :class:`GameInfo` instance from stored ORM rows."""
        if game_id is None and game_name is None:
            raise ValueError("Either game_id or game_name must be provided")

        if game_id is not None:
            game = self.session.execute(select(Game).where(Game.id == game_id)).scalar_one_or_none()
        else:
            game = self.session.execute(select(Game).where(Game.game_name == game_name)).scalar_one_or_none()
        if game is None:
            return None
        kifus = self.session.execute(select(Kifu).where(Kifu.game_id == game.id).order_by(Kifu.id.asc())).fetchall()

        black_player_name = self.session.execute(
            select(Player.player_name).where(Player.id == game.black_player_id)
        ).scalar_one()
        white_player_name = self.session.execute(
            select(Player.player_name).where(Player.id == game.white_player_id)
        ).scalar_one()

        moves: list[int] = []
        move_times_ms: list[int | None] = []
        wall_times_ms: list[int | None] = []
        latency_deltas_ms: list[int | None] = []
        move_comments: list[str | None] = []
        eval_values: list[int | None] = []
        nodes_values: list[int | None] = []
        depth_values: list[int | None] = []
        seldepth_values: list[int | None] = []
        board = cshogi.Board()
        board.set_sfen(game.init_position_sfen)
        for (kifu,) in kifus:
            if kifu.next_move == MOVE_END:
                moves.append(MOVE_END)
                move_times_ms.append(kifu.next_move_time_ms)
                wall_times_ms.append(getattr(kifu, "wall_time_ms", None))
                latency_deltas_ms.append(getattr(kifu, "latency_delta_ms", None))
                move_comments.append(kifu.next_move_comment)
                eval_values.append(kifu.eval)
                depth_values.append(kifu.depth)
                seldepth_values.append(kifu.seldepth)
                nodes_values.append(kifu.nodes)
                break
            else:
                next_move = board.move_from_move16(kifu.next_move)
                assert board.is_legal(next_move)
                moves.append(next_move)
                move_times_ms.append(kifu.next_move_time_ms)
                wall_times_ms.append(getattr(kifu, "wall_time_ms", None))
                latency_deltas_ms.append(getattr(kifu, "latency_delta_ms", None))
                move_comments.append(kifu.next_move_comment)
                eval_values.append(kifu.eval)
                depth_values.append(kifu.depth)
                seldepth_values.append(kifu.seldepth)
                nodes_values.append(kifu.nodes)
                board.push(next_move)

        # Reconstruct time limit object if present
        game_info = GameInfo(
            game_name=game.game_name,
            game_type=game.game_type,
            black_player_name=black_player_name,
            white_player_name=white_player_name,
            start_date=game.start_date,
            end_date=game.end_date,
            black_time_control=game.time_control_black,
            white_time_control=game.time_control_white,
            game_result=GameResult(game.result_code),
            init_position_sfen=game.init_position_sfen,
            moves=moves,
            move_comments=move_comments,
            eval_values=eval_values,
            nodes_values=nodes_values,
            depth_values=depth_values,
            seldepth_values=seldepth_values,
            move_times_ms=move_times_ms,
            wall_times_ms=wall_times_ms,
            latency_deltas_ms=latency_deltas_ms,
        )
        return game_info
