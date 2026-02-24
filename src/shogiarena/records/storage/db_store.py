from __future__ import annotations

import logging
from collections.abc import Iterable, Iterator
from datetime import datetime

import rshogi
from rshogi.core import Board, Move, Move32
from sqlalchemy import delete, select

from shogiarena.db.models import Game, Kifu, Player
from shogiarena.db.repository import ShogiRepository
from shogiarena.utils.types.types import GameResult, game_result_terminal_kind

logger = logging.getLogger(__name__)


def _parse_optional_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


class DBRecordStore:
    """DB向けのRecordStore実装。"""

    def __init__(self, repository: ShogiRepository) -> None:
        self._repository = repository

    def append(self, records: Iterable[rshogi.record.GameRecord | None], *, update: bool = False) -> None:
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
            updated_date_new = _parse_optional_datetime(record.updated_date)
            if updated_date_new is None:
                raise ValueError("GameRecord.updated_date must be ISO-8601 datetime")
            start_date = _parse_optional_datetime(metadata.start_date)
            end_date = _parse_optional_datetime(metadata.end_date)
            black_tc = record.black_time_control
            white_tc = record.white_time_control
            tc_black = black_tc.to_spec() if black_tc is not None else None
            tc_white = white_tc.to_spec() if white_tc is not None else None
            result_obj = record.result
            result_code = result_obj.value if result_obj is not None else 0
            end_time_ms = record.end_time_ms
            end_comment = record.end_comment
            move_records = list(record.moves)
            init_sfen = record.init_position_sfen
            if init_sfen is None:
                raise ValueError("GameRecord.init_position_sfen must be defined")

            existing_game_id = session.execute(select(Game.id).where(Game.game_name == game_name)).scalar_one_or_none()
            if existing_game_id is not None:
                if not update:
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
                result_code=result_code,
                num_moves=num_moves,
                time_control_black=tc_black,
                time_control_white=tc_white,
                init_position_sfen=init_sfen,
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
                if isinstance(move_obj, Move):
                    mv = move_obj
                elif isinstance(move_obj, Move32):
                    mv = move_obj.to_move()
                else:
                    raise TypeError(f"db storage requires Move/Move32 payload, got {type(move_obj)!r}")
                if not board.is_legal_move(mv):
                    raise ValueError(f"db storage requires legal move payload: {move_obj!r}")
                engine_info = move_record.engine_info
                wall_time_ms = engine_info.wall_time_ms if engine_info is not None else None
                latency_delta_ms = engine_info.latency_delta_ms if engine_info is not None else None
                kifu = Kifu(
                    ply=int(board.game_ply) - 1,
                    next_move=int(mv),
                    next_move_time_ms=move_record.time_ms,
                    wall_time_ms=wall_time_ms,
                    latency_delta_ms=latency_delta_ms,
                    game=game,
                    next_move_comment=move_record.comment,
                    eval=engine_info.eval if engine_info is not None else None,
                    depth=engine_info.depth if engine_info is not None else None,
                    seldepth=engine_info.seldepth if engine_info is not None else None,
                    nodes=engine_info.nodes if engine_info is not None else None,
                )
                session.add(kifu)
                board.apply_move(mv)

            end_kifu = Kifu(
                ply=int(board.game_ply) - 1,
                next_move=int(Move.MOVE_END),
                next_move_time_ms=end_time_ms,
                wall_time_ms=None,
                latency_delta_ms=None,
                game=game,
                next_move_comment=end_comment,
                eval=None,
                depth=None,
                seldepth=None,
                nodes=None,
            )
            session.add(end_kifu)

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

        kifus = session.execute(select(Kifu).where(Kifu.game_id == game.id).order_by(Kifu.id.asc())).fetchall()

        black_player_name = session.execute(
            select(Player.player_name).where(Player.id == game.black_player_id)
        ).scalar_one()
        white_player_name = session.execute(
            select(Player.player_name).where(Player.id == game.white_player_id)
        ).scalar_one()

        move_records: list[rshogi.record.MoveRecord] = []
        board = Board()
        board.set_sfen(game.init_position_sfen)
        end_time_ms: int | None = game.end_time_ms
        end_comment: str | None = game.end_comment
        for (kifu,) in kifus:
            if kifu.next_move == int(Move.MOVE_END):
                end_time_ms = kifu.next_move_time_ms
                end_comment = kifu.next_move_comment
                break
            try:
                mv = Move(kifu.next_move)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Invalid move in db record: {kifu.next_move}") from exc
            if not board.is_legal_move(mv):
                raise ValueError(f"Illegal move in db record: {kifu.next_move}")
            wall_time = getattr(kifu, "wall_time_ms", None)
            latency_delta = getattr(kifu, "latency_delta_ms", None)
            engine_info = rshogi.record.MoveEngineInfo(
                eval=kifu.eval,
                depth=kifu.depth,
                seldepth=kifu.seldepth,
                nodes=kifu.nodes,
                wall_time_ms=int(wall_time) if wall_time is not None else None,
                latency_delta_ms=int(latency_delta) if latency_delta is not None else None,
            )
            move_records.append(
                rshogi.record.MoveRecord(
                    mv,
                    time_ms=kifu.next_move_time_ms,
                    comment=kifu.next_move_comment,
                    engine_info=engine_info,
                )
            )
            board.apply_move(mv)

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
        game_result = GameResult(game.result_code)
        terminal = rshogi.record.SpecialMoveRecord(
            game_result_terminal_kind(game_result),
            game_result,
            time_ms=end_time_ms,
            comment=end_comment,
        )
        return rshogi.record.GameRecord.from_main_line(game.init_position_sfen, move_records, terminal, record_metadata)

    def iterate(self, *, game_type: str | None = None) -> Iterator[rshogi.record.GameRecord]:
        raise NotImplementedError("Game record iteration is not implemented yet for DB storage")
