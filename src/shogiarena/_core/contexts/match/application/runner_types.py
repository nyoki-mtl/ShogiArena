"""Shared internal types for GameRunner."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, NamedTuple, Protocol, TypedDict, runtime_checkable

from rsshogi.core import Board, Move

from shogiarena._core.contexts.match.domain.adjudication import Adjudicator
from shogiarena._core.contexts.match.ports.usi_think_ports import UsiThinkResultPort
from shogiarena._core.shared.kernel.game_results import GameResult
from shogiarena._core.shared.kernel.time_control import GameClock
from shogiarena._core.shared.kernel.timeout_attribution import TimeoutAttributionDecision


class RecoveredBestmoveResult(NamedTuple):
    """Return type for ``_handle_recovered_bestmove``."""

    result: GameResult | None
    ply_count: int


class ApplyMoveCommonResult(NamedTuple):
    """Return type for ``_apply_move_common`` to avoid unpacking ambiguity."""

    result: GameResult | None
    ply_count: int
    eval_value: int | None
    search_stats: dict[str, int | None]
    wall_time_ms: int | None
    engine_wall_time_ms: int | None


@dataclass(slots=True)
class MoveApplicationRequest:
    """Immutable request data for applying a decided move."""

    move: Move
    think_result: UsiThinkResultPort
    elapsed_ms: int
    engine_wall_time_ms: int | None
    move_source: str | None
    game_id: str | None
    ply_count: int
    is_side_that_moved_black: bool
    repetition_occurrences_to_draw: int
    # timeout attribution 用の ``bestmove`` 観測（task 0052）。実到着時刻の上界。
    observed_at_s: float | None = None
    observation_basis: str | None = None


@dataclass(slots=True)
class MoveApplicationStateRefs:
    """Mutable state references updated while applying a move."""

    board: Board
    moves: list[Move]
    eval_values: list[int | None]
    nodes_values: list[int | None]
    depth_values: list[int | None]
    seldepth_values: list[int | None]
    move_times_ms: list[int | None]
    wall_times_ms: list[int | None]
    engine_wall_times_ms: list[int | None]
    move_sources: list[str | None]
    latency_deltas_ms: list[int | None]
    current_time_control: GameClock
    black_time_control: GameClock
    white_time_control: GameClock
    # Per-game out-param for the timeout attribution decision (task 0047, typed in 0052).
    # scalar origin ではなく decision object を運ぶことで、breaker と persistence が
    # 同じ根拠から分岐できる。runner は worker 共有なので ``self`` には置かない。
    timeout_decision_holder: list[TimeoutAttributionDecision | None] = field(default_factory=lambda: [None])


@dataclass(slots=True)
class MoveApplicationDependencies:
    """External collaborators needed during move application."""

    adjudicator: Adjudicator | None


@dataclass(slots=True)
class RecoveredBestmoveRequest:
    """Immutable recovery event data after timeout stop."""

    think_result: UsiThinkResultPort
    elapsed_ms: int
    engine_wall_time_ms: int | None
    move_source: str | None
    current_engine_name: str
    game_id: str | None
    ply_count: int
    start_ply_number: int
    initial_sfen: str
    black_name: str
    white_name: str
    player_name: str
    is_black_turn: bool
    repetition_occurrences_to_draw: int
    # timeout attribution 用の ``bestmove`` 観測（task 0052）。実到着時刻の上界。
    observed_at_s: float | None = None
    observation_basis: str | None = None


@dataclass(slots=True)
class RecoveredBestmoveStateRefs:
    """Mutable state references touched during recovered-bestmove handling."""

    move_state: MoveApplicationStateRefs
    result_progress_emitted: list[bool] | None = None


class _MoveContinue(TypedDict):
    is_game_over: Literal[False]
    move: Move


class _GameOverResult(TypedDict):
    is_game_over: Literal[True]
    result: GameResult


_GameMoveResult = _MoveContinue | _GameOverResult


class _ClockStartPayload(TypedDict):
    type: Literal["clock_start"]
    game_id: str | None
    active: Literal["black", "white"]
    black_remain_ms: int
    white_remain_ms: int
    started_at_ms: int
    time_control_black: str
    time_control_white: str
    byoyomi_ms_black: int
    byoyomi_ms_white: int
    increment_ms_black: int
    increment_ms_white: int
    initial_sfen: str
    black_name: str
    white_name: str
    start_ply_number: int


class _MoveProgressPayload(TypedDict, total=False):
    type: Literal["move_progress"]
    game_id: str | None
    initial_sfen: str
    black_name: str
    white_name: str
    sfen: str
    move: str
    ki2_move: str
    eval_cp: int | None
    ply: int
    display_ply: int
    current_ply: int
    start_ply_number: int
    depth: int | None
    seldepth: int | None
    nodes: int | None
    time_ms: int | None
    wall_time_ms: int | None
    engine_wall_time_ms: int | None
    game_result: str


class _HandshakePayload(TypedDict, total=False):
    type: Literal["handshake_log"]
    game_id: str | None
    initial_sfen: str
    black_name: str
    white_name: str
    role: Literal["black", "white"]
    direction: object
    line: object
    ts: int
    state: str


class _EngineIoPayload(TypedDict, total=False):
    type: Literal["engine_io"]
    game_id: str | None
    initial_sfen: str
    black_name: str
    white_name: str
    role: Literal["black", "white"]
    direction: Literal["in", "out", "stderr"]
    line: str
    ts: int
    state: str


class _EngineIoBatchEntry(TypedDict, total=False):
    """1 行分の raw USI I/O。batch payload の ``entries`` 要素。"""

    direction: Literal["in", "out", "stderr"]
    line: str
    ts: int
    state: str


class _EngineIoBatchPayload(TypedDict, total=False):
    """時間窓で coalesce した raw engine-I/O の1イベント。

    move/clock/result と異なり lossy telemetry として扱う（0046）。
    """

    type: Literal["engine_io_batch"]
    game_id: str | None
    initial_sfen: str
    black_name: str
    white_name: str
    role: Literal["black", "white"]
    entries: list[_EngineIoBatchEntry]


class _EngineIoGameMeta(NamedTuple):
    """1 局の engine-I/O batch が共有する不変メタ情報。"""

    initial_sfen: str
    black_name: str
    white_name: str


class _EngineStatePayload(TypedDict, total=False):
    """engine lifecycle event 由来のバッジ状態遷移（0046）。

    raw engine-I/O 行から独立した安価な per-transition イベント。engine_status バッジは
    これを唯一の更新源とし、engine_io は raw ログ配信のみを担う。
    """

    type: Literal["engine_state"]
    game_id: str | None
    initial_sfen: str
    black_name: str
    white_name: str
    role: Literal["black", "white"]
    state: str
    ts: int
    # Optional command line to keep in the engine_status io_tail (e.g. ``usinewgame`` at kickoff).
    # Raw ``info`` lines are NOT carried here; only lifecycle-significant commands.
    direction: Literal["in", "out", "stderr"]
    line: str


class _ClockIncrementPayload(TypedDict):
    type: Literal["clock_increment"]
    game_id: str | None
    side: Literal["black", "white"]
    applied_increment_ms: int
    pre_black_remain_ms: int
    pre_white_remain_ms: int
    black_remain_ms: int
    white_remain_ms: int
    occurred_at_ms: int


_ProgressPayload = (
    _ClockStartPayload
    | _MoveProgressPayload
    | _HandshakePayload
    | _EngineIoPayload
    | _EngineIoBatchPayload
    | _EngineStatePayload
    | _ClockIncrementPayload
)


@runtime_checkable
class _ReadyStateEnginePort(Protocol):
    async def prepare_ready_state(self) -> None: ...

    async def prepare_new_game_position(self, *, initial_sfen: str) -> None: ...


@runtime_checkable
class _OptionNameEnginePort(Protocol):
    @property
    def options_name(self) -> str: ...


def _starting_ply_number(sfen: str) -> int:
    parts = sfen.split()
    if len(parts) >= 4:
        try:
            value = int(parts[3])
            return value if value > 0 else 1
        except ValueError:
            return 1
    return 1


__all__ = [
    "ApplyMoveCommonResult",
    "MoveApplicationDependencies",
    "MoveApplicationRequest",
    "MoveApplicationStateRefs",
    "RecoveredBestmoveResult",
    "RecoveredBestmoveRequest",
    "RecoveredBestmoveStateRefs",
    "_ClockIncrementPayload",
    "_EngineIoBatchEntry",
    "_EngineIoBatchPayload",
    "_EngineIoGameMeta",
    "_EngineIoPayload",
    "_EngineStatePayload",
    "_GameMoveResult",
    "_MoveProgressPayload",
    "_OptionNameEnginePort",
    "_ProgressPayload",
    "_ReadyStateEnginePort",
    "_starting_ply_number",
]
