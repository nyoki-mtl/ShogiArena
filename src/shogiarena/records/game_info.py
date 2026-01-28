from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import TYPE_CHECKING, Any, TypeVar

import cshogi

from shogiarena.records.parser.csa import CSAParser
from shogiarena.records.parser.kif import KIFParser
from shogiarena.utils.common.constants import MOVE_END
from shogiarena.utils.types.types import GameResult

if TYPE_CHECKING:
    from shogiarena.arena.services.persistence.records import GameParticipationRecord

T = TypeVar("T")


def _normalize_series(values: Sequence[T] | None, *, length: int, default: T) -> list[T]:
    if values is None:
        return [default for _ in range(length)]
    if len(values) != length:
        raise ValueError(f"Expected sequence of length {length}, got {len(values)}")
    return list(values)


def _normalize_move_times(values: Sequence[Any] | None, *, length: int) -> list[int | None]:
    if values is None:
        return [None for _ in range(length)]
    if len(values) != length:
        raise ValueError(f"Expected move_times length {length}, got {len(values)}")

    normalized: list[int | None] = []
    for index, value in enumerate(values):
        if value is None:
            normalized.append(None)
            continue
        try:
            normalized_value = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid move time at index {index}: {value!r}") from exc
        if normalized_value < 0:
            raise ValueError(f"Move time must be non-negative (index {index})")
        normalized.append(normalized_value)

    return normalized


def _normalize_latency(values: Sequence[Any] | None, *, length: int) -> list[int | None]:
    if values is None:
        return [None for _ in range(length)]
    if len(values) != length:
        raise ValueError(f"Expected latency series length {length}, got {len(values)}")

    normalized: list[int | None] = []
    for index, value in enumerate(values):
        if value is None:
            normalized.append(None)
            continue
        try:
            normalized.append(int(value))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid latency value at index {index}: {value!r}") from exc
    return normalized


class GameInfo:
    """Game record with metadata, annotated moves, and timing information."""

    def __init__(
        self,
        game_name: str | None = None,
        game_type: str | None = None,
        black_player_name: str | None = None,
        white_player_name: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        black_time_control: str | None = None,
        white_time_control: str | None = None,
        depth_limit_black: int | None = None,
        depth_limit_white: int | None = None,
        node_limit_black: int | None = None,
        node_limit_white: int | None = None,
        game_result: GameResult | None = None,
        init_position_sfen: str | None = None,
        moves: list[int] | None = None,
        move_comments: list[str | None] | None = None,
        eval_values: list[int | None] | None = None,
        nodes_values: list[int | None] | None = None,
        depth_values: list[int | None] | None = None,
        seldepth_values: list[int | None] | None = None,
        move_times_ms: list[int | None] | None = None,
        wall_times_ms: list[int | None] | None = None,
        latency_deltas_ms: list[int | None] | None = None,
        updated_date: datetime | None = None,
    ):
        self.game_name = game_name
        self.game_type = game_type
        self.black_player_name = black_player_name
        self.white_player_name = white_player_name
        self.start_date = start_date
        self.end_date = end_date
        self.black_time_control = black_time_control
        self.white_time_control = white_time_control
        self.depth_limit_black = depth_limit_black
        self.depth_limit_white = depth_limit_white
        self.node_limit_black = node_limit_black
        self.node_limit_white = node_limit_white
        self.game_result = game_result
        self.init_position_sfen = init_position_sfen or cshogi.STARTING_SFEN

        self.moves: list[int] = [] if moves is None else list(moves)
        series_length = len(self.moves)

        self.move_comments: list[str | None] = _normalize_series(move_comments, length=series_length, default=None)
        self.eval_values: list[int | None] = _normalize_series(eval_values, length=series_length, default=None)
        self.nodes_values: list[int | None] = _normalize_series(nodes_values, length=series_length, default=None)
        self.depth_values: list[int | None] = _normalize_series(depth_values, length=series_length, default=None)
        self.seldepth_values: list[int | None] = _normalize_series(seldepth_values, length=series_length, default=None)
        self.move_times_ms: list[int | None] = _normalize_move_times(move_times_ms, length=series_length)
        self.wall_times_ms: list[int | None] = _normalize_latency(wall_times_ms, length=series_length)
        self.latency_deltas_ms: list[int | None] = _normalize_latency(latency_deltas_ms, length=series_length)

        self.updated_date = updated_date or datetime.now().replace(microsecond=0)
        self._arena_participation: list[GameParticipationRecord] | None = None
        self.validate()

    def __str__(self) -> str:
        return (
            f"GameInfo("
            f"{self.black_player_name} vs {self.white_player_name}\n"
            f"game_name={self.game_name}, date={self.start_date} ~ {self.end_date}, num_moves={self.num_moves}\n"
            f"game_result={self.game_result}, updated_date={self.updated_date})"
        )

    def __repr__(self) -> str:
        return self.__str__()

    def copy(self) -> GameInfo:
        return GameInfo(
            game_name=self.game_name,
            game_type=self.game_type,
            black_player_name=self.black_player_name,
            white_player_name=self.white_player_name,
            start_date=self.start_date,
            end_date=self.end_date,
            black_time_control=self.black_time_control,
            white_time_control=self.white_time_control,
            depth_limit_black=self.depth_limit_black,
            depth_limit_white=self.depth_limit_white,
            node_limit_black=self.node_limit_black,
            node_limit_white=self.node_limit_white,
            game_result=self.game_result,
            init_position_sfen=self.init_position_sfen,
            moves=self.moves.copy(),
            move_comments=self.move_comments.copy(),
            eval_values=self.eval_values.copy(),
            updated_date=self.updated_date,
            nodes_values=self.nodes_values.copy(),
            depth_values=self.depth_values.copy(),
            seldepth_values=self.seldepth_values.copy(),
            move_times_ms=self.move_times_ms.copy(),
            wall_times_ms=self.wall_times_ms.copy(),
            latency_deltas_ms=self.latency_deltas_ms.copy(),
        )

    def validate(self) -> bool:
        board = cshogi.Board()
        board.set_sfen(self.init_position_sfen)
        for index, move in enumerate(self.moves):
            if move == MOVE_END:
                break
            if not board.is_legal(move):
                raise ValueError(f"Illegal move at index {index}: {move}")
            board.push(move)

        return True

    @property
    def num_moves(self) -> int:
        if not self.moves:
            return 0
        return len(self.moves) - (1 if self.moves[-1] == MOVE_END else 0)

    def get_board(self, move_num: int | None = None) -> cshogi.Board:
        board = cshogi.Board()
        board.set_sfen(self.init_position_sfen)
        if move_num is not None and move_num < 0:
            raise ValueError("move_num must be non-negative")

        end_index = len(self.moves) if move_num is None else min(move_num, len(self.moves))

        for index in range(end_index):
            move = self.moves[index]
            if move == MOVE_END:
                break
            if not board.is_legal(move):
                raise ValueError(f"Illegal move at index {index}: {move}")
            board.push(move)

        return board

    @classmethod
    def from_parser(
        cls,
        parse_result: dict[str, Any],
        game_type: str,
        game_name: str | None = None,
    ) -> GameInfo:
        black_player_name = parse_result["black_player_name"]
        white_player_name = parse_result["white_player_name"]
        start_date = parse_result["start_date"]
        end_date = parse_result["end_date"]
        init_position_sfen = parse_result["init_position_sfen"]

        game_result = GameResult.from_csa((parse_result["endgame"], parse_result["win"]))

        moves = list(parse_result["moves"])
        move_times_ms: list[int | None] | None = parse_result.get("move_times")
        if move_times_ms is not None:
            move_times_ms = list(move_times_ms)
        move_comments = list(parse_result["move_comments"])
        black_time_control = parse_result.get("black_time_control")
        white_time_control = parse_result.get("white_time_control")

        return cls(
            game_name=game_name,
            game_type=game_type,
            black_player_name=black_player_name,
            white_player_name=white_player_name,
            game_result=game_result,
            start_date=start_date,
            end_date=end_date,
            init_position_sfen=init_position_sfen,
            black_time_control=black_time_control,
            white_time_control=white_time_control,
            moves=moves,
            move_times_ms=move_times_ms,
            move_comments=move_comments,
            wall_times_ms=parse_result.get("wall_times_ms"),
            latency_deltas_ms=parse_result.get("latency_deltas_ms"),
        )

    @classmethod
    def from_kif_str(
        cls,
        kif_str: str,
        game_type: str,
        game_name: str | None = None,
    ) -> GameInfo:
        parser = KIFParser()
        parse_result = parser.parse_kif_str(kif_str)
        return cls.from_parser(parse_result, game_type, game_name=game_name)

    @classmethod
    def from_csa_str(
        cls,
        csa_str: str,
        game_type: str,
        game_name: str | None = None,
    ) -> GameInfo:
        parser = CSAParser()
        parse_result = parser.parse_csa_str(csa_str)
        return cls.from_parser(parse_result, game_type, game_name=game_name)

    def export_to_kif(self) -> str:
        from .exporters import export_to_kif

        return export_to_kif(self)

    def export_to_csa(self) -> str:
        from .exporters import export_to_csa

        return export_to_csa(self)
