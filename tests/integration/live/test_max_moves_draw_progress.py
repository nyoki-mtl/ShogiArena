import asyncio
import json
from collections import deque

import pytest

from shogiarena.arena.engines.time_control import TimeControlLimits
from shogiarena.arena.engines.usi_engine import PonderHitTimings
from shogiarena.arena.engines.usi_think import UsiThinkRequest
from shogiarena.arena.engines.usi_types import UsiThinkPV, UsiThinkResult
from shogiarena.arena.execution.game_runner import GameRunner
from shogiarena.arena.execution.types import GameEngineProtocol, InfoHandler
from shogiarena.arena.services.game_control.adjudication import AdjudicationConfig
from shogiarena.utils.types.types import GameResult


class ScriptedEngine(GameEngineProtocol):
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
        moves: tuple[str, ...] | list[str],
        request: UsiThinkRequest,
        info_handler: InfoHandler | None = None,
        timeout: float | None = None,
    ) -> UsiThinkResult:
        await asyncio.sleep(0)
        if not self._moves:
            raise AssertionError(f"{self._name} was asked to move beyond scripted sequence")
        move = self._moves.popleft()
        result = UsiThinkResult()
        result.bestmove = move
        pv = UsiThinkPV()
        pv.pv = [move]
        pv.eval = 0
        result.pvs.append(pv)
        return result

    async def think_mate(
        self,
        *,
        sfen: str,
        moves: tuple[str, ...] | list[str],
        ply_limit: int | None = None,
        timeout: float | None = None,
    ) -> UsiThinkResult:
        raise NotImplementedError

    async def analyze(
        self,
        *,
        sfen: str,
        moves: tuple[str, ...] | list[str],
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
        moves: tuple[str, ...] | list[str],
        request: UsiThinkRequest,
        predicted_move: str | None,
        info_handler: InfoHandler | None = None,
        enable_early_ponder: bool | None = None,
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

    def active_ponder_predicted_move(self) -> str | None:
        return None


@pytest.mark.asyncio
async def test_max_moves_draw_includes_terminal_move_progress() -> None:
    queue: asyncio.Queue = asyncio.Queue()
    limits = TimeControlLimits(time_ms=1000, increment_ms=0)
    adjudication_cfg = AdjudicationConfig(
        resign_enabled=False,
        max_plies_enabled=True,
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

    assert game_info.game_result == GameResult.DRAW_BY_MAX_PLIES

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

    result_updates = [payload for _move_count, payload in move_events if payload.get("result_code") is not None]
    assert result_updates, "expected move_progress with result_code"
    assert result_updates[-1]["result_code"] == GameResult.DRAW_BY_MAX_PLIES.value
