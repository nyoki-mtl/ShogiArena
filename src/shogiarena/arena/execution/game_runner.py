"""
Game runner for executing single games between two engines.

This module handles:
- Single game execution with proper position setup
- Move validation and game state management
- Timeout and error handling
- Game result determination (win/loss/draw)
- GameRecord creation for database persistence
"""

import asyncio
import json
import logging
import time
import zlib
from collections.abc import Awaitable, Callable
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime
from typing import Literal, NamedTuple, TypedDict, cast

import rshogi
from rshogi.core import Board, Move, normalize_usi_position
from rshogi.types import Color, RepetitionState

from shogiarena.arena.engines.time_control import TimeControl, TimeControlLimits, limits_to_record_time_spec
from shogiarena.arena.engines.usi_engine import PonderHitTimings
from shogiarena.arena.engines.usi_think import request_from_time_controls
from shogiarena.arena.engines.usi_types import UsiThinkResult
from shogiarena.arena.execution.types import GameEngineProtocol
from shogiarena.arena.services.game_control.adjudication import AdjudicationConfig, Adjudicator
from shogiarena.utils.types.types import STARTING_SFEN, GameResult, game_result_terminal_kind, timeout_win_result

logger = logging.getLogger(__name__)
_BLACK = int(Color.BLACK)
_GAME_RESULT_ERROR = GameResult.from_str("ERROR")


class RecoveredBestmoveResult(NamedTuple):
    """Return type for _handle_recovered_bestmove."""

    result: GameResult | None
    ply_count: int


class ApplyMoveCommonResult(NamedTuple):
    """Return type for _apply_move_common to avoid unpacking ambiguity."""

    result: GameResult | None
    ply_count: int
    eval_value: int | None
    search_stats: dict[str, int | None]
    wall_time_ms: int | None


class _MoveContinue(TypedDict):
    game_over: Literal[False]
    move: Move


class _GameOverResult(TypedDict):
    game_over: Literal[True]
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
    currentPly: int
    start_ply_number: int
    depth: int | None
    seldepth: int | None
    nodes: int | None
    time_ms: int | None
    wall_time_ms: int | None
    result_code: int


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
    direction: object
    line: object
    ts: int
    state: str


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
    _ClockStartPayload | _MoveProgressPayload | _HandshakePayload | _EngineIoPayload | _ClockIncrementPayload
)


def _starting_ply_number(sfen: str) -> int:
    parts = sfen.split()
    if len(parts) >= 4:
        try:
            value = int(parts[3])
            return value if value > 0 else 1
        except ValueError:
            return 1
    return 1


class GameRunner:
    """
    Executes a single game between two engines.
    """

    def __init__(
        self,
        progress_queue: asyncio.Queue[tuple[int, int, str | None]] | None = None,
        time_control_limits: TimeControlLimits | None = None,
        adjudication_config: AdjudicationConfig | None = None,
        *,
        repetition_occurrences_to_draw: int = 2,
        on_engine_options: Callable[[str, dict[str, object], dict[str, str] | None], None] | None = None,
    ):
        """
        Initialize game runner.

        Args:
            progress_queue: Optional queue for progress updates (game_id, ply_index, description or None)
            time_control_limits: Global time control configuration (per-side overrides are passed to run_game)
            adjudication_config: Adjudication configuration (max plies and resign policies)
            repetition_occurrences_to_draw: Number of times the same position must
                appear before declaring a repetition draw (>=2). Value 4 matches
                official shogi rules.
        """
        self.progress_queue = progress_queue

        # New time control and adjudication

        # Initialize time control (no implicit fallback defaults)
        self.time_control_limits = time_control_limits
        self.adjudication_config = adjudication_config
        if repetition_occurrences_to_draw < 2:
            raise ValueError("repetition_occurrences_to_draw must be at least 2")
        self.repetition_occurrences_to_draw = repetition_occurrences_to_draw

        self._shutting_down: bool = False
        self._engine_options_callback = on_engine_options
        self._published_engine_options: set[str] = set()
        self._latency_callback: Callable[[Literal["black", "white"], int | None, int | None, bool], None] | None = None
        self._apply_move_log_threshold_ms = 50.0
        self._clock_notify_log_threshold_ms = 50.0

    def set_engine_options_callback(
        self,
        callback: Callable[[str, dict[str, object], dict[str, str] | None], None] | None,
    ) -> None:
        """Install a callback invoked when USI option snapshots become available."""

        self._engine_options_callback = callback
        self._published_engine_options.clear()

    def set_latency_callback(
        self,
        callback: Callable[[Literal["black", "white"], int | None, int | None, bool], None] | None,
    ) -> None:
        """Install a callback invoked for each sampled latency measurement."""

        self._latency_callback = callback

    def request_shutdown(self) -> None:
        """Mark runner as shutting down to avoid further USI chatter."""
        self._shutting_down = True

    async def _enqueue_progress(self, game_id: str | None, ply: int, payload: _ProgressPayload) -> None:
        if self.progress_queue is None or game_id is None:
            return
        numeric_id = self._progress_numeric_id(game_id)
        await self.progress_queue.put((numeric_id, ply, json.dumps(payload, ensure_ascii=False)))

    async def _enqueue_clock_start(
        self,
        *,
        game_id: str | None,
        ply: int,
        start_ply_number: int,
        active_is_black: bool,
        black_remaining_ms: int,
        white_remaining_ms: int,
        time_control_black: str,
        time_control_white: str,
        black_limits: TimeControlLimits,
        white_limits: TimeControlLimits,
        initial_sfen: str,
        black_name: str,
        white_name: str,
    ) -> None:
        payload: _ClockStartPayload = {
            "type": "clock_start",
            "game_id": game_id,
            "active": "black" if active_is_black else "white",
            "black_remain_ms": black_remaining_ms,
            "white_remain_ms": white_remaining_ms,
            "started_at_ms": int(time.time() * 1000),
            "time_control_black": time_control_black,
            "time_control_white": time_control_white,
            "byoyomi_ms_black": int(black_limits.byoyomi_ms or 0),
            "byoyomi_ms_white": int(white_limits.byoyomi_ms or 0),
            "increment_ms_black": int(black_limits.increment_ms or 0),
            "increment_ms_white": int(white_limits.increment_ms or 0),
            "initial_sfen": initial_sfen,
            "black_name": black_name,
            "white_name": white_name,
            "start_ply_number": start_ply_number,
        }
        await self._enqueue_progress(game_id, ply, payload)

    async def _enqueue_move_progress(
        self,
        *,
        game_id: str | None,
        ply_index: int,
        start_ply_number: int,
        initial_sfen: str,
        black_name: str,
        white_name: str,
        board_sfen: str,
        usi_move: str,
        ki2_move: str,
        eval_cp: int | None,
        depth: int | None,
        seldepth: int | None,
        nodes: int | None,
        time_ms: int | None,
        wall_time_ms: int | None = None,
    ) -> None:
        display_ply = max(0, start_ply_number - 1) + ply_index
        payload: _MoveProgressPayload = {
            "type": "move_progress",
            "game_id": game_id,
            "initial_sfen": initial_sfen,
            "black_name": black_name,
            "white_name": white_name,
            "sfen": board_sfen,
            "move": usi_move,
            "ki2_move": ki2_move,
            "eval_cp": eval_cp,
            "ply": ply_index,
            "display_ply": display_ply,
            "currentPly": ply_index,
            "start_ply_number": start_ply_number,
            "depth": depth,
            "seldepth": seldepth,
            "nodes": nodes,
            "time_ms": time_ms,
            "wall_time_ms": wall_time_ms,
        }
        await self._enqueue_progress(game_id, ply_index, payload)

    async def _enqueue_game_result(
        self,
        *,
        game_id: str | None,
        usi_moves: list[str],
        result: GameResult,
        initial_sfen: str,
        final_sfen: str,
        black_name: str,
        white_name: str,
        start_ply_number: int,
    ) -> None:
        if self.progress_queue is None or game_id is None:
            return
        trimmed_moves = [move for move in usi_moves if move.strip()]
        completion_index = len(trimmed_moves)
        payload: _MoveProgressPayload = {
            "type": "move_progress",
            "game_id": game_id,
            "initial_sfen": initial_sfen,
            "black_name": black_name,
            "white_name": white_name,
            "sfen": final_sfen,
            "start_ply_number": start_ply_number,
            "ply": completion_index,
            "currentPly": completion_index,
            "result_code": result.value,
        }
        await self._enqueue_progress(
            game_id,
            completion_index,
            payload,
        )

    async def _enqueue_handshake_event(
        self,
        *,
        game_id: str | None,
        role: Literal["black", "white"],
        entry: dict[str, object],
        initial_sfen: str,
        black_name: str,
        white_name: str,
    ) -> None:
        if game_id is None:
            return
        direction = entry.get("dir")
        line = entry.get("line")
        ts_value = entry.get("ts")
        timestamp = int(ts_value) if isinstance(ts_value, int | float) else int(time.time() * 1000)
        payload: _HandshakePayload = {
            "type": "handshake_log",
            "game_id": game_id,
            "initial_sfen": initial_sfen,
            "black_name": black_name,
            "white_name": white_name,
            "role": role,
            "direction": direction,
            "line": line,
            "ts": timestamp,
        }
        state = entry.get("state")
        if isinstance(state, str) and state:
            payload["state"] = state
        await self._enqueue_progress(game_id, 0, payload)

    async def _enqueue_engine_io_event(
        self,
        *,
        game_id: str | None,
        role: Literal["black", "white"],
        entry: dict[str, object],
        initial_sfen: str,
        black_name: str,
        white_name: str,
    ) -> None:
        if game_id is None:
            return
        direction = entry.get("dir")
        line = entry.get("line")
        ts_value = entry.get("ts")
        timestamp = int(ts_value) if isinstance(ts_value, int | float) else int(time.time() * 1000)
        payload: _EngineIoPayload = {
            "type": "engine_io",
            "game_id": game_id,
            "initial_sfen": initial_sfen,
            "black_name": black_name,
            "white_name": white_name,
            "role": role,
            "direction": direction,
            "line": line,
            "ts": timestamp,
        }
        state = entry.get("state")
        if isinstance(state, str) and state:
            payload["state"] = state
        await self._enqueue_progress(game_id, 0, payload)

    def _register_handshake_listener(
        self,
        engine: GameEngineProtocol,
        role: Literal["black", "white"],
        game_id: str | None,
        initial_sfen: str,
        black_name: str,
        white_name: str,
    ) -> Callable[[], None]:
        if game_id is None:
            return lambda: None
        register = getattr(engine, "register_handshake_log_handler", None)
        if not callable(register):
            return lambda: None

        def handler(entry: dict[str, object]) -> Awaitable[None] | None:
            return self._enqueue_handshake_event(
                game_id=game_id,
                role=role,
                entry=entry,
                initial_sfen=initial_sfen,
                black_name=black_name,
                white_name=white_name,
            )

        remove = register(handler)
        return remove if callable(remove) else lambda: None

    def _register_engine_io_listener(
        self,
        engine: GameEngineProtocol,
        role: Literal["black", "white"],
        game_id: str | None,
        initial_sfen: str,
        black_name: str,
        white_name: str,
    ) -> Callable[[], None]:
        if game_id is None:
            return lambda: None
        register = getattr(engine, "register_io_log_handler", None)
        if not callable(register):
            return lambda: None

        def handler(entry: dict[str, object]) -> Awaitable[None] | None:
            return self._enqueue_engine_io_event(
                game_id=game_id,
                role=role,
                entry=entry,
                initial_sfen=initial_sfen,
                black_name=black_name,
                white_name=white_name,
            )

        remove = register(handler)
        return remove if callable(remove) else lambda: None

    @contextmanager
    def _handshake_listener_context(
        self,
        black_engine: GameEngineProtocol,
        white_engine: GameEngineProtocol,
        game_id: str | None,
        initial_sfen: str,
        black_name: str,
        white_name: str,
    ):
        cleanups: list[Callable[[], None]] = [
            self._register_handshake_listener(black_engine, "black", game_id, initial_sfen, black_name, white_name),
            self._register_handshake_listener(white_engine, "white", game_id, initial_sfen, black_name, white_name),
        ]
        try:
            yield
        finally:
            for cleanup in cleanups:
                cleanup()

    @contextmanager
    def _engine_io_listener_context(
        self,
        black_engine: GameEngineProtocol,
        white_engine: GameEngineProtocol,
        game_id: str | None,
        initial_sfen: str,
        black_name: str,
        white_name: str,
    ):
        cleanups: list[Callable[[], None]] = [
            self._register_engine_io_listener(black_engine, "black", game_id, initial_sfen, black_name, white_name),
            self._register_engine_io_listener(white_engine, "white", game_id, initial_sfen, black_name, white_name),
        ]
        try:
            yield
        finally:
            for cleanup in cleanups:
                cleanup()

    async def _enqueue_terminal_progress(
        self,
        *,
        game_id: str | None,
        ply_index: int,
        start_ply_number: int,
        initial_sfen: str,
        black_name: str,
        white_name: str,
        board_sfen: str,
        result: GameResult,
        think_result: UsiThinkResult,
        elapsed_ms: int,
    ) -> None:
        if self.progress_queue is None or game_id is None:
            return
        eval_cp = self._extract_evaluation(think_result)
        search_stats = self._extract_search_statistics(think_result, elapsed_ms)
        display_ply = max(0, start_ply_number - 1) + ply_index
        payload: _MoveProgressPayload = {
            "type": "move_progress",
            "game_id": game_id,
            "initial_sfen": initial_sfen,
            "black_name": black_name,
            "white_name": white_name,
            "sfen": board_sfen,
            "ply": ply_index,
            "display_ply": display_ply,
            "currentPly": ply_index,
            "start_ply_number": start_ply_number,
            "result_code": result.value,
            "eval_cp": eval_cp,
            "depth": search_stats["depth"],
            "seldepth": search_stats["seldepth"],
            "nodes": search_stats["nodes"],
            "time_ms": search_stats["time_ms"],
            "wall_time_ms": int(elapsed_ms),
        }
        await self._enqueue_progress(game_id, ply_index, payload)

    def _progress_numeric_id(self, game_id: str) -> int:
        """Convert textual game_id to numeric form for progress tracking."""
        if game_id.startswith("game_"):
            suffix = game_id[len("game_") :]
            compact_suffix = suffix.replace("_", "")
            if compact_suffix.isdigit():
                return int(compact_suffix)
        return zlib.crc32(game_id.encode("utf-8")) & 0x7FFFFFFF

    async def run_game(
        self,
        black_engine: GameEngineProtocol,
        white_engine: GameEngineProtocol,
        initial_sfen: str = "startpos",
        game_id: str | None = None,
        black_time_control_limits: TimeControlLimits | None = None,
        white_time_control_limits: TimeControlLimits | None = None,
    ) -> rshogi.record.GameRecord:
        """
        Run a single game between two engines.

        Args:
            black_engine: Engine playing black (sente)
            white_engine: Engine playing white (gote)
            initial_sfen: Starting position (SFEN format or "startpos")
            game_id: Optional game identifier

        Returns:
            GameRecord object with game results
        """
        if game_id is None:
            game_id = f"game_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        logger.debug(f"Starting game {game_id}: {black_engine.name} (Black) vs {white_engine.name} (White)")

        # Initialize game_result to handle exceptions
        game_result = _GAME_RESULT_ERROR
        result_progress_emitted = [False]

        # Initialize game state with normalized SFEN
        board = Board()

        normalized_sfen = normalize_usi_position(initial_sfen)
        if normalized_sfen == "startpos":
            board.reset()
        else:
            board.set_sfen(normalized_sfen)
        initial_sfen = normalized_sfen

        start_ply_number = _starting_ply_number(initial_sfen)

        start_time = datetime.now()
        moves: list[int] = []
        usi_moves: list[str] = []
        eval_values: list[int | None] = []
        # Search statistics arrays for enhanced dashboard
        nodes_values: list[int | None] = []
        depth_values: list[int | None] = []
        seldepth_values: list[int | None] = []
        move_times_ms: list[int | None] = []
        wall_times_ms: list[int | None] = []
        latency_deltas_ms: list[int | None] = []

        # Initialize time control and adjudication (time control is required)
        black_time_control: TimeControl
        white_time_control: TimeControl
        adjudicator: Adjudicator | None = None

        # Prefer per-side overrides if provided; fallback to global limits
        black_limits = black_time_control_limits or self.time_control_limits
        white_limits = white_time_control_limits or self.time_control_limits
        if black_limits is None or white_limits is None:
            raise RuntimeError("Time control limits are required for both sides (provide per-side or global limits)")
        black_time_control = TimeControl(black_limits)
        white_time_control = TimeControl(white_limits)
        black_time_control.initialize_for_game()
        white_time_control.initialize_for_game()
        logger.debug(f"Time control initialized (B/W): {black_time_control} / {white_time_control}")

        if self.adjudication_config:
            adjudicator = Adjudicator(self.adjudication_config)
            logger.debug(f"Adjudication initialized: {self.adjudication_config}")

        with (
            self._engine_io_listener_context(
                black_engine,
                white_engine,
                game_id,
                initial_sfen,
                black_engine.name,
                white_engine.name,
            ),
        ):
            cancelled = False
            error: Exception | None = None
            try:
                # Prepare both engines for the game
                if not self._shutting_down:
                    await self._prepare_engines_for_game(black_engine, white_engine, initial_sfen)
                else:
                    raise asyncio.CancelledError()

                # Game loop
                game_result = await self._game_loop(
                    board,
                    black_engine,
                    white_engine,
                    initial_sfen,
                    start_ply_number,
                    moves,
                    usi_moves,
                    eval_values,
                    nodes_values,
                    depth_values,
                    seldepth_values,
                    move_times_ms,
                    wall_times_ms,
                    latency_deltas_ms,
                    black_time_control=black_time_control,
                    white_time_control=white_time_control,
                    game_id=game_id,
                    adjudicator=adjudicator,
                    result_progress_emitted=result_progress_emitted,
                )

            except asyncio.CancelledError:
                # Propagate cancellation to align with orchestrator-level SIGINT handling
                cancelled = True
                raise
            except (OSError, RuntimeError, ValueError, asyncio.TimeoutError) as exc:
                if self._shutting_down:
                    # Suppress error spam on shutdown
                    game_result = GameResult.PAUSED
                else:
                    logger.exception("Game %s failed with unhandled error", game_id)
                    game_result = _GAME_RESULT_ERROR
                error = exc
            finally:
                # Send gameover to both engines
                if not cancelled and not self._shutting_down:
                    await self._finalize_game(black_engine, white_engine, game_result)

        if error is not None:
            raise error

        end_time = datetime.now()

        # Send final result to progress queue (as move_progress with result_code)
        if game_result is not None and not result_progress_emitted[0]:
            await self._enqueue_game_result(
                game_id=game_id,
                usi_moves=usi_moves,
                result=game_result,
                initial_sfen=normalized_sfen,
                final_sfen=board.to_sfen(),
                black_name=black_engine.name,
                white_name=white_engine.name,
                start_ply_number=start_ply_number,
            )

        # Build encoded TimeControl spec strings for DB/UI
        tc_spec_black_str = limits_to_record_time_spec(black_time_control.limits)
        tc_spec_white_str = limits_to_record_time_spec(white_time_control.limits)
        record_metadata = rshogi.record.GameRecordMetadata(
            game_name=game_id,
            game_type="arena",
            black_player=black_engine.name,
            white_player=white_engine.name,
            start_date=start_time.isoformat(),
            end_date=end_time.isoformat(),
            updated_date=end_time.isoformat(),
            black_time_control=rshogi.record.TimeControl.from_spec(tc_spec_black_str),
            white_time_control=rshogi.record.TimeControl.from_spec(tc_spec_white_str),
            attributes={
                "game_name": game_id,
                "game_type": "arena",
                "updated_date": end_time.isoformat(),
            },
        )
        move_records: list[rshogi.record.MoveRecord] = []
        for idx, move in enumerate(moves):
            wall_time = wall_times_ms[idx] if idx < len(wall_times_ms) else None
            latency_delta = latency_deltas_ms[idx] if idx < len(latency_deltas_ms) else None
            engine_info = rshogi.record.MoveEngineInfo(
                eval=eval_values[idx] if idx < len(eval_values) else None,
                nodes=nodes_values[idx] if idx < len(nodes_values) else None,
                depth=depth_values[idx] if idx < len(depth_values) else None,
                seldepth=seldepth_values[idx] if idx < len(seldepth_values) else None,
                wall_time_ms=int(wall_time) if wall_time is not None else None,
                latency_delta_ms=int(latency_delta) if latency_delta is not None else None,
            )
            move_records.append(
                rshogi.record.MoveRecord(
                    Move(move),
                    time_ms=move_times_ms[idx] if idx < len(move_times_ms) else None,
                    engine_info=engine_info,
                )
            )
        init_sfen = normalized_sfen if normalized_sfen != "startpos" else STARTING_SFEN
        terminal = rshogi.record.SpecialMoveRecord(game_result_terminal_kind(game_result), game_result)

        logger.debug(f"Game {game_id} completed: {game_result}")
        return rshogi.record.GameRecord.from_main_line(init_sfen, move_records, terminal, record_metadata)

    async def _prepare_engines_for_game(
        self,
        black_engine: GameEngineProtocol,
        white_engine: GameEngineProtocol,
        initial_sfen: str,
    ) -> None:
        """Prepare engines for a new game."""
        if self._shutting_down:
            return
        black_ready = getattr(black_engine, "prepare_ready_state", None)
        white_ready = getattr(white_engine, "prepare_ready_state", None)
        black_new_game = getattr(black_engine, "prepare_new_game_position", None)
        white_new_game = getattr(white_engine, "prepare_new_game_position", None)

        if callable(black_ready) and callable(white_ready) and callable(black_new_game) and callable(white_new_game):
            # Two-phase barrier:
            # 1) Wait until both engines are ready (readyok observed by each engine instance).
            # 2) Start game setup for both sides (usinewgame + position) in parallel.
            await asyncio.gather(
                black_ready(),
                white_ready(),
            )
            await asyncio.gather(
                black_new_game(initial_sfen=initial_sfen),
                white_new_game(initial_sfen=initial_sfen),
            )
        else:
            await asyncio.gather(
                black_engine.prepare(initial_sfen=initial_sfen),
                white_engine.prepare(initial_sfen=initial_sfen),
            )
        self._publish_engine_options_snapshot(black_engine)
        self._publish_engine_options_snapshot(white_engine)

    def _publish_engine_options_snapshot(self, engine: GameEngineProtocol) -> None:
        callback = self._engine_options_callback
        if callback is None:
            return
        name = getattr(engine, "options_name", None) or getattr(engine, "name", None)
        if not isinstance(name, str) or not name:
            return
        try:
            snapshot_fn = getattr(engine, "get_usi_options_snapshot", None)
            options = snapshot_fn() if callable(snapshot_fn) else None
            if not options:
                return
            info_fn = getattr(engine, "get_engine_info_snapshot", None)
            info = info_fn() if callable(info_fn) else None
        except (RuntimeError, ValueError, TypeError, AttributeError) as exc:
            logger.debug("Failed to capture USI options for %s: %s", name, exc, exc_info=True)
            return
        callback(name, options, info if isinstance(info, dict) and info else None)
        self._published_engine_options.add(name)

    async def _game_loop(
        self,
        board: Board,
        black_engine: GameEngineProtocol,
        white_engine: GameEngineProtocol,
        initial_sfen: str,
        start_ply_number: int,
        moves: list[int],
        usi_moves: list[str],
        eval_values: list[int | None],
        nodes_values: list[int | None],
        depth_values: list[int | None],
        seldepth_values: list[int | None],
        move_times_ms: list[int | None],
        wall_times_ms: list[int | None],
        latency_deltas_ms: list[int | None],
        black_time_control: TimeControl,
        white_time_control: TimeControl,
        game_id: str | None = None,
        adjudicator: Adjudicator | None = None,
        result_progress_emitted: list[bool] | None = None,
    ) -> GameResult:
        """
        Main game loop.

        Returns:
            GameResult indicating the outcome
        """
        # Exit immediately if shutdown requested
        if self._shutting_down:
            raise asyncio.CancelledError()
        initial_ply = max(int(board.game_ply) - 1, 0)
        ply_count = initial_ply
        max_plies: int | None = None
        if adjudicator and self.adjudication_config and self.adjudication_config.max_plies_enabled:
            max_plies = self.adjudication_config.max_plies

        # Pre-compute time control spec strings for UI (per-side)
        time_control_black_str: str = black_time_control.limits.to_spec_str()
        time_control_white_str: str = white_time_control.limits.to_spec_str()

        while True:
            # Determine current player and engine
            if self._shutting_down:
                raise asyncio.CancelledError()
            is_black_turn = int(board.turn) == _BLACK
            current_engine = black_engine if is_black_turn else white_engine
            player_name = "Black" if is_black_turn else "White"

            # Get time control instances
            current_time_control: TimeControl = black_time_control if is_black_turn else white_time_control
            enemy_time_control: TimeControl = white_time_control if is_black_turn else black_time_control

            logger.debug(f"Ply {ply_count + 1}: {player_name} to move")

            # Check for game end conditions
            # 1. Check for checkmate (no legal moves)
            if board.is_mated():
                # In shogi, no legal moves means checkmate (current player loses)
                logger.debug(f"Game ended by checkmate - {player_name} is checkmated")
                return GameResult.WHITE_WIN if is_black_turn else GameResult.BLACK_WIN

            # 2. Check for entering king declaration win
            if board.can_declare_win():
                # Current player wins by entering king declaration
                logger.debug(f"Game ended by nyugyoku declaration - {player_name} wins")
                return GameResult.BLACK_WIN if is_black_turn else GameResult.WHITE_WIN

            # 3. Check for time expiry
            if current_time_control.expired():
                # If allow_timeout is enabled, do not end the game on time expiry
                if current_time_control.limits.allow_timeout:
                    logger.debug(f"Time expired for {player_name}, but allow_timeout=True (continuing game)")
                else:
                    logger.debug(f"Game ended by time expiry - {player_name} loses on time")
                    winner_color = Color.WHITE if is_black_turn else Color.BLACK
                    return timeout_win_result(winner_color)

            # Build think request from time controls
            think_request = request_from_time_controls(
                my_limits=current_time_control.limits,
                enemy_limits=enemy_time_control.limits,
                my_is_black=is_black_turn,
                my_remaining_ms=current_time_control.active_time_left_ms(),
                enemy_remaining_ms=enemy_time_control.active_time_left_ms(),
            )
            logger.debug(
                "Think request for %s: %s",
                current_engine.name,
                think_request.to_command(),
            )

            last_move_usi = usi_moves[-1] if usi_moves else None
            use_ponder = False
            ponder_timings: PonderHitTimings | None = None

            # Start move clock before any ponder synchronization so cancel_ponder
            # latency is also charged to the side to move.
            current_time_control.start_timer()
            go_start_time = time.perf_counter()
            await self._enqueue_clock_start(
                game_id=game_id,
                ply=ply_count,
                start_ply_number=start_ply_number,
                active_is_black=is_black_turn,
                black_remaining_ms=black_time_control.active_time_left_ms(),
                white_remaining_ms=white_time_control.active_time_left_ms(),
                time_control_black=time_control_black_str,
                time_control_white=time_control_white_str,
                black_limits=black_time_control.limits,
                white_limits=white_time_control.limits,
                initial_sfen=initial_sfen,
                black_name=black_engine.name,
                white_name=white_engine.name,
            )

            if current_engine.has_active_ponder():
                predicted = current_engine.active_ponder_predicted_move()
                if predicted is not None and predicted.to_usi() == last_move_usi:
                    use_ponder = True
                    ponder_timings = self._build_ponder_hit_timings(
                        current_time_control=current_time_control,
                        enemy_time_control=enemy_time_control,
                        is_black_turn=is_black_turn,
                    )
                    logger.debug("Ponderhit attempt for %s (predicted %s)", current_engine.name, predicted.to_usi())
                else:
                    wait_timeout = current_time_control.get_timeout_for_wait()
                    wait_timeout_f = 1.0 if wait_timeout is None else float(wait_timeout)
                    cancel_timeout = min(wait_timeout_f, 1.0)
                    await current_engine.cancel_ponder(timeout=cancel_timeout)

            # Calculate timeout for wait_bestmove
            timeout_seconds = current_time_control.get_timeout_for_wait()

            # Wait for bestmove
            try:
                if self._shutting_down:
                    raise asyncio.CancelledError()
                if use_ponder:
                    ponder_result = await current_engine.ponder_hit(
                        timings=ponder_timings,
                        timeout=timeout_seconds,
                    )
                    if ponder_result is None:
                        logger.debug(
                            "Ponder hit returned no result, falling back to fresh go for %s", current_engine.name
                        )
                        think_result = await current_engine.think(
                            sfen=initial_sfen,
                            moves=tuple(usi_moves),
                            request=think_request,
                            timeout=timeout_seconds,
                        )
                    else:
                        think_result = ponder_result
                else:
                    think_result = await current_engine.think(
                        sfen=initial_sfen,
                        moves=tuple(usi_moves),
                        request=think_request,
                        timeout=timeout_seconds,
                    )

                # Calculate elapsed time as fallback for time_ms
                elapsed_ms = int((time.perf_counter() - go_start_time) * 1000)
                move_result = await self._process_move_result(board, think_result, current_engine.name)

                if move_result["game_over"]:
                    game_result: GameResult = cast(_GameOverResult, move_result)["result"]
                    if result_progress_emitted is not None and not result_progress_emitted[0]:
                        await self._enqueue_terminal_progress(
                            game_id=game_id,
                            ply_index=len(moves) + 1,
                            start_ply_number=start_ply_number,
                            initial_sfen=initial_sfen,
                            black_name=black_engine.name,
                            white_name=white_engine.name,
                            board_sfen=board.to_sfen(),
                            result=game_result,
                            think_result=think_result,
                            elapsed_ms=elapsed_ms,
                        )
                        result_progress_emitted[0] = True
                    return game_result

                # Record the move value for downstream processing
                move = cast(_MoveContinue, move_result)["move"]

                # Prepare KI2 notation BEFORE applying the move
                ki2_move = board.move32_from_move(move).to_ki2(board) or move.to_usi()
                side_that_moved_is_black = int(board.turn) == _BLACK

                # Accumulate search statistics for database storage and apply move/time updates
                apply_start = time.perf_counter()
                (
                    result_after_apply,
                    ply_count,
                    eval_value,
                    search_stats,
                    wall_time_sample,
                ) = await self._apply_move_common(
                    board=board,
                    move=move,
                    think_result=think_result,
                    elapsed_ms=elapsed_ms,
                    moves=moves,
                    usi_moves=usi_moves,
                    eval_values=eval_values,
                    nodes_values=nodes_values,
                    depth_values=depth_values,
                    seldepth_values=seldepth_values,
                    move_times_ms=move_times_ms,
                    wall_times_ms=wall_times_ms,
                    latency_deltas_ms=latency_deltas_ms,
                    current_time_control=current_time_control,
                    black_time_control=black_time_control,
                    white_time_control=white_time_control,
                    game_id=game_id,
                    ply_count=ply_count,
                    adjudicator=adjudicator,
                    side_that_moved_is_black=side_that_moved_is_black,
                    repetition_occurrences_to_draw=self.repetition_occurrences_to_draw,
                )
                apply_elapsed_ms = (time.perf_counter() - apply_start) * 1000.0
                if apply_elapsed_ms >= self._apply_move_log_threshold_ms:
                    print(
                        f"[apply] game={game_id} ply={ply_count} duration_ms={apply_elapsed_ms:.1f}",
                        flush=True,
                    )
                game_finished_after_move = result_after_apply is not None
                hit_max_plies = max_plies is not None and ply_count >= max_plies

                # Report progress if queue is available. Emit before early returns so that
                # dashboards receive the terminal move even when max-plies adjudication fires.
                await self._enqueue_move_progress(
                    game_id=game_id,
                    ply_index=len(moves),
                    start_ply_number=start_ply_number,
                    initial_sfen=initial_sfen,
                    black_name=black_engine.name,
                    white_name=white_engine.name,
                    board_sfen=board.to_sfen(),
                    usi_move=move.to_usi(),
                    ki2_move=ki2_move,
                    eval_cp=eval_value,
                    depth=search_stats["depth"],
                    seldepth=search_stats["seldepth"],
                    nodes=search_stats["nodes"],
                    time_ms=search_stats["time_ms"],
                    wall_time_ms=wall_time_sample,
                )

                # Enforce max plies if enabled
                if hit_max_plies:
                    logger.debug(f"Game ended by ply limit ({max_plies})")
                    return GameResult.DRAW_BY_MAX_PLIES

                if game_finished_after_move:
                    assert result_after_apply is not None
                    return result_after_apply

                await self._maybe_start_ponder(
                    engine=current_engine,
                    think_result=think_result,
                    is_black_turn=is_black_turn,
                    initial_sfen=initial_sfen,
                    usi_moves=usi_moves,
                    current_time_control=current_time_control,
                    enemy_time_control=enemy_time_control,
                )

            except asyncio.TimeoutError:
                if self._shutting_down:
                    raise asyncio.CancelledError() from None
                logger.warning(f"Engine {current_engine.name} timed out")

                if use_ponder:
                    recovered_think = await current_engine.cancel_ponder(timeout=timeout_seconds)
                else:
                    recovered_think = await current_engine.stop()

                if recovered_think is None:
                    if current_time_control.limits.allow_timeout:
                        logger.debug(
                            f"Timeout recovery failed for {player_name}, but allow_timeout=True (treat as draw)"
                        )
                        return GameResult.DRAW_BY_MAX_PLIES
                    logger.debug(f"Timeout recovery failed - {player_name} loses on time")
                    winner_color = Color.WHITE if is_black_turn else Color.BLACK
                    return timeout_win_result(winner_color)

                result_after_recovery, new_ply_count = await self._handle_recovered_bestmove(
                    board=board,
                    think_result=recovered_think,
                    current_engine=current_engine,
                    go_start_time=go_start_time,
                    moves=moves,
                    usi_moves=usi_moves,
                    eval_values=eval_values,
                    nodes_values=nodes_values,
                    depth_values=depth_values,
                    seldepth_values=seldepth_values,
                    move_times_ms=move_times_ms,
                    wall_times_ms=wall_times_ms,
                    latency_deltas_ms=latency_deltas_ms,
                    current_time_control=current_time_control,
                    black_time_control=black_time_control,
                    white_time_control=white_time_control,
                    game_id=game_id,
                    ply_count=ply_count,
                    start_ply_number=start_ply_number,
                    initial_sfen=initial_sfen,
                    black_name=black_engine.name,
                    white_name=white_engine.name,
                    player_name=player_name,
                    is_black_turn=is_black_turn,
                    adjudicator=adjudicator,
                    repetition_occurrences_to_draw=self.repetition_occurrences_to_draw,
                    result_progress_emitted=result_progress_emitted,
                )
                ply_count = new_ply_count
                if result_after_recovery is not None:
                    return result_after_recovery
                continue

            except asyncio.CancelledError:
                raise

            except (OSError, RuntimeError, ValueError) as exc:
                if self._shutting_down:
                    logger.debug("Suppressed move error during shutdown for %s", current_engine.name)
                else:
                    logger.exception("Error during move by %s: %s", current_engine.name, exc)
                # Error results in loss for the current player
                return GameResult.WHITE_WIN if is_black_turn else GameResult.BLACK_WIN

    def _build_ponder_hit_timings(
        self,
        *,
        current_time_control: TimeControl,
        enemy_time_control: TimeControl,
        is_black_turn: bool,
    ) -> PonderHitTimings:
        current_limits = current_time_control.limits
        enemy_limits = enemy_time_control.limits
        request = request_from_time_controls(
            my_limits=current_limits,
            enemy_limits=enemy_limits,
            my_is_black=is_black_turn,
            my_remaining_ms=current_time_control.active_time_left_ms(),
            enemy_remaining_ms=enemy_time_control.active_time_left_ms(),
        )
        return PonderHitTimings(
            btime=request.btime,
            wtime=request.wtime,
            binc=request.binc,
            winc=request.winc,
            byoyomi=request.byoyomi,
        )

    async def _maybe_start_ponder(
        self,
        *,
        engine: GameEngineProtocol,
        think_result: UsiThinkResult,
        is_black_turn: bool,
        initial_sfen: str,
        usi_moves: list[str],
        current_time_control: TimeControl,
        enemy_time_control: TimeControl,
    ) -> None:
        predicted = think_result.ponder
        if predicted is None:
            await engine.cancel_ponder(timeout=0.2)
            return
        ponder_moves = list(usi_moves)
        ponder_moves.append(predicted.to_usi())
        try:
            ponder_request = request_from_time_controls(
                my_limits=current_time_control.limits,
                enemy_limits=enemy_time_control.limits,
                my_is_black=is_black_turn,
                my_remaining_ms=current_time_control.active_time_left_ms(),
                enemy_remaining_ms=enemy_time_control.active_time_left_ms(),
            )
            ponder_request = replace(ponder_request, ponder=True)
            await engine.start_ponder(
                sfen=initial_sfen,
                moves=tuple(ponder_moves),
                request=ponder_request,
                predicted_move=predicted,
            )
        except (OSError, RuntimeError, ValueError, asyncio.TimeoutError) as exc:
            logger.debug("Failed to start ponder for %s: %s", engine.name, exc, exc_info=True)
            await engine.cancel_ponder(timeout=0.2)

    async def _finalize_game(
        self, black_engine: GameEngineProtocol, white_engine: GameEngineProtocol, game_result: GameResult
    ) -> None:
        """Notify engines about game result using the protocol abstraction."""
        tasks = {
            "black": black_engine.notify_gameover(game_result),
            "white": white_engine.notify_gameover(game_result),
        }
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)
        failures = {
            label: result for label, result in zip(tasks.keys(), results, strict=False) if isinstance(result, Exception)
        }
        if failures:
            if self._shutting_down:
                for label, failure in failures.items():
                    logger.debug("Suppressed %s gameover error during shutdown: %s", label, failure)
                return
            for label, failure in failures.items():
                logger.error("Engine %s gameover notification failed: %s", label, failure, exc_info=failure)
            message = ", ".join(f"{label}={type(exc).__name__}:{exc}" for label, exc in failures.items())
            first_exc = next(iter(failures.values()))
            raise RuntimeError(f"Engine gameover notification failed: {message}") from first_exc

    # --- Typed results for move processing ---------------------------------

    async def _process_move_result(
        self, board: Board, think_result: UsiThinkResult, engine_name: str
    ) -> _GameMoveResult:
        """
        Process the move result from engine.

        Returns:
            Typed dict indicating either game termination or next move
        """
        bestmove = think_result.bestmove

        # Handle special moves
        if bestmove == Move.MOVE_RESIGN:
            logger.debug(f"Engine {engine_name} resigned")
            # Current player loses
            result = GameResult.WHITE_WIN if int(board.turn) == _BLACK else GameResult.BLACK_WIN
            return _GameOverResult(game_over=True, result=result)

        if bestmove == Move.MOVE_WIN:
            logger.debug(f"Engine {engine_name} declared win")
            # Current player wins
            result = GameResult.BLACK_WIN if int(board.turn) == _BLACK else GameResult.WHITE_WIN
            return _GameOverResult(game_over=True, result=result)

        # Validate normal move
        assert bestmove is not None  # Already handled resign/win above
        if not board.is_legal_move(bestmove):
            logger.warning(f"Illegal move from {engine_name}: {bestmove.to_usi()}")
            result = GameResult.WHITE_WIN if int(board.turn) == _BLACK else GameResult.BLACK_WIN
            return _GameOverResult(game_over=True, result=result)

        return _MoveContinue(game_over=False, move=bestmove)

    def _extract_evaluation(self, think_result: UsiThinkResult) -> int | None:
        """Extract evaluation value from think result.

        Returns evaluation from the engine's perspective (positive = good for engine).
        """
        if think_result.pvs:
            pv = think_result.pvs[0]
            if pv.eval is not None:
                return int(pv.eval)
        return None

    def _extract_search_statistics(
        self, think_result: UsiThinkResult, fallback_time_ms: int | None = None
    ) -> dict[str, int | None]:
        """Extract search statistics from think result.

        Args:
            think_result: The USI think result from engine
            fallback_time_ms: Fallback time in milliseconds if engine doesn't report time

        Returns dict with depth, seldepth, nodes, time_ms.
        """
        stats: dict[str, int | None] = {"depth": None, "seldepth": None, "nodes": None, "time_ms": None}

        # Get the last PV which should have the most recent stats
        last_pv = think_result.get_last_pv()
        if last_pv:
            stats["depth"] = last_pv.depth
            stats["seldepth"] = last_pv.seldepth
            stats["nodes"] = last_pv.nodes
            stats["time_ms"] = last_pv.time  # time is already in ms

            # Use fallback time if engine didn't report time
            if stats["time_ms"] is None and fallback_time_ms is not None:
                stats["time_ms"] = fallback_time_ms

        return stats

    async def _notify_clock_increment(
        self,
        *,
        game_id: str | None,
        ply_count: int,
        side_that_moved_is_black: bool,
        current_time_control: TimeControl,
        black_time_control: TimeControl,
        white_time_control: TimeControl,
        pre_black_remain_ms: int,
        pre_white_remain_ms: int,
    ) -> None:
        """Notify UI about clock increment."""
        payload: _ClockIncrementPayload = {
            "type": "clock_increment",
            "game_id": game_id,
            "side": "black" if side_that_moved_is_black else "white",
            "applied_increment_ms": int(current_time_control.limits.increment_ms or 0),
            "pre_black_remain_ms": int(pre_black_remain_ms),
            "pre_white_remain_ms": int(pre_white_remain_ms),
            "black_remain_ms": int(black_time_control.active_time_left_ms()),
            "white_remain_ms": int(white_time_control.active_time_left_ms()),
            "occurred_at_ms": int(time.time() * 1000),
        }
        await self._enqueue_progress(game_id, ply_count, payload)

    async def _handle_recovered_bestmove(
        self,
        *,
        board: Board,
        think_result: UsiThinkResult,
        current_engine: GameEngineProtocol,
        go_start_time: float,
        moves: list[int],
        usi_moves: list[str],
        eval_values: list[int | None],
        nodes_values: list[int | None],
        depth_values: list[int | None],
        seldepth_values: list[int | None],
        move_times_ms: list[int | None],
        wall_times_ms: list[int | None],
        latency_deltas_ms: list[int | None],
        current_time_control: TimeControl,
        black_time_control: TimeControl,
        white_time_control: TimeControl,
        game_id: str | None,
        ply_count: int,
        start_ply_number: int,
        initial_sfen: str,
        black_name: str,
        white_name: str,
        player_name: str,
        is_black_turn: bool,
        adjudicator: Adjudicator | None,
        repetition_occurrences_to_draw: int,
        result_progress_emitted: list[bool] | None = None,
    ) -> "RecoveredBestmoveResult":
        """
        Process recovered bestmove after a timeout stop.

        Returns (game_result_or_none, new ply count). If result is None, caller continues loop.
        """
        elapsed_ms = int((time.perf_counter() - go_start_time) * 1000)
        move_result = await self._process_move_result(board, think_result, current_engine.name)

        if move_result["game_over"]:
            result = cast(_GameOverResult, move_result)["result"]
            assert isinstance(result, GameResult), f"Expected GameResult, got {type(result)}"
            if result_progress_emitted is not None and not result_progress_emitted[0]:
                await self._enqueue_terminal_progress(
                    game_id=game_id,
                    ply_index=len(moves) + 1,
                    start_ply_number=start_ply_number,
                    initial_sfen=initial_sfen,
                    black_name=black_name,
                    white_name=white_name,
                    board_sfen=board.to_sfen(),
                    result=result,
                    think_result=think_result,
                    elapsed_ms=elapsed_ms,
                )
                result_progress_emitted[0] = True
            return RecoveredBestmoveResult(result=result, ply_count=ply_count)

        # Strict timeout policy: if time already expired and allow_timeout is False,
        # treat as loss even if a late bestmove arrived after stop.
        allow_timeout = current_time_control.limits.allow_timeout
        if current_time_control.expired() and not allow_timeout:
            logger.debug(f"Time expired for {player_name} before applying increment; strict timeout -> loss on time")
            winner_color = Color.WHITE if is_black_turn else Color.BLACK
            return RecoveredBestmoveResult(result=timeout_win_result(winner_color), ply_count=ply_count)

        # Apply recovered move using common path
        move = cast(_MoveContinue, move_result)["move"]
        side_that_moved_is_black = int(board.turn) == _BLACK
        (
            result_after_apply,
            new_ply_count,
            _eval_value,
            _search_stats,
            _wall_sample,
        ) = await self._apply_move_common(
            board=board,
            move=move,
            think_result=think_result,
            elapsed_ms=elapsed_ms,
            moves=moves,
            usi_moves=usi_moves,
            eval_values=eval_values,
            nodes_values=nodes_values,
            depth_values=depth_values,
            seldepth_values=seldepth_values,
            move_times_ms=move_times_ms,
            wall_times_ms=wall_times_ms,
            latency_deltas_ms=latency_deltas_ms,
            current_time_control=current_time_control,
            black_time_control=black_time_control,
            white_time_control=white_time_control,
            game_id=game_id,
            ply_count=ply_count,
            adjudicator=adjudicator,
            side_that_moved_is_black=side_that_moved_is_black,
            repetition_occurrences_to_draw=repetition_occurrences_to_draw,
        )
        return RecoveredBestmoveResult(result=result_after_apply, ply_count=new_ply_count)

    async def _apply_move_common(
        self,
        *,
        board: Board,
        move: Move,
        think_result: UsiThinkResult,
        elapsed_ms: int,
        moves: list[int],
        usi_moves: list[str],
        eval_values: list[int | None],
        nodes_values: list[int | None],
        depth_values: list[int | None],
        seldepth_values: list[int | None],
        move_times_ms: list[int | None],
        wall_times_ms: list[int | None],
        latency_deltas_ms: list[int | None],
        current_time_control: TimeControl,
        black_time_control: TimeControl,
        white_time_control: TimeControl,
        game_id: str | None,
        ply_count: int,
        adjudicator: Adjudicator | None,
        side_that_moved_is_black: bool,
        repetition_occurrences_to_draw: int,
    ) -> "ApplyMoveCommonResult":
        """Common post-bestmove routine used by normal and recovery paths."""
        # Record the move and statistics
        moves.append(int(move))
        usi_moves.append(move.to_usi())
        eval_value = self._extract_evaluation(think_result)
        eval_values.append(eval_value)
        search_stats = self._extract_search_statistics(think_result, elapsed_ms)
        nodes_values.append(search_stats["nodes"])
        depth_values.append(search_stats["depth"])
        seldepth_values.append(search_stats["seldepth"])
        move_times_ms.append(search_stats["time_ms"])
        wall_sample: int | None = int(elapsed_ms)
        wall_times_ms.append(wall_sample)
        latency_deltas_ms.append(None)

        # Apply move
        board.apply_move(move)
        ply_count += 1
        # Repetition detection using rshogi native API.
        # is_repetition(threshold) fires when repetition_counter >= threshold,
        # where threshold = repetition_occurrences_to_draw - 1 maps occurrences to the
        # internal counter (e.g. 2 occurrences → threshold 1, 4 occurrences → threshold 3).
        # When threshold >= 3 (official 4-occurrence rule), repetition_state() also fires
        # and provides WIN/LOSE/DRAW detail for perpetual check adjudication.
        repetition_result: GameResult | None = None
        if board.is_repetition(repetition_occurrences_to_draw - 1):
            if repetition_occurrences_to_draw >= 4:
                repetition_state = board.repetition_state()
                if repetition_state == RepetitionState.DRAW:
                    repetition_result = GameResult.DRAW_BY_REPETITION
                elif repetition_state == RepetitionState.WIN:
                    # RepetitionState.WIN means the current side to move (opponent of the mover) wins.
                    repetition_result = GameResult.WHITE_WIN if side_that_moved_is_black else GameResult.BLACK_WIN
                elif repetition_state == RepetitionState.LOSE:
                    repetition_result = GameResult.BLACK_WIN if side_that_moved_is_black else GameResult.WHITE_WIN
                # SUPERIOR/INFERIOR: not a forced result, let the game continue
            else:
                repetition_result = GameResult.DRAW_BY_REPETITION

        # Check immediate game termination caused by the move (e.g., checkmate)
        if board.is_mated():
            winner_result = GameResult.BLACK_WIN if side_that_moved_is_black else GameResult.WHITE_WIN
            return ApplyMoveCommonResult(
                result=winner_result,
                ply_count=ply_count,
                eval_value=eval_value,
                search_stats=search_stats,
                wall_time_ms=wall_sample,
            )

        # Update time control and notify
        pre_black_remain = black_time_control.active_time_left_ms()
        pre_white_remain = white_time_control.active_time_left_ms()
        current_time_control.update_after_move(apply_increment=True)
        notify_start = time.perf_counter()
        await self._notify_clock_increment(
            game_id=game_id,
            ply_count=ply_count,
            side_that_moved_is_black=side_that_moved_is_black,
            current_time_control=current_time_control,
            black_time_control=black_time_control,
            white_time_control=white_time_control,
            pre_black_remain_ms=pre_black_remain,
            pre_white_remain_ms=pre_white_remain,
        )
        notify_elapsed_ms = (time.perf_counter() - notify_start) * 1000.0
        if notify_elapsed_ms >= self._clock_notify_log_threshold_ms:
            print(
                f"[clock-notify] game={game_id} ply={ply_count} duration_ms={notify_elapsed_ms:.1f}",
                flush=True,
            )
        if current_time_control.expired() and not current_time_control.limits.allow_timeout:
            logger.debug("Time expired after move; strict timeout -> loss on time")
            winner_color = Color.WHITE if side_that_moved_is_black else Color.BLACK
            return ApplyMoveCommonResult(
                result=timeout_win_result(winner_color),
                ply_count=ply_count,
                eval_value=eval_value,
                search_stats=search_stats,
                wall_time_ms=wall_sample,
            )

        # Adjudication
        if adjudicator:
            pv_info = think_result.pvs[0] if think_result.pvs else None
            eval_cp = pv_info.eval if pv_info else None
            score_type = "cp" if pv_info and pv_info.eval is not None else None
            adjudication_result = adjudicator.update(
                eval_cp=eval_cp,
                score_type=score_type,
                ply=ply_count,
                side_to_move_is_black=side_that_moved_is_black,
            )
            if adjudication_result:
                logger.debug(f"Game adjudicated: {adjudication_result}")
                return ApplyMoveCommonResult(
                    result=adjudication_result,
                    ply_count=ply_count,
                    eval_value=eval_value,
                    search_stats=search_stats,
                    wall_time_ms=wall_sample,
                )
        return ApplyMoveCommonResult(
            result=repetition_result,
            ply_count=ply_count,
            eval_value=eval_value,
            search_stats=search_stats,
            wall_time_ms=wall_sample,
        )
