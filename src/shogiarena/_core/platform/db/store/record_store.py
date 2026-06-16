from __future__ import annotations

import logging
from collections.abc import Iterable

import rshogi
from rshogi.core import Board, Move, Move32
from sqlalchemy import delete, select

from shogiarena._core.shared.kernel.game_results import game_result_name
from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_game_result, coerce_int, coerce_iso_datetime

from .entities import Game, GameMove, Player
from .repository import ShogiRepositoryPort

logger = logging.getLogger(__name__)


def _push_record_move(board: Board, move_obj: Move | Move32) -> Move:
    if isinstance(move_obj, Move):
        return board.push_move(move_obj).to_move()
    if isinstance(move_obj, Move32):
        return board.push_move32(move_obj).to_move()
    raise TypeError(f"db storage requires Move/Move32 payload, got {type(move_obj)!r}")


def _engine_wall_time_from_info(engine_info: rshogi.record.MoveEngineInfo | None) -> int | None:
    if engine_info is None:
        return None
    return coerce_int(engine_info.extras.get("engine_wall_time_ms"))


class DBRecordStore:
    """DB-backed RecordStore implementation."""

    def __init__(self, repository: ShogiRepositoryPort) -> None:
        self._repository = repository

    def append(self, records: Iterable[rshogi.record.GameRecord | None], *, should_update: bool = False) -> None:
        session = self._repository.session
        board = Board()

        for item in records:
            if item is None:
                continue
            record = item
            metadata = record.metadata
            game_name = record.game_name
            game_type = record.game_type
            black_name = metadata.black_player
            white_name = metadata.white_player
            if game_name is None:
                raise ValueError("GameRecord.game_name must be defined")
            if game_type is None:
                raise ValueError("GameRecord.game_type must be defined")
            if black_name is None or white_name is None:
                raise ValueError("GameRecord player names must be defined")
            updated_date_new = coerce_iso_datetime(record.updated_date)
            if updated_date_new is None:
                raise ValueError("GameRecord.updated_date must be ISO-8601 datetime")
            start_date = coerce_iso_datetime(metadata.start_date)
            end_date = coerce_iso_datetime(metadata.end_date)
            black_tc = record.black_time_control
            white_tc = record.white_time_control
            tc_black = black_tc.to_spec() if black_tc is not None else None
            tc_white = white_tc.to_spec() if white_tc is not None else None
            result_obj = record.result
            if result_obj is None:
                raise ValueError("GameRecord.result must be defined")
            game_result = game_result_name(result_obj)
            end_time_ms = record.end_time_ms
            end_comment = record.end_comment
            move_records = list(record.moves)
            init_sfen = record.init_position_sfen
            if init_sfen is None:
                raise ValueError("GameRecord.init_position_sfen must be defined")

            existing_game_id = session.execute(select(Game.id).where(Game.game_name == game_name)).scalar_one_or_none()
            if existing_game_id is not None:
                if not should_update:
                    continue
                existing_updated_date = session.execute(
                    select(Game.updated_date).where(Game.id == existing_game_id)
                ).scalar_one_or_none()
                if existing_updated_date is None or updated_date_new is None:
                    if existing_updated_date is None and updated_date_new is None:
                        continue
                elif existing_updated_date >= updated_date_new:
                    continue
                session.execute(delete(Game).where(Game.id == existing_game_id))
                logger.info(
                    "Update game %s %s -> %s",
                    game_name,
                    existing_updated_date,
                    updated_date_new,
                )

            num_moves = len(move_records)
            black_player, _ = self._repository.get_player_from_player_name(black_name, game_type, True)
            white_player, _ = self._repository.get_player_from_player_name(white_name, game_type, True)
            game = Game(
                game_type=game_type,
                game_name=game_name,
                start_date=start_date,
                end_date=end_date,
                game_result=game_result,
                num_moves=num_moves,
                time_control_black=tc_black,
                time_control_white=tc_white,
                initial_position_sfen=init_sfen,
                end_time_ms=end_time_ms,
                end_comment=end_comment,
                updated_date=updated_date_new,
                black_player=black_player,
                white_player=white_player,
            )
            session.add(game)

            board.set_sfen(init_sfen)
            for move_record in move_records:
                move_obj = move_record.move
                ply = int(board.game_ply) - 1
                try:
                    mv = _push_record_move(board, move_obj)
                except ValueError as exc:
                    raise ValueError(f"db storage requires legal move payload: {move_obj!r}") from exc
                engine_info = move_record.engine_info
                wall_time_ms = engine_info.wall_time_ms if engine_info is not None else None
                engine_wall_time_ms = _engine_wall_time_from_info(engine_info)
                latency_delta_ms = engine_info.latency_delta_ms if engine_info is not None else None
                game_move = GameMove(
                    ply=ply,
                    next_move=int(mv),
                    next_move_time_ms=move_record.time_ms,
                    wall_time_ms=wall_time_ms,
                    engine_wall_time_ms=engine_wall_time_ms,
                    latency_delta_ms=latency_delta_ms,
                    game=game,
                    next_move_comment=move_record.comment,
                    eval=engine_info.eval if engine_info is not None else None,
                    depth=engine_info.depth if engine_info is not None else None,
                    seldepth=engine_info.seldepth if engine_info is not None else None,
                    nodes=engine_info.nodes if engine_info is not None else None,
                )
                session.add(game_move)

            end_game_move = GameMove(
                ply=int(board.game_ply) - 1,
                next_move=int(Move.MOVE_END),
                next_move_time_ms=end_time_ms,
                wall_time_ms=None,
                engine_wall_time_ms=None,
                latency_delta_ms=None,
                game=game,
                next_move_comment=end_comment,
                eval=None,
                depth=None,
                seldepth=None,
                nodes=None,
            )
            session.add(end_game_move)

            session.commit()

    def load(self, *, game_id: int | None = None, game_name: str | None = None) -> rshogi.record.GameRecord | None:
        if game_id is None and game_name is None:
            raise ValueError("Either game_id or game_name must be provided")

        session = self._repository.session
        if game_id is not None:
            game = session.execute(select(Game).where(Game.id == game_id)).scalar_one_or_none()
        else:
            game = session.execute(select(Game).where(Game.game_name == game_name)).scalar_one_or_none()
        if game is None:
            return None

        game_moves = session.execute(
            select(GameMove).where(GameMove.game_id == game.id).order_by(GameMove.id.asc())
        ).fetchall()

        black_player_name = session.execute(
            select(Player.player_name).where(Player.id == game.black_player_id)
        ).scalar_one()
        white_player_name = session.execute(
            select(Player.player_name).where(Player.id == game.white_player_id)
        ).scalar_one()

        move_records: list[rshogi.record.MoveRecord] = []
        board = Board()
        board.set_sfen(game.initial_position_sfen)
        end_time_ms: int | None = game.end_time_ms
        end_comment: str | None = game.end_comment
        for (game_move,) in game_moves:
            if game_move.next_move == int(Move.MOVE_END):
                end_time_ms = game_move.next_move_time_ms
                end_comment = game_move.next_move_comment
                break
            try:
                mv = Move(game_move.next_move)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Invalid move in db record: {game_move.next_move}") from exc
            try:
                pushed_move = board.push_move(mv).to_move()
            except ValueError as exc:
                raise ValueError(f"Illegal move in db record: {game_move.next_move}") from exc
            wall_time = game_move.wall_time_ms
            engine_wall_time = game_move.engine_wall_time_ms
            latency_delta = game_move.latency_delta_ms
            extras: dict[str, str | int | float] = {}
            if engine_wall_time is not None:
                extras["engine_wall_time_ms"] = int(engine_wall_time)
            engine_info = rshogi.record.MoveEngineInfo(
                eval=game_move.eval,
                depth=game_move.depth,
                seldepth=game_move.seldepth,
                nodes=game_move.nodes,
                wall_time_ms=int(wall_time) if wall_time is not None else None,
                latency_delta_ms=int(latency_delta) if latency_delta is not None else None,
                extras=extras or None,
            )
            move_records.append(
                rshogi.record.MoveRecord(
                    pushed_move,
                    time_ms=game_move.next_move_time_ms,
                    comment=game_move.next_move_comment,
                    engine_info=engine_info,
                )
            )

        tc_black = (
            rshogi.record.TimeControl.from_spec(game.time_control_black)
            if game.time_control_black is not None
            else None
        )
        tc_white = (
            rshogi.record.TimeControl.from_spec(game.time_control_white)
            if game.time_control_white is not None
            else None
        )
        record_metadata = rshogi.record.GameRecordMetadata(
            game_name=game.game_name,
            game_type=game.game_type,
            black_player=black_player_name,
            white_player=white_player_name,
            start_date=game.start_date.isoformat() if game.start_date is not None else None,
            end_date=game.end_date.isoformat() if game.end_date is not None else None,
            updated_date=game.updated_date.isoformat(),
            black_time_control=tc_black,
            white_time_control=tc_white,
            attributes={
                "storage": "db",
                "game_name": game.game_name,
                "game_type": game.game_type,
                "updated_date": game.updated_date.isoformat(),
            },
        )
        game_result = coerce_game_result(game.game_result, is_strict=True)
        terminal = rshogi.record.SpecialMoveRecord.from_result(
            game_result,
            time_ms=end_time_ms,
            comment=end_comment,
        )
        return rshogi.record.GameRecord.from_main_line(
            game.initial_position_sfen,
            move_records,
            terminal,
            record_metadata,
        )


__all__ = ["DBRecordStore"]
