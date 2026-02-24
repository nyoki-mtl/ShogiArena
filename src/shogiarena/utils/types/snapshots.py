"""Worker / Game snapshot の TypedDict 定義。

Arena core とダッシュボードの両方から import するため、
``utils/types`` に配置してレイヤー間の依存を回避する。
"""

from __future__ import annotations

from typing import Any, TypedDict

# ---------------------------------------------------------------------------
# Engine status
# ---------------------------------------------------------------------------


class _EngineIoTailEntryRequired(TypedDict):
    dir: str
    line: str
    ts: int


class EngineIoTailEntry(_EngineIoTailEntryRequired, total=False):
    """エンジン I/O ログの 1 エントリ。

    ``state`` は ``api_server._store_engine_io_entry()`` が
    エンジン状態変化時にのみ付与するオプショナルフィールド。
    """

    state: str


class EngineStatusEntry(TypedDict):
    state: str
    io_tail: list[EngineIoTailEntry]
    updated_at_ms: int


EngineStatusMap = dict[str, EngineStatusEntry]


# ---------------------------------------------------------------------------
# Worker snapshot (arena core)
# ---------------------------------------------------------------------------


class _WorkerSnapshotRequired(TypedDict):
    """``make_initial_snapshot`` で必ず初期化されるフィールド。"""

    game_id: str
    initial_sfen: str
    black_name: str
    white_name: str
    moves: list[str]
    ki2_moves: list[str]
    eval_black: list[int]
    eval_white: list[int]
    nodes_values: list[int]
    depth_values: list[int]
    seldepth_values: list[int]
    move_times_ms: list[int]
    wall_times_ms: list[int]
    latency_deltas_ms: list[int]
    latency_alerts: list[bool]
    currentPly: int
    sfen: str


class WorkerSnapshot(_WorkerSnapshotRequired, total=False):
    """ライフサイクル途中で追加されるオプションフィールド。"""

    _generation: int
    engine_status: EngineStatusMap
    result_code: int
    _clock_active: str | None
    _black_remain_ms: int
    _white_remain_ms: int
    _clock_started_at_ms: int
    time_control_black: object
    time_control_white: object


# ---------------------------------------------------------------------------
# Dashboard-enriched game snapshot
# ---------------------------------------------------------------------------


class GameSnapshot(WorkerSnapshot, total=False):
    """ダッシュボード側で付加されるフィールドを含む拡張スナップショット。

    ``GameStateUpdater.update_from_worker`` が ``clock``, ``end_reason``,
    ``meta`` を追加するため、ゲームキャッシュにはこの型で格納する。
    """

    clock: dict[str, Any]
    end_reason: str
    meta: dict[str, Any]


# ---------------------------------------------------------------------------
# Move progress event
# ---------------------------------------------------------------------------


class MoveProgressEvent(TypedDict, total=False):
    """``parse_move_progress`` で検証済みの move_progress イベント。

    JSON デシリアライズ境界で一度だけ isinstance / coerce_int を行い、
    以降のコードでは型安全にフィールドへアクセスできる。
    """

    game_id: str
    initial_sfen: str
    black_name: str
    white_name: str
    move: str
    ki2_move: str
    eval_cp: int
    ply: int
    sfen: str
    nodes: int
    depth: int
    seldepth: int
    time_ms: int
    wall_time_ms: int
    latency_ms: int
    latency_alert: bool
    result_code: int
