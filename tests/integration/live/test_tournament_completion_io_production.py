"""CLI/composition smoke: disk delays must not stop another game's USI clock."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
import time

import pytest
from test_tournament_composition_timeout import _write_config, _write_engine_assets

from shogiarena._core.contexts.game_session.application.completion.session_service import (
    TournamentSessionCompletionService,
)
from shogiarena._core.contexts.match.application.runner_finalize_mixin import GameRunnerFinalizeMixin
from shogiarena._core.contexts.tournament.application.session import state_store
from shogiarena._core.platform.engine_runtime.usi_engine_session import AsyncUsiEngine
from shogiarena.cli import main


@pytest.mark.parametrize("sink", ["record", "state"])
def test_cli_parallel_games_receive_bestmove_during_400ms_completion_write(tmp_path, monkeypatch, sink):
    entered = threading.Event()
    intervals, go_times, move_times = [], [], []
    go_count = 0
    main_thread = threading.get_ident()

    def delay(write):
        def slow(*args, **kwargs):
            if not intervals:
                assert threading.get_ident() != main_thread
                started = time.perf_counter()
                entered.set()
                time.sleep(0.4)
                try:
                    return write(*args, **kwargs)
                finally:
                    intervals.append((started, time.perf_counter()))
            return write(*args, **kwargs)

        return slow

    if sink == "record":
        monkeypatch.setattr(
            TournamentSessionCompletionService,
            "persist_record_outputs",
            delay(TournamentSessionCompletionService.persist_record_outputs),
        )
    else:
        original_write = state_store.write_json_atomic
        delayed_write = delay(original_write)

        def save(path, payload):
            # Setup has no active game clock and must not release the barrier.
            if payload["completed_games_count"] > 0:
                return delayed_write(path, payload)
            return original_write(path, payload)

        monkeypatch.setattr(state_store, "write_json_atomic", save)

    original_send = AsyncUsiEngine._send_command

    async def send(self, command, *, state=None):
        nonlocal go_count
        is_go = command.startswith("go ")
        if is_go:
            go_count += 1
            if go_count == 2:
                # Keep one game active until the other game's completion enters
                # its disk write; then send go and receive bestmove during it.
                assert await asyncio.to_thread(entered.wait, 10)
        result = await original_send(self, command, state=state)
        if is_go:
            go_times.append(time.perf_counter())
        return result

    original_move = GameRunnerFinalizeMixin._process_move_result

    async def move(self, board, think_result, engine_name):
        result = await original_move(self, board, think_result, engine_name)
        move_times.append(time.perf_counter())
        return result

    monkeypatch.setattr(AsyncUsiEngine, "_send_command", send)
    monkeypatch.setattr(GameRunnerFinalizeMixin, "_process_move_result", move)
    black, white = _write_engine_assets(tmp_path, delay_s=0.01)
    config_path = _write_config(
        tmp_path,
        black=black,
        white=white,
        time_ms=2_000,
        margin_ms=0,
        games_per_pair=2,
        num_parallel=2,
    )
    run_dir = tmp_path / "run"
    main(["run", "tournament", str(config_path), "--run-dir", str(run_dir), "--no-resume"])

    assert len(intervals) == 1
    start, end = intervals[0]
    assert end - start >= 0.4
    assert any(start < stamp < end for stamp in go_times)
    assert any(start < stamp < end for stamp in move_times)
    status = json.loads((run_dir / "completion_status.json").read_text(encoding="utf-8"))
    assert status["completed"] == 2
    assert status["status"] == "clean"
    assert status["timeouts_by_origin"] == {}
    assert json.loads((run_dir / "state.json").read_text(encoding="utf-8"))["is_finished"] is True
    with sqlite3.connect(run_dir / "game.db") as db:
        assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert db.execute("SELECT count(*) FROM game").fetchone() == (2,)
