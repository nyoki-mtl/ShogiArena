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


class _ScriptedThinkResult:
    def __init__(self, bestmove_usi: str, *, ponder_usi: str | None = None) -> None:
        from rsshogi.core import Move

        self.bestmove = Move.MOVE_RESIGN if bestmove_usi == "resign" else Move.from_usi(bestmove_usi)
        self.ponder = Move.from_usi(ponder_usi) if ponder_usi is not None else None
        self.pvs: tuple[object, ...] = ()
        self.info_strings: tuple[str, ...] = ()

    def get_last_pv(self) -> None:
        return None


class _ScriptedGameEngine:
    def __init__(
        self,
        name: str,
        script: list[tuple[str, str | None]],
    ) -> None:
        self.name = name
        self._script = list(script)
        self.prepared_sfens: list[str] = []
        self.think_positions: list[tuple[str, tuple[str, ...]]] = []
        self.ponder_positions: list[tuple[str, tuple[str, ...]]] = []

    async def prepare(self, *, initial_sfen: str) -> None:
        self.prepared_sfens.append(initial_sfen)

    async def think(
        self,
        *,
        sfen: str,
        moves: Any,
        request: Any,
        info_handler: Any = None,
        timeout: float | None = None,
    ) -> _ScriptedThinkResult:
        del request, info_handler, timeout
        self.think_positions.append((sfen, tuple(m.to_usi() for m in moves)))
        bestmove_usi, ponder_usi = self._script.pop(0) if self._script else ("resign", None)
        return _ScriptedThinkResult(bestmove_usi, ponder_usi=ponder_usi)

    async def think_mate(self, **_kwargs: Any) -> Any:
        return None

    async def analyze(self, **_kwargs: Any) -> Any:
        return None

    async def start_ponder(
        self,
        *,
        sfen: str,
        moves: Any,
        request: Any,
        predicted_move: Any,
        info_handler: Any = None,
        should_enable_early_ponder: bool | None = None,
    ) -> None:
        del request, predicted_move, info_handler, should_enable_early_ponder
        self.ponder_positions.append((sfen, tuple(m.to_usi() for m in moves)))

    async def ponder_hit(self, *, timings: Any, timeout: float | None = None) -> None:
        del timings, timeout
        return None

    async def cancel_ponder(self, *, timeout: float | None = None) -> None:
        del timeout
        return None

    def has_active_ponder(self) -> bool:
        return False

    def active_ponder_predicted_move(self) -> None:
        return None

    async def notify_gameover(self, result: GameResult) -> None:
        del result

    async def stop(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    def get_usi_options_snapshot(self) -> dict[str, Any]:
        return {}

    def get_engine_info_snapshot(self) -> dict[str, str]:
        return {}

    def register_io_log_handler(self, _handler: Any) -> Any:
        return lambda: None

    def register_lifecycle_handler(self, _handler: Any) -> Any:
        return lambda: None


@pytest.mark.asyncio
async def test_game_runner_forwards_opening_move_history_and_ponder_to_engines() -> None:
    from shogiarena._core.contexts.match.application.runner import GameRunner

    runner = GameRunner(
        time_control_limits=TimeControlLimits(time_ms=5000, increment_ms=0),
    )
    black = _ScriptedGameEngine("black", [("2g2f", "8c8d"), ("resign", None)])
    white = _ScriptedGameEngine("white", [("8c8d", None)])

    post_opening_sfen = "lnsgkgsnl/1r5b1/pppppp1pp/6p2/9/2P6/PP1PPPPPP/1B5R1/LNSGKGSNL b - 3"
    record = await runner.run_game(
        black,
        white,
        initial_sfen=post_opening_sfen,
        game_id="g-usi-line",
        opening_base_sfen="startpos",
        opening_moves=("7g7f", "3c3d"),
    )

    assert black.prepared_sfens == ["startpos"]
    assert white.prepared_sfens == ["startpos"]
    assert black.think_positions == [
        ("startpos", ("7g7f", "3c3d")),
        ("startpos", ("7g7f", "3c3d", "2g2f", "8c8d")),
    ]
    assert white.think_positions == [
        ("startpos", ("7g7f", "3c3d", "2g2f")),
    ]
    assert black.ponder_positions == [
        ("startpos", ("7g7f", "3c3d", "2g2f", "8c8d")),
    ]
    assert record.init_position_sfen == post_opening_sfen
    assert [m.move.to_usi() for m in record.moves] == ["2g2f", "8c8d"]
    assert record.result == GameResult.WHITE_WIN


@pytest.mark.asyncio
async def test_game_runner_detects_fourfold_repetition_spanning_opening_moves() -> None:
    from shogiarena._core.contexts.match.application.runner import GameRunner

    runner = GameRunner(
        time_control_limits=TimeControlLimits(time_ms=5000, increment_ms=0),
        repetition_occurrences_to_draw=4,
    )
    # Opening moves return to the starting board arrangement once (2 occurrences in opening history).
    opening_moves = ("5i5h", "5a5b", "5h5i", "5b5a")
    post_opening_sfen = "lnsgkgsnl/1r5b1/ppppppppp/9/9/9/PPPPPPPPP/1B5R1/LNSGKGSNL b - 5"
    # Playing two more king-shuffle cycles reaches the 4th occurrence on move 8.
    black = _ScriptedGameEngine(
        "black",
        [("5i5h", None), ("5h5i", None), ("5i5h", None), ("5h5i", None), ("7g7f", None)],
    )
    white = _ScriptedGameEngine(
        "white",
        [("5a5b", None), ("5b5a", None), ("5a5b", None), ("5b5a", None), ("3c3d", None)],
    )

    record = await runner.run_game(
        black,
        white,
        initial_sfen=post_opening_sfen,
        game_id="g-cross-rep",
        opening_base_sfen="startpos",
        opening_moves=opening_moves,
    )

    assert record.result == GameResult.DRAW_BY_REPETITION
    assert [m.move.to_usi() for m in record.moves] == [
        "5i5h",
        "5a5b",
        "5h5i",
        "5b5a",
        "5i5h",
        "5a5b",
        "5h5i",
        "5b5a",
    ]


def test_two_sided_resign_counts_agreement_for_same_winner() -> None:
    from shogiarena._core.contexts.match.domain.adjudication import AdjudicationResult, ResignTracker

    tracker = ResignTracker(resign_score_cp=800, move_count=1, is_two_sided=True)

    assert tracker.update(-900, "cp", is_side_to_move_black=True) is None
    assert tracker.update(-900, "cp", is_side_to_move_black=False) == AdjudicationResult.RESIGN_BLACK
