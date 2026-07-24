"""Handshake stalls must not be scored as a loss on time.

A `usi` / `isready` exchange happens before the engine is asked to think, so a stall there is an
orchestrator or transport problem, not the engine exceeding its clock. Recording it as
`*_WIN_BY_TIMEOUT` feeds a fabricated decisive result into Elo and SPRT, and shows up as a
0-move "timeout".
"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Sequence

import pytest
from rsshogi.core import Move

from shogiarena._core.contexts.match.application.runner import GameRunner
from shogiarena._core.contexts.match.ports.game_engine_ports import GameEnginePort, InfoHandler
from shogiarena._core.contexts.match.ports.usi_think_ports import UsiThinkRequest
from shogiarena._core.platform.engine_runtime.usi_protocol_types import UsiThinkPV, UsiThinkResult, move_from_usi
from shogiarena._core.shared.kernel.engine_errors import UsiHandshakeTimeoutError
from shogiarena._core.shared.kernel.game_results import GameResult
from shogiarena._core.shared.kernel.time_control import TimeControlLimits


class _StallingEngine(GameEnginePort):
    """Engine whose think() raises the given error instead of answering."""

    def __init__(self, name: str, error: BaseException, moves: list[str] | None = None) -> None:
        self._name = name
        self._error = error
        self._moves = deque(moves or [])

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
        if self._moves:
            move_usi = self._moves.popleft()
            mv = move_from_usi(move_usi)
            result = UsiThinkResult()
            result.bestmove = mv
            pv = UsiThinkPV()
            pv.pv = [mv]
            pv.eval = 0
            result.pvs.append(pv)
            return result
        raise self._error

    async def think_mate(self, **kwargs: object) -> UsiThinkResult:
        raise NotImplementedError

    async def analyze(self, **kwargs: object) -> UsiThinkResult:
        raise NotImplementedError

    async def notify_gameover(self, result: GameResult) -> None:
        return None

    async def stop(self) -> UsiThinkResult | None:
        return None

    async def shutdown(self) -> None:
        return None

    async def start_ponder(self, **kwargs: object) -> None:
        return None

    async def ponder_hit(self, **kwargs: object) -> UsiThinkResult | None:
        return None

    async def cancel_ponder(self, *, timeout: float | None = None) -> UsiThinkResult | None:
        return None

    def has_active_ponder(self) -> bool:
        return False

    def active_ponder_predicted_move(self) -> Move | None:
        return None


def _runner(limits: TimeControlLimits) -> GameRunner:
    return GameRunner(
        progress_queue=None,
        time_control_limits=limits,
        adjudication_config=None,
        repetition_occurrences_to_draw=4,
    )


@pytest.mark.asyncio
async def test_handshake_timeout_is_not_recorded_as_a_loss_on_time() -> None:
    limits = TimeControlLimits(fixed_time_ms=200, expiry_margin_ms=500)
    runner = _runner(limits)

    black_engine = _StallingEngine("black", UsiHandshakeTimeoutError("isready stalled"))
    white_engine = _StallingEngine("white", UsiHandshakeTimeoutError("isready stalled"))

    with pytest.raises(UsiHandshakeTimeoutError):
        await runner.run_game(
            black_engine=black_engine,
            white_engine=white_engine,
            initial_sfen="startpos",
            game_id="game_handshake_stall",
            black_time_control_limits=limits,
            white_time_control_limits=limits,
        )


@pytest.mark.asyncio
async def test_plain_bestmove_timeout_is_still_a_loss_on_time() -> None:
    limits = TimeControlLimits(fixed_time_ms=200, expiry_margin_ms=500)
    runner = _runner(limits)

    # The engine received go and failed to answer: that is a genuine loss on time and must keep
    # producing a decisive timeout result.
    black_engine = _StallingEngine("black", TimeoutError("no bestmove"))
    white_engine = _StallingEngine("white", TimeoutError("no bestmove"))

    game_info = await runner.run_game(
        black_engine=black_engine,
        white_engine=white_engine,
        initial_sfen="startpos",
        game_id="game_bestmove_timeout",
        black_time_control_limits=limits,
        white_time_control_limits=limits,
    )

    assert game_info.result == GameResult.WHITE_WIN_BY_TIMEOUT


def test_handshake_timeout_is_a_timeout_error_subclass() -> None:
    # Existing broad `except TimeoutError` handlers must keep catching it; only the game loop
    # distinguishes it, and it does so by catching the subclass first.
    assert issubclass(UsiHandshakeTimeoutError, TimeoutError)
