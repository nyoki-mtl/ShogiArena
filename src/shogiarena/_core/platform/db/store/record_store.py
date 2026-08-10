from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from json import JSONDecodeError

import rsshogi
from rsshogi.core import Board, Move, Move32
from sqlalchemy import delete, inspect, select
from sqlalchemy.orm import Session

from shogiarena._core.shared.kernel.game_results import game_result_name
from shogiarena._core.shared.kernel.scalar_coercion.api import (
    coerce_game_result,
    coerce_int,
    coerce_iso_datetime,
    coerce_optional_bool,
    coerce_str,
)
from shogiarena._core.shared.kernel.serialization import json_serialize

from .entities import Game, GameMove, GameTimeoutAttribution, Player
from .repository import ShogiRepositoryPort

logger = logging.getLogger(__name__)

_DB_METADATA_ATTRIBUTE_KEYS = {"storage", "game_name", "game_type", "updated_date"}

# 時間切れ由来を列へ投影するための metadata attribute（task 0049）。値は解釈せず不透明な文字列として扱う。
# blob 側にも残すので、load の roundtrip は投影の有無に依存しない。
_TIMEOUT_ORIGIN_ATTRIBUTE = "timeout_origin"
_TIMEOUT_ORIGIN_MAX_LENGTH = 32


def _database_datetime(value: datetime | None) -> datetime | None:
    """Store and compare timestamps in the timezone-naive UTC form used by the DB schema."""
    if value is None or value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def _record_datetime(value: object) -> datetime | None:
    parsed = coerce_iso_datetime(value)
    if parsed is not None or not isinstance(value, str):
        return parsed
    try:
        return datetime.strptime(value, "%Y/%m/%d %H:%M:%S")
    except ValueError:
        return None


def _push_record_move(board: Board, move_obj: Move | Move32) -> Move:
    if isinstance(move_obj, Move):
        return board.push_move(move_obj).to_move()
    if isinstance(move_obj, Move32):
        return board.push_move32(move_obj).to_move()
    raise TypeError(f"db storage requires Move/Move32 payload, got {type(move_obj)!r}")


def _engine_wall_time_from_info(engine_info: rsshogi.record.EngineInfo | None) -> int | None:
    return coerce_int(_engine_info_extras(engine_info).get("engine_wall_time_ms"))


def _engine_info_extras(engine_info: rsshogi.record.EngineInfo | None) -> Mapping[str, object]:
    if engine_info is None:
        return {}
    extras = engine_info.extras
    return extras if isinstance(extras, Mapping) else {}


def _move_source_from_info(engine_info: rsshogi.record.EngineInfo | None) -> str | None:
    value = coerce_str(_engine_info_extras(engine_info).get("move_source"))
    return value.strip() if value is not None and value.strip() else None


def _book_hit_from_info(engine_info: rsshogi.record.EngineInfo | None) -> int | None:
    extras = _engine_info_extras(engine_info)
    if "book_hit" in extras:
        value = coerce_optional_bool(extras.get("book_hit"))
        return None if value is None else int(value)
    if _move_source_from_info(engine_info) == "book":
        return 1
    return None


def _timeout_origin_from_attributes(attributes: object) -> str | None:
    if not isinstance(attributes, Mapping):
        return None
    raw = coerce_str(attributes.get(_TIMEOUT_ORIGIN_ATTRIBUTE))
    value = raw.strip() if raw is not None else ""
    if not value:
        return None
    if len(value) > _TIMEOUT_ORIGIN_MAX_LENGTH:
        logger.debug("Skipping oversized timeout_origin projection: %r", value)
        return None
    return value


def _serialize_metadata_attributes(attributes: object) -> str | None:
    if not isinstance(attributes, Mapping):
        return None
    payload = {
        str(key): json_serialize(value)
        for key, value in attributes.items()
        if str(key) not in _DB_METADATA_ATTRIBUTE_KEYS
    }
    if not payload:
        return None
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _metadata_attribute_value(value: object) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(json_serialize(value), ensure_ascii=False, sort_keys=True)


def _deserialize_metadata_attributes(raw: object) -> dict[str, str]:
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        decoded = json.loads(raw)
    except JSONDecodeError:
        logger.debug("Skipping invalid game metadata_attributes_json payload")
        return {}
    if not isinstance(decoded, Mapping):
        return {}
    return {str(key): _metadata_attribute_value(value) for key, value in decoded.items()}


class DBRecordStore:
    """DB-backed RecordStore implementation."""

    def __init__(self, repository: ShogiRepositoryPort) -> None:
        self._repository = repository

    def append(self, records: Iterable[rsshogi.record.Record | None], *, should_update: bool = False) -> None:
        """Persist a batch atomically and release its task-scoped session."""

        with self._repository.operation(commit=True):
            self._append(records, should_update=should_update)

    def append_in_current_transaction(
        self,
        records: Iterable[rsshogi.record.Record | None],
        *,
        should_update: bool = False,
    ) -> None:
        """Caller-owned repository transactionへrecordを追加する。"""

        self._append(records, should_update=should_update)

    @staticmethod
    def _has_timeout_attribution_table(session: Session) -> bool:
        """attribution table の有無を transaction 内で確認する（task 0052 / Decision 11）。

        1.0.x が作った table 無し DB では、存在しない table への DELETE / INSERT を発行しない。
        欠落していても ``metadata_attributes_json`` 側の origin は保持されるので、
        load の roundtrip は投影の有無に依存しない。
        """

        bind = session.get_bind()
        return bool(inspect(bind).has_table(GameTimeoutAttribution.__tablename__))

    def _append(self, records: Iterable[rsshogi.record.Record | None], *, should_update: bool = False) -> None:
        session = self._repository.session
        board = Board()
        has_attribution_table = self._has_timeout_attribution_table(session)

        for item in records:
            if item is None:
                continue
            record = item
            metadata = record.metadata
            metadata_attributes_json = _serialize_metadata_attributes(metadata.attributes)
            timeout_origin = _timeout_origin_from_attributes(metadata.attributes)
            game_name = record.game_name
            game_type = record.game_type
            black_name = metadata.black_player
            white_name = metadata.white_player
            if game_name is None:
                raise ValueError("Record.game_name must be defined")
            if game_type is None:
                raise ValueError("Record.game_type must be defined")
            if black_name is None or white_name is None:
                raise ValueError("Record player names must be defined")
            updated_date_new = _database_datetime(coerce_iso_datetime(record.updated_date))
            if updated_date_new is None:
                raise ValueError("Record.updated_date must be ISO-8601 datetime")
            start_date = _record_datetime(metadata.start_date)
            end_date = _record_datetime(metadata.end_date)
            black_tc = record.black_time_control
            white_tc = record.white_time_control
            tc_black = black_tc.to_spec() if black_tc is not None else None
            tc_white = white_tc.to_spec() if white_tc is not None else None
            result_obj = record.result
            if result_obj is None:
                raise ValueError("Record.result must be defined")
            game_result = game_result_name(result_obj)
            end_time_ms = record.end_time_ms
            end_comment = record.end_comment
            move_records = list(record.moves)
            init_sfen = record.init_position_sfen
            if init_sfen is None:
                raise ValueError("Record.init_position_sfen must be defined")

            existing_game_id = session.execute(select(Game.id).where(Game.game_name == game_name)).scalar_one_or_none()
            if existing_game_id is not None:
                if not should_update:
                    continue
                existing_updated_date = session.execute(
                    select(Game.updated_date).where(Game.id == existing_game_id)
                ).scalar_one_or_none()
                existing_updated_date = _database_datetime(existing_updated_date)
                if existing_updated_date is None or updated_date_new is None:
                    if existing_updated_date is None and updated_date_new is None:
                        continue
                elif existing_updated_date >= updated_date_new:
                    continue
                session.execute(delete(GameMove).where(GameMove.game_id == existing_game_id))
                if has_attribution_table:
                    session.execute(
                        delete(GameTimeoutAttribution).where(GameTimeoutAttribution.game_id == existing_game_id)
                    )
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
                metadata_attributes_json=metadata_attributes_json,
                black_player=black_player,
                white_player=white_player,
            )
            session.add(game)
            if timeout_origin is not None and has_attribution_table:
                session.add(GameTimeoutAttribution(game=game, origin=timeout_origin))

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
                move_source = _move_source_from_info(engine_info)
                book_hit = _book_hit_from_info(engine_info)
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
                    move_source=move_source,
                    book_hit=book_hit,
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
                move_source=None,
                book_hit=None,
            )
            session.add(end_game_move)

    def load(self, *, game_id: int | None = None, game_name: str | None = None) -> rsshogi.record.Record | None:
        """Load one record and release its task-scoped session."""

        with self._repository.operation():
            return self._load(game_id=game_id, game_name=game_name)

    def _load(self, *, game_id: int | None = None, game_name: str | None = None) -> rsshogi.record.Record | None:
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

        move_records: list[rsshogi.record.MoveEntry] = []
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
            if game_move.move_source is not None:
                extras["move_source"] = game_move.move_source
            if game_move.book_hit is not None:
                extras["book_hit"] = int(game_move.book_hit)
            engine_info = rsshogi.record.EngineInfo(
                eval=game_move.eval,
                depth=game_move.depth,
                seldepth=game_move.seldepth,
                nodes=game_move.nodes,
                wall_time_ms=int(wall_time) if wall_time is not None else None,
                latency_delta_ms=int(latency_delta) if latency_delta is not None else None,
                extras=extras or None,
            )
            move_records.append(
                rsshogi.record.MoveEntry(
                    pushed_move,
                    time_ms=game_move.next_move_time_ms,
                    comment=game_move.next_move_comment,
                    engine_info=engine_info,
                )
            )

        tc_black = (
            rsshogi.record.TimeControl.from_spec(game.time_control_black)
            if game.time_control_black is not None
            else None
        )
        tc_white = (
            rsshogi.record.TimeControl.from_spec(game.time_control_white)
            if game.time_control_white is not None
            else None
        )
        metadata_attributes = _deserialize_metadata_attributes(game.metadata_attributes_json)
        metadata_attributes.update(
            {
                "storage": "db",
                "game_name": game.game_name,
                "game_type": game.game_type,
                "updated_date": game.updated_date.isoformat(),
            }
        )
        record_metadata = rsshogi.record.RecordMetadata(
            game_name=game.game_name,
            game_type=game.game_type,
            black_player=black_player_name,
            white_player=white_player_name,
            start_date=game.start_date.isoformat() if game.start_date is not None else None,
            end_date=game.end_date.isoformat() if game.end_date is not None else None,
            updated_date=game.updated_date.isoformat(),
            black_time_control=tc_black,
            white_time_control=tc_white,
            attributes=metadata_attributes,
        )
        game_result = coerce_game_result(game.game_result, is_strict=True)
        terminal = rsshogi.record.SpecialMoveEntry.from_result(
            game_result,
            time_ms=end_time_ms,
            comment=end_comment,
        )
        return rsshogi.record.Record.from_main_line(
            game.initial_position_sfen,
            move_records,
            terminal,
            record_metadata,
        )


__all__ = ["DBRecordStore"]
