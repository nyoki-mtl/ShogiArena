"""CLI/composition: a starting game's manifest cannot stop another game's clock."""

from __future__ import annotations

import json
import sqlite3
import threading
import time

from test_tournament_composition_timeout import _write_config, _write_engine_assets

from shogiarena._core.contexts.game_session.adapters.orchestration import game_execution
from shogiarena._core.contexts.match.application.runner_finalize_mixin import GameRunnerFinalizeMixin
from shogiarena._core.platform.engine_runtime.usi_engine_session import AsyncUsiEngine
from shogiarena.cli import main


def test_cli_clock_and_bestmove_continue_during_start_manifest_save(tmp_path, monkeypatch):
    entered, moved = threading.Event(), threading.Event()
    saved, started, intervals, move_times = {}, {}, [], []
    main_thread = threading.get_ident()
    original_save = game_execution.persist_game_execution_manifest
    write_count = 0

    def save(**kwargs):
        nonlocal write_count
        assert threading.get_ident() != main_thread
        write_count += 1
        if write_count == 2:
            start = time.perf_counter()
            entered.set()
            # The other game's real USI response must be processed before this
            # manifest can finish. A blocked loop cannot satisfy this barrier.
            assert moved.wait(10)
            time.sleep(0.3)
            result = original_save(**kwargs)
            end = time.perf_counter()
            intervals.append((kwargs["game_id"], start, end))
        else:
            result = original_save(**kwargs)
        saved[kwargs["game_id"]] = time.perf_counter()
        return result

    original_send = AsyncUsiEngine._send_command

    async def send(self, command, *, state=None):
        if command.startswith("go ") and not entered.is_set():
            # Keep the first game's clock active until the second write starts.
            import asyncio

            assert await asyncio.to_thread(entered.wait, 10)
        return await original_send(self, command, state=state)

    original_move = GameRunnerFinalizeMixin._process_move_result

    async def move(self, board, think_result, engine_name):
        result = await original_move(self, board, think_result, engine_name)
        move_times.append(time.perf_counter())
        moved.set()
        return result

    original_run = game_execution._run_game_with_engines

    async def run(owner, spec, **kwargs):
        assert spec.game_id in saved
        started[spec.game_id] = time.perf_counter()
        return await original_run(owner, spec, **kwargs)

    monkeypatch.setattr(game_execution, "persist_game_execution_manifest", save)
    monkeypatch.setattr(game_execution, "_run_game_with_engines", run)
    monkeypatch.setattr(AsyncUsiEngine, "_send_command", send)
    monkeypatch.setattr(GameRunnerFinalizeMixin, "_process_move_result", move)
    black, white = _write_engine_assets(tmp_path, delay_s=0.01)
    config = _write_config(
        tmp_path, black=black, white=white, time_ms=2_000, margin_ms=0, games_per_pair=2, num_parallel=2
    )
    run_dir = tmp_path / "run"
    main(["run", "tournament", str(config), "--run-dir", str(run_dir), "--no-resume"])

    assert len(intervals) == 1
    game_id, start, end = intervals[0]
    assert end - start >= 0.3
    assert any(start < stamp < end for stamp in move_times)
    assert started[game_id] >= saved[game_id] >= end
    assert len(started) == len(saved) == 2
    status = json.loads((run_dir / "completion_status.json").read_text(encoding="utf-8"))
    assert status["completed"] == 2
    assert status["status"] == "clean"
    assert status["timeouts_by_origin"] == {}
    with sqlite3.connect(run_dir / "game.db") as db:
        assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert db.execute("SELECT count(*) FROM game").fetchone() == (2,)
