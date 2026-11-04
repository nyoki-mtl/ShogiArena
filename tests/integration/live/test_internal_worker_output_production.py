"""Internal CLI + real USI subprocesses must run while stdout is blocked."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import sys
import threading
import time

import pytest
from test_tournament_composition_timeout import _write_engine_script
from tests.unit.cli.test_internal_remote_game_execution_spec import _sealed_spec

from shogiarena._core.contexts.game_session.ports.game_execution_spec import (
    GameExecutionSpecPayload,
    seal_game_execution_spec,
)
from shogiarena._core.contexts.match.application.runner_finalize_mixin import GameRunnerFinalizeMixin
from shogiarena._core.interfaces.cli.internal import output
from shogiarena._core.platform.engine_runtime.usi_engine_session import AsyncUsiEngine
from shogiarena.cli import main


def _spec_file(root):
    payload = _sealed_spec().model_dump(mode="json", exclude={"execution_digest"})
    for role in ("black", "white"):
        script = _write_engine_script(root, role, delay_s=0.01, slow_only_as_white=False)
        process = payload[f"{role}_engine"]["process"]
        process["working_directory"] = "."
        process["lifecycle"] = "per_game"
        process["artifact"]["entrypoint"] = script.name
        process["artifact"]["sha256"] = hashlib.sha256(script.read_bytes()).hexdigest()
        process["artifact"]["target_platform"]["operating_system"] = "windows" if os.name == "nt" else "linux"
        payload["time"][role] = {"time_ms": 2000, "expiry_margin_ms": 0}
    payload["rules"]["adjudication"].update(max_plies=4, sync_max_plies_with_engine=False)
    spec = seal_game_execution_spec(GameExecutionSpecPayload.model_validate(payload))
    path = root / "spec.json"
    path.write_bytes(spec.canonical_json_bytes())
    return path, spec


@pytest.mark.parametrize("sink_kind", ["write", "flush", "pipe"])
def test_internal_cli_receives_bestmove_while_stdout_is_blocked(tmp_path, monkeypatch, sink_kind):
    entered, finished, read_release = threading.Event(), threading.Event(), threading.Event()
    intervals, go_times, move_times = [], [], []
    reader_timed_out = threading.Event()
    reader_paused = threading.Event()
    main_thread = threading.get_ident()
    original_write = output._write_and_flush
    collected = []
    pipe_writer = None
    reader = None

    if sink_kind == "pipe":
        read_fd, write_fd = os.pipe()
        pipe_reader = os.fdopen(read_fd, "r", encoding="utf-8")
        pipe_writer = os.fdopen(write_fd, "w", encoding="utf-8")

        def read_pipe():
            with pipe_reader:
                for line in pipe_reader:
                    collected.append(line)
                    parsed = json.loads(line)
                    if parsed.get("type") == "move_progress" and parsed.get("ply", 0) == 1:
                        # Drain startup events before blocking the intended write.
                        # Keep a timeout so an inline writer cannot deadlock the test.
                        reader_paused.set()
                        if not read_release.wait(10):
                            reader_timed_out.set()
                        collected.append(pipe_reader.read())
                        break

        reader = threading.Thread(target=read_pipe)
        reader.start()
        target = pipe_writer
    else:
        target = io.StringIO()
    monkeypatch.setattr(sys, "stdout", target)

    def write(stream, payload):
        parsed = json.loads(payload)
        if not entered.is_set() and parsed.get("type") == "move_progress" and parsed.get("ply", 0) >= 1:
            assert threading.get_ident() != main_thread
            started = time.perf_counter()
            entered.set()
            if sink_kind == "pipe":
                original_write(stream, payload)
                # The reader pauses after this event. Whitespace fills the pipe
                # before the next JSON line without changing any event fields.
                stream.write(" " * (2 * 1024 * 1024))
                stream.flush()
            elif sink_kind == "write":
                time.sleep(0.4)
                original_write(stream, payload)
            else:
                stream.write(payload + "\n")
                time.sleep(0.4)
                stream.flush()
            intervals.append((started, time.perf_counter()))
            finished.set()
        else:
            original_write(stream, payload)

    monkeypatch.setattr(output, "_write_and_flush", write)
    original_send = AsyncUsiEngine._send_command
    go_count = 0

    async def send(self, command, *, state=None):
        nonlocal go_count
        is_go = command.strip().startswith("go ")
        if is_go:
            go_count += 1
            if go_count == 2:
                assert await asyncio.to_thread(entered.wait, 5)
                if sink_kind == "pipe":
                    assert await asyncio.to_thread(reader_paused.wait, 5)
            elif go_count == 1 and sink_kind == "pipe":
                # Let startup I/O batches arrive before the first move too.
                await asyncio.sleep(0.2)
        result = await original_send(self, command, state=state)
        if is_go:
            go_times.append(time.perf_counter())
        return result

    original_move = GameRunnerFinalizeMixin._process_move_result

    async def move(self, board, think_result, engine_name):
        result = await original_move(self, board, think_result, engine_name)
        move_times.append(time.perf_counter())
        if sink_kind == "pipe" and len(move_times) == 4:
            # The reader stays paused after the first move until game completion.
            assert entered.is_set() and not finished.is_set()
            read_release.set()
        return result

    monkeypatch.setattr(AsyncUsiEngine, "_send_command", send)
    monkeypatch.setattr(GameRunnerFinalizeMixin, "_process_move_result", move)
    spec_path, spec = _spec_file(tmp_path)
    try:
        main(["_internal", "remote-run-pair", "--spec-file", str(spec_path)])
    finally:
        read_release.set()
        if pipe_writer is not None:
            pipe_writer.close()
        if reader is not None:
            reader.join(5)
            assert not reader.is_alive()

    text = "".join(collected) if sink_kind == "pipe" else target.getvalue()
    rows = [json.loads(line) for line in text.splitlines()]
    assert len(intervals) == 1
    assert not reader_timed_out.is_set()
    start, end = intervals[0]
    if sink_kind != "pipe":
        assert end - start >= 0.4
    assert any(start < stamp < end for stamp in go_times)
    assert any(start < stamp < end for stamp in move_times)
    assert len(move_times) == 4
    assert rows[-1]["execution_digest"] == spec.execution_digest
    assert rows[-1]["game_id"] == spec.identity.game_id
    assert "TIMEOUT" not in rows[-1]["classification"] and "ERROR" not in rows[-1]["classification"]
    assert any(row.get("type") == "move_progress" and row.get("game_result") is not None for row in rows[:-1])
    assert sum("execution_digest" in row and "classification" in row for row in rows) == 1
