import asyncio
import json
from collections import deque
from collections.abc import Sequence

import pytest
from rshogi.core import Move

from shogiarena._core.contexts.match.application.runner import GameRunner
from shogiarena._core.contexts.match.domain.adjudication import AdjudicationConfig
from shogiarena._core.contexts.match.ports.game_engine_ports import GameEnginePort, InfoHandler
from shogiarena._core.contexts.match.ports.usi_think_ports import UsiThinkRequest
from shogiarena._core.platform.engine_runtime.usi_engine_session import PonderHitTimings
from shogiarena._core.platform.engine_runtime.usi_protocol_types import UsiThinkPV, UsiThinkResult, move_from_usi
from shogiarena._core.shared.kernel.game_results import GameResult
from shogiarena._core.shared.kernel.time_control import GameClock, TimeControlLimits


class ScriptedEngine(GameEnginePort):
    """Minimal engine that plays from a scripted sequence of USI moves."""

    def __init__(self, name: str, moves: list[str]) -> None:
        self._name = name
        self._moves = deque(moves)

    @property
    def name(self) -> str:  # type: ignore[override]
        return self._name

    async def prepare(self, *, initial_sfen: str) -> None:
        return None

    async def think(
        self,
        *,
        sfen: str,
        moves: Sequence[Move],
        request: UsiThinkRequest,
        info_handler: InfoHandler | None = None,
        timeout: float | None = None,
    ) -> UsiThinkResult:
        await asyncio.sleep(0)
        if not self._moves:
            raise AssertionError(f"{self._name} was asked to move beyond scripted sequence")
        move_usi = self._moves.popleft()
        mv = move_from_usi(move_usi)
        result = UsiThinkResult()
        result.bestmove = mv
        pv = UsiThinkPV()
        pv.pv = [mv]
        pv.eval = 0
        result.pvs.append(pv)
        return result

    async def think_mate(
        self,
        *,
        sfen: str,
        moves: Sequence[Move],
        ply_limit: int | None = None,
        timeout: float | None = None,
    ) -> UsiThinkResult:
        raise NotImplementedError

    async def analyze(
        self,
        *,
        sfen: str,
        moves: Sequence[Move],
        request: UsiThinkRequest,
        info_handler: InfoHandler | None = None,
    ) -> UsiThinkResult:
        raise NotImplementedError

    async def notify_gameover(self, result: GameResult) -> None:
        return None

    async def stop(self) -> UsiThinkResult | None:
        return None

    async def shutdown(self) -> None:
        return None

    async def start_ponder(
        self,
        *,
        sfen: str,
        moves: Sequence[Move],
        request: UsiThinkRequest,
        predicted_move: Move | None,
        info_handler: InfoHandler | None = None,
        should_enable_early_ponder: bool | None = None,
    ) -> None:
        return None

    async def ponder_hit(
        self,
        *,
        timings: PonderHitTimings | None,
        timeout: float | None = None,
    ) -> UsiThinkResult | None:
        return None

    async def cancel_ponder(self, *, timeout: float | None = None) -> UsiThinkResult | None:
        return None

    def has_active_ponder(self) -> bool:
        return False

    def active_ponder_predicted_move(self) -> Move | None:
        return None


class TrackingScriptedEngine(ScriptedEngine):
    def __init__(self, name: str, moves: list[str]) -> None:
        super().__init__(name, moves)
        self.gameover_results: list[GameResult] = []

    async def notify_gameover(self, result: GameResult) -> None:
        self.gameover_results.append(result)


def _new_result(bestmove: str, ponder: str | None = None) -> UsiThinkResult:
    result = UsiThinkResult()
    result.bestmove = move_from_usi(bestmove)
    result.ponder = move_from_usi(ponder) if ponder is not None else None
    pv = UsiThinkPV()
    pv.pv = [result.bestmove]
    pv.eval = 0
    result.pvs.append(pv)
    return result


class PonderAwareScriptedEngine(GameEnginePort):
    def __init__(
        self,
        name: str,
        think_results: list[UsiThinkResult],
        *,
        ponder_hit_results: list[UsiThinkResult] | None = None,
    ) -> None:
        self._name = name
        self._think_results = deque(think_results)
        self._ponder_hit_results = deque(ponder_hit_results or [])
        self._active_predicted: Move | None = None
        self.think_calls = 0
        self.start_ponder_calls = 0
        self.ponder_hit_calls = 0
        self.cancel_ponder_calls = 0

    @property
    def name(self) -> str:  # type: ignore[override]
        return self._name

    async def prepare(self, *, initial_sfen: str) -> None:
        return None

    async def think(
        self,
        *,
        sfen: str,
        moves: Sequence[Move],
        request: UsiThinkRequest,
        info_handler: InfoHandler | None = None,
        timeout: float | None = None,
    ) -> UsiThinkResult:
        self.think_calls += 1
        await asyncio.sleep(0)
        if not self._think_results:
            raise AssertionError(f"{self._name}: no scripted think result")
        return self._think_results.popleft()

    async def think_mate(
        self,
        *,
        sfen: str,
        moves: Sequence[Move],
        ply_limit: int | None = None,
        timeout: float | None = None,
    ) -> UsiThinkResult:
        raise NotImplementedError

    async def analyze(
        self,
        *,
        sfen: str,
        moves: Sequence[Move],
        request: UsiThinkRequest,
        info_handler: InfoHandler | None = None,
    ) -> UsiThinkResult:
        raise NotImplementedError

    async def notify_gameover(self, result: GameResult) -> None:
        return None

    async def stop(self) -> UsiThinkResult | None:
        return None

    async def shutdown(self) -> None:
        return None

    async def start_ponder(
        self,
        *,
        sfen: str,
        moves: Sequence[Move],
        request: UsiThinkRequest,
        predicted_move: Move | None,
        info_handler: InfoHandler | None = None,
        should_enable_early_ponder: bool | None = None,
    ) -> None:
        self.start_ponder_calls += 1
        self._active_predicted = predicted_move

    async def ponder_hit(
        self,
        *,
        timings: PonderHitTimings | None,
        timeout: float | None = None,
    ) -> UsiThinkResult | None:
        self.ponder_hit_calls += 1
        self._active_predicted = None
        if not self._ponder_hit_results:
            raise AssertionError(f"{self._name}: no scripted ponder-hit result")
        return self._ponder_hit_results.popleft()

    async def cancel_ponder(self, *, timeout: float | None = None) -> UsiThinkResult | None:
        self.cancel_ponder_calls += 1
        self._active_predicted = None
        return None

    def has_active_ponder(self) -> bool:
        return self._active_predicted is not None

    def active_ponder_predicted_move(self) -> Move | None:
        return self._active_predicted


class DelayedCancelPonderEngine(PonderAwareScriptedEngine):
    def __init__(
        self,
        name: str,
        think_results: list[UsiThinkResult],
        *,
        cancel_delay_s: float,
        ponder_hit_results: list[UsiThinkResult] | None = None,
    ) -> None:
        super().__init__(name, think_results, ponder_hit_results=ponder_hit_results)
        self._cancel_delay_s = cancel_delay_s

    async def cancel_ponder(self, *, timeout: float | None = None) -> UsiThinkResult | None:
        self.cancel_ponder_calls += 1
        self._active_predicted = None
        await asyncio.sleep(self._cancel_delay_s)
        return None


@pytest.mark.asyncio
async def test_max_moves_draw_includes_terminal_move_progress() -> None:
    queue: asyncio.Queue = asyncio.Queue()
    limits = TimeControlLimits(time_ms=1000, increment_ms=0)
    adjudication_cfg = AdjudicationConfig(
        is_resign_enabled=False,
        is_max_plies_enabled=True,
        max_plies=4,
    )
    runner = GameRunner(
        progress_queue=queue,
        time_control_limits=limits,
        adjudication_config=adjudication_cfg,
        repetition_occurrences_to_draw=2,
    )

    black_engine = ScriptedEngine("black", ["7g7f", "2g2f"])
    white_engine = ScriptedEngine("white", ["3c3d", "8c8d"])

    game_info = await runner.run_game(
        black_engine=black_engine,
        white_engine=white_engine,
        initial_sfen="startpos",
        game_id="game_max_moves",
        black_time_control_limits=limits,
        white_time_control_limits=limits,
    )

    assert game_info.result == GameResult.DRAW_BY_MAX_PLIES

    events: list[tuple[int, int, str | None]] = []
    while True:
        try:
            events.append(queue.get_nowait())
        except asyncio.QueueEmpty:
            break

    move_events: list[tuple[int, dict[str, object]]] = []
    for numeric_id, move_count, payload in events:
        assert numeric_id >= 0
        if not isinstance(payload, str):
            continue
        parsed = json.loads(payload)
        if parsed.get("type") == "move_progress":
            move_events.append((move_count, parsed))

    assert move_events, "move_progress events were not emitted"
    assert any(move_count >= 4 for move_count, _ in move_events), "terminal ply update missing"
    terminal_updates = [payload for move_count, payload in move_events if move_count == 4]
    assert terminal_updates, "expected move_progress at ply 4"
    assert any(update.get("move") == "8c8d" for update in terminal_updates)

    result_updates = [payload for _move_count, payload in move_events if payload.get("game_result") is not None]
    assert result_updates, "expected move_progress with game_result"
    assert result_updates[-1]["game_result"] == "DRAW_BY_MAX_PLIES"


@pytest.mark.asyncio
async def test_max_moves_draw_notifies_both_engines_with_draw_gameover() -> None:
    limits = TimeControlLimits(time_ms=1000, increment_ms=0)
    adjudication_cfg = AdjudicationConfig(
        is_resign_enabled=False,
        is_max_plies_enabled=True,
        max_plies=4,
    )
    runner = GameRunner(
        progress_queue=None,
        time_control_limits=limits,
        adjudication_config=adjudication_cfg,
        repetition_occurrences_to_draw=2,
    )

    black_engine = TrackingScriptedEngine("black", ["7g7f", "2g2f"])
    white_engine = TrackingScriptedEngine("white", ["3c3d", "8c8d"])

    game_info = await runner.run_game(
        black_engine=black_engine,
        white_engine=white_engine,
        initial_sfen="startpos",
        game_id="game_max_moves_notify",
        black_time_control_limits=limits,
        white_time_control_limits=limits,
    )

    assert game_info.result == GameResult.DRAW_BY_MAX_PLIES
    assert black_engine.gameover_results == [GameResult.DRAW_BY_MAX_PLIES]
    assert white_engine.gameover_results == [GameResult.DRAW_BY_MAX_PLIES]


@pytest.mark.asyncio
async def test_game_runner_uses_ponderhit_when_prediction_matches() -> None:
    limits = TimeControlLimits(time_ms=1000, increment_ms=0)
    runner = GameRunner(
        progress_queue=None,
        time_control_limits=limits,
        adjudication_config=AdjudicationConfig(is_resign_enabled=False),
        repetition_occurrences_to_draw=2,
    )

    black_engine = PonderAwareScriptedEngine(
        "black",
        [_new_result("7g7f", "3c3d")],
        ponder_hit_results=[_new_result("resign")],
    )
    white_engine = PonderAwareScriptedEngine("white", [_new_result("3c3d")])

    game_info = await runner.run_game(
        black_engine=black_engine,
        white_engine=white_engine,
        initial_sfen="startpos",
        game_id="ponder_match",
        black_time_control_limits=limits,
        white_time_control_limits=limits,
    )

    assert game_info.result == GameResult.WHITE_WIN
    assert black_engine.start_ponder_calls >= 1
    assert black_engine.ponder_hit_calls == 1
    assert black_engine.cancel_ponder_calls == 0
    assert black_engine.think_calls == 1


@pytest.mark.asyncio
async def test_game_runner_cancels_ponder_when_prediction_mismatches() -> None:
    limits = TimeControlLimits(time_ms=1000, increment_ms=0)
    runner = GameRunner(
        progress_queue=None,
        time_control_limits=limits,
        adjudication_config=AdjudicationConfig(is_resign_enabled=False),
        repetition_occurrences_to_draw=2,
    )

    black_engine = PonderAwareScriptedEngine(
        "black",
        [_new_result("7g7f", "8c8d"), _new_result("resign")],
    )
    white_engine = PonderAwareScriptedEngine("white", [_new_result("3c3d")])

    game_info = await runner.run_game(
        black_engine=black_engine,
        white_engine=white_engine,
        initial_sfen="startpos",
        game_id="ponder_mismatch",
        black_time_control_limits=limits,
        white_time_control_limits=limits,
    )

    assert game_info.result == GameResult.WHITE_WIN
    assert black_engine.start_ponder_calls >= 1
    assert black_engine.ponder_hit_calls == 0
    assert black_engine.cancel_ponder_calls >= 1
    assert black_engine.think_calls == 2


@pytest.mark.asyncio
async def test_game_runner_counts_cancel_ponder_latency_in_move_time() -> None:
    limits = TimeControlLimits(time_ms=1000, increment_ms=0)
    runner = GameRunner(
        progress_queue=None,
        time_control_limits=limits,
        adjudication_config=AdjudicationConfig(is_resign_enabled=False),
        repetition_occurrences_to_draw=2,
    )

    cancel_delay_s = 0.05
    black_engine = DelayedCancelPonderEngine(
        "black",
        [_new_result("7g7f", "8c8d"), _new_result("2g2f")],
        cancel_delay_s=cancel_delay_s,
    )
    white_engine = PonderAwareScriptedEngine("white", [_new_result("3c3d"), _new_result("resign")])

    game_info = await runner.run_game(
        black_engine=black_engine,
        white_engine=white_engine,
        initial_sfen="startpos",
        game_id="ponder_mismatch_latency",
        black_time_control_limits=limits,
        white_time_control_limits=limits,
    )

    assert game_info.result == GameResult.BLACK_WIN
    assert black_engine.cancel_ponder_calls >= 1
    assert len(game_info.moves) >= 3
    black_second_move = game_info.moves[2]
    assert black_second_move.time_ms is not None
    assert black_second_move.engine_info is not None
    wall_time_ms_raw = black_second_move.engine_info.extras.get("wall_time_ms")
    assert wall_time_ms_raw is not None
    wall_time_ms = int(wall_time_ms_raw)
    expected_floor_ms = int(cancel_delay_s * 1000) - 15
    assert black_second_move.time_ms >= expected_floor_ms
    assert wall_time_ms >= expected_floor_ms


def test_ponderhit_timings_do_not_mix_byoyomi_and_increment() -> None:
    runner = GameRunner(progress_queue=None, time_control_limits=TimeControlLimits(time_ms=1000, increment_ms=0))
    current = GameClock(TimeControlLimits(time_ms=10_000, byoyomi_ms=2000))
    enemy = GameClock(TimeControlLimits(time_ms=10_000, increment_ms=1000))

    timings = runner._build_ponder_hit_timings(
        current_time_control=current,
        enemy_time_control=enemy,
        is_black_turn=True,
    )

    suffix = timings.to_command_suffix()
    assert "byoyomi 2000" in suffix
    assert "binc" not in suffix
    assert "winc" not in suffix


def test_ponderhit_timings_match_go_time_adjustment_for_increment() -> None:
    runner = GameRunner(progress_queue=None, time_control_limits=TimeControlLimits(time_ms=1000, increment_ms=0))
    current = GameClock(TimeControlLimits(time_ms=30_000, increment_ms=2_000))
    enemy = GameClock(TimeControlLimits(time_ms=28_103, increment_ms=5_000))

    timings = runner._build_ponder_hit_timings(
        current_time_control=current,
        enemy_time_control=enemy,
        is_black_turn=True,
    )

    # Match shogihome/request_from_time_controls behavior:
    # advertise main time as remaining - increment, and provide increments separately.
    assert timings.btime == 28_000
    assert timings.wtime == 23_103
    assert timings.binc == 2_000
    assert timings.winc == 5_000
    assert timings.byoyomi is None
