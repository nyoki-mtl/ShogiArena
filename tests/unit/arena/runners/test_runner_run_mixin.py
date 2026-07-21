from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any

import pytest

from shogiarena._core.contexts.match.application.runner_run_mixin import GameRunnerRunMixin
from shogiarena._core.shared.kernel.game_results import GameResult
from shogiarena._core.shared.kernel.time_control import TimeControlLimits


class _EngineStub:
    def __init__(self, name: str) -> None:
        self.name = name
        self.prepare_calls = 0

    async def prepare(self, *, initial_sfen: str) -> None:
        del initial_sfen
        self.prepare_calls += 1


class _RunHarness(GameRunnerRunMixin):
    def __init__(self) -> None:
        self.time_control_limits = TimeControlLimits(time_ms=1000, increment_ms=0)
        self.adjudication_config = None
        self.repetition_occurrences_to_draw = 4
        self._is_shutting_down = False
        self._engine_options_callback = None
        self._published_engine_options: set[str] = set()
        self.enqueued_results: list[dict[str, object]] = []
        self.finalize_calls: list[GameResult] = []

    @contextmanager
    def _engine_io_listener_context(self, *_args: object) -> Any:
        yield

    async def _game_loop(self, *_args: object, **_kwargs: object) -> GameResult:
        return GameResult.BLACK_WIN

    async def _enqueue_game_result(self, **payload: object) -> None:
        self.enqueued_results.append(dict(payload))

    async def _finalize_game(self, _black_engine: object, _white_engine: object, game_result: GameResult) -> None:
        self.finalize_calls.append(game_result)
        raise RuntimeError("gameover failed")


@pytest.mark.asyncio
async def test_run_game_keeps_result_when_finalize_fails(caplog) -> None:
    harness = _RunHarness()

    with caplog.at_level(logging.ERROR):
        record = await harness.run_game(
            _EngineStub("black"),
            _EngineStub("white"),
            game_id="g-finalize",
        )

    assert record.game_name == "g-finalize"
    assert record.result == GameResult.BLACK_WIN
    assert [payload["result"] for payload in harness.enqueued_results] == [GameResult.BLACK_WIN]
    assert harness.finalize_calls == [GameResult.BLACK_WIN]
    assert "finalization failed after result recording" in caplog.text
