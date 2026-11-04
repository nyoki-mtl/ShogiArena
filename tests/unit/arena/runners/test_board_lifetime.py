"""Check real Board owners when retained tracebacks are collected on another thread."""

from __future__ import annotations

import subprocess
import sys
import textwrap

import pytest

_PROBE = textwrap.dedent(
    r"""
    import asyncio
    import gc
    import logging
    import sys
    import threading
    from types import SimpleNamespace

    from rsshogi.core import Board, Move

    from shogiarena._core.contexts.match.application.runner_finalize_mixin import GameRunnerFinalizeMixin
    from shogiarena._core.contexts.match.application.runner_move_mixin import GameRunnerMoveMixin
    from shogiarena._core.contexts.match.application.runner_types import (
        MoveApplicationDependencies, MoveApplicationRequest, MoveApplicationStateRefs,
        RecoveredBestmoveRequest, RecoveredBestmoveStateRefs,
    )
    from shogiarena._core.platform.db.store.record_store import DBRecordStore
    from shogiarena._core.shared.kernel.time_control import GameClock, TimeControlLimits
    from shogiarena._core.shared.kernel.out_of_book import compute_out_of_book
    from shogiarena._core.contexts.dashboard.application.game.detail_builder import build_game_detail_payload
    from shogiarena._core.contexts.dashboard.application.book.pairs import _lookup_hits

    mode = sys.argv[1]
    unraisable = []
    sys.unraisablehook = lambda event: unraisable.append(str(event.exc_value))
    gc.disable()
    retained = []

    class BadThink:
        @property
        def bestmove(self):
            raise ValueError("injected bestmove failure")

    class Harness(GameRunnerMoveMixin, GameRunnerFinalizeMixin):
        _clock_notify_log_threshold_ms = float("inf")

        async def _notify_clock_increment(self, **payload):
            if mode.endswith("cancel"):
                asyncio.current_task().cancel()
                await asyncio.sleep(0)
            raise ValueError("injected notification failure")

    def make_state(board):
        clock = GameClock(TimeControlLimits(time_ms=100000))
        clock.initialize_for_game()
        clock.start_timer()
        return MoveApplicationStateRefs(
            board=board, moves=[], eval_values=[], nodes_values=[], depth_values=[],
            seldepth_values=[], move_times_ms=[], wall_times_ms=[], engine_wall_times_ms=[],
            move_sources=[], latency_deltas_ms=[], current_time_control=clock,
            black_time_control=clock, white_time_control=clock,
        )

    async def run_move():
        board = Board()
        state = make_state(board)
        recovered = RecoveredBestmoveStateRefs(move_state=state)
        try:
            harness = Harness()
            if mode == "process_error":
                await harness._process_move_result(board, BadThink(), "test")
            elif mode.startswith("common"):
                await harness._apply_move_common(
                    request=MoveApplicationRequest(
                        move=Move.from_usi("7g7f"),
                        think_result=SimpleNamespace(pvs=[], get_last_pv=lambda: None),
                        elapsed_ms=1, engine_wall_time_ms=1, move_source="search", game_id="test",
                        ply_count=0, is_side_that_moved_black=True, repetition_occurrences_to_draw=4,
                    ),
                    state=state, dependencies=MoveApplicationDependencies(adjudicator=None),
                )
            else:
                think = BadThink() if mode == "recovered_process_error" else SimpleNamespace(
                    bestmove=Move.from_usi("7g7f"), pvs=[], get_last_pv=lambda: None,
                )
                await harness._handle_recovered_bestmove(
                    request=RecoveredBestmoveRequest(
                        think_result=think, elapsed_ms=1, engine_wall_time_ms=1, move_source="search",
                        current_engine_name="test", game_id="test", ply_count=0, start_ply_number=1,
                        initial_sfen=board.to_sfen(), black_name="black", white_name="white",
                        player_name="Black", is_black_turn=True, repetition_occurrences_to_draw=4,
                    ),
                    state=recovered, dependencies=MoveApplicationDependencies(adjudicator=None),
                )
        finally:
            # The caller releases its own references; child frames must release theirs too.
            del board, state, recovered

    class BadBook:
        def lookup_sfen(self, sfen):
            raise RuntimeError("injected lookup failure")

    def bad_opener(path):
        raise RuntimeError("injected opener failure")

    def run_sync():
        if mode == "db_invalid_sfen":
            game = SimpleNamespace(id=1, initial_position_sfen="invalid", black_player_id=1, white_player_id=2)
            results = iter([
                SimpleNamespace(scalar_one_or_none=lambda: game),
                SimpleNamespace(fetchall=lambda: []),
                SimpleNamespace(scalar_one=lambda: "black"),
                SimpleNamespace(scalar_one=lambda: "white"),
            ])
            session = SimpleNamespace(execute=lambda statement: next(results))
            store = object.__new__(DBRecordStore)
            store._repository = SimpleNamespace(session=session)
            store._load(game_id=1)
        elif mode == "book_error":
            compute_out_of_book(["7g7f"], BadBook())
        elif mode == "pairs_error":
            _lookup_hits(
                moves_usi=["7g7f"], start_sfen=None, black_book={"resolved_path": "test"},
                white_book=None, opener=bad_opener, cache={}, book_moves_limit=16,
            )
        elif mode == "detail_invalid_sfen":
            metadata = SimpleNamespace(
                attributes={}, black_player="black", white_player="white", start_date=None, end_date=None,
            )
            record = SimpleNamespace(
                metadata=metadata, game_name="test", black_time_control=None, white_time_control=None,
                init_position_sfen="invalid",
            )
            build_game_detail_payload(record=record, game_id="test", logger=logging.getLogger("test"))

    try:
        if mode in {"db_invalid_sfen", "book_error", "pairs_error", "detail_invalid_sfen"}:
            run_sync()
        else:
            asyncio.run(run_move())
    except BaseException as error:
        expected = asyncio.CancelledError if mode.endswith("cancel") else (ValueError, RuntimeError)
        assert isinstance(error, expected), repr(error)
        retained.append(error)
    else:
        raise AssertionError("probe did not exercise the injected failure")

    def collect_on_foreign_thread():
        retained.clear()
        gc.collect()

    collector = threading.Thread(target=collect_on_foreign_thread)
    collector.start()
    collector.join()
    assert not unraisable, unraisable
    print("foreign-thread collection: no unraisable events")
    """
)


@pytest.mark.parametrize(
    "mode",
    [
        "process_error",
        "common_error",
        "common_cancel",
        "recovered_process_error",
        "recovered_apply_error",
        "recovered_apply_cancel",
        "db_invalid_sfen",
        "book_error",
        "pairs_error",
        "detail_invalid_sfen",
    ],
)
def test_board_is_released_before_traceback_crosses_threads(mode: str) -> None:
    result = subprocess.run(
        [sys.executable, "-c", _PROBE, mode],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "foreign-thread collection: no unraisable events" in result.stdout
