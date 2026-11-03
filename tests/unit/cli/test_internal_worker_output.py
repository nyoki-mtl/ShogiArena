"""Output backpressure must preserve worker clocks, ownership and JSONL order."""

from __future__ import annotations

import asyncio
import io
import json
import threading
from types import SimpleNamespace

import pytest
from tests.unit.cli.test_internal_remote_game_execution_spec import _sealed_spec

from shogiarena._core.contexts.game_session.ports.game_execution_spec import GameExecutionResult
from shogiarena._core.interfaces.cli.internal import output, remote


class BlockingSink(io.StringIO):
    def __init__(self, phase):
        super().__init__()
        self.phase = phase
        self.entered = threading.Event()
        self.release = threading.Event()
        self.finished = threading.Event()
        self.writer_threads = []
        self.in_line = False

    def _block(self, phase):
        self.writer_threads.append(threading.get_ident())
        if self.phase == phase and not self.entered.is_set():
            self.entered.set()
            assert self.release.wait(8)
            self.finished.set()

    def write(self, value):
        assert not self.in_line, "overlapping line writers"
        self.in_line = True
        self._block("write")
        return super().write(value)

    def flush(self):
        self._block("flush")
        self.in_line = False


async def _entered(sink):
    async with asyncio.timeout(5):
        while not sink.entered.is_set():
            await asyncio.sleep(0.001)


def _outcome(spec):
    return SimpleNamespace(
        result=GameExecutionResult(
            execution_digest=spec.execution_digest,
            game_id=spec.identity.game_id,
            classification="DRAW_BY_REPETITION",
        )
    )


def _install(monkeypatch, execute, sink):
    worker = SimpleNamespace(execute=execute, request_shutdown=lambda: None)
    monkeypatch.setattr(remote, "build_default_root", lambda: SimpleNamespace(game_execution_worker=worker))
    monkeypatch.setattr(output.sys, "stdout", sink)
    return worker


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["write", "flush"])
@pytest.mark.parametrize("mode", ["success", "error", "cancel"])
async def test_slow_output_keeps_loop_running_and_drains_before_final_envelope(tmp_path, monkeypatch, phase, mode):
    sink = BlockingSink(phase)
    advanced, cleaned = asyncio.Event(), asyncio.Event()

    async def execute(spec, *, progress_queue, **kwargs):
        progress_queue.put_nowait((0, 0, json.dumps({"type": "engine_io", "seq": 0})))
        try:
            await _entered(sink)
            await asyncio.sleep(0.02)
            assert not sink.finished.is_set()
            advanced.set()
            if mode == "cancel":
                await asyncio.Event().wait()
            if mode == "error":
                raise OSError("injected worker failure")
            progress_queue.put_nowait((0, 1, json.dumps({"type": "move_progress", "game_result": "draw"})))
            return _outcome(spec)
        finally:
            progress_queue.put_nowait((0, 0, json.dumps({"type": "engine_state", "state": "closed"})))
            cleaned.set()

    _install(monkeypatch, execute, sink)
    owner = asyncio.create_task(remote.run_game_execution_spec(_sealed_spec(), execution_root=tmp_path))
    try:
        await asyncio.wait_for(advanced.wait(), 5)
        if mode == "cancel":
            for _ in range(2):
                owner.cancel("caller stopped")
                await asyncio.sleep(0)
        assert not owner.done()
    finally:
        sink.release.set()
        rc = await owner
    assert cleaned.is_set()
    assert rc == (0 if mode == "success" else 1)
    rows = [json.loads(line) for line in sink.getvalue().splitlines()]
    assert rows[0] == {"type": "engine_io", "seq": 0}
    assert rows[-2] == {"type": "engine_state", "state": "closed"}
    assert rows[-1].get("type", "result") == ("result" if mode == "success" else "error")
    assert all(writer != threading.get_ident() for writer in sink.writer_threads)
    assert not sink.in_line


@pytest.mark.asyncio
async def test_output_blocked_longer_than_old_drain_timeout_is_not_discarded(tmp_path, monkeypatch):
    sink = BlockingSink("flush")

    async def execute(spec, *, progress_queue, **kwargs):
        progress_queue.put_nowait((0, 1, '{"type":"move_progress","game_result":"draw"}'))
        progress_queue.put_nowait((0, 0, '{"type":"engine_state","state":"closed"}'))
        return _outcome(spec)

    _install(monkeypatch, execute, sink)
    owner = asyncio.create_task(remote.run_game_execution_spec(_sealed_spec(), execution_root=tmp_path))
    try:
        await _entered(sink)
        await asyncio.sleep(2.1)
        assert not owner.done()
    finally:
        sink.release.set()
        assert await owner == 0
    assert [json.loads(line).get("type", "result") for line in sink.getvalue().splitlines()] == [
        "move_progress",
        "engine_state",
        "result",
    ]


@pytest.mark.asyncio
async def test_missing_terminal_progress_does_not_hang_or_fabricate_progress(tmp_path, monkeypatch):
    sink = io.StringIO()

    async def execute(spec, *, progress_queue, **kwargs):
        # None remains a valid ignored event, not the private end sentinel.
        progress_queue.put_nowait((0, 0, None))
        progress_queue.put_nowait((0, 0, '{"type":"engine_state"}'))
        return _outcome(spec)

    _install(monkeypatch, execute, sink)
    assert await asyncio.wait_for(remote.run_game_execution_spec(_sealed_spec(), execution_root=tmp_path), 2) == 0
    assert [json.loads(line).get("type", "result") for line in sink.getvalue().splitlines()] == [
        "engine_state",
        "result",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["write", "flush"])
@pytest.mark.parametrize("error", [BrokenPipeError, OSError])
async def test_failed_output_stops_and_awaits_worker_without_retry(tmp_path, monkeypatch, phase, error):
    closed = asyncio.Event()
    calls = []

    class FailedSink(io.StringIO):
        def write(self, value):
            calls.append("write")
            if phase == "write":
                raise error("injected output failure")
            return super().write(value)

        def flush(self):
            calls.append("flush")
            raise error("injected output failure")

    async def execute(spec, *, progress_queue, **kwargs):
        progress_queue.put_nowait((0, 0, '{"type":"engine_state"}'))
        try:
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0)
            closed.set()

    _install(monkeypatch, execute, FailedSink())
    with pytest.raises(error, match="injected output failure"):
        await asyncio.wait_for(remote.run_game_execution_spec(_sealed_spec(), execution_root=tmp_path), 5)
    assert closed.is_set()
    assert calls == (["write"] if phase == "write" else ["write", "flush"])


@pytest.mark.asyncio
async def test_cancel_after_final_result_selected_drains_it_once_and_returns_failure(tmp_path, monkeypatch):
    sink = BlockingSink("write")

    async def execute(spec, **kwargs):
        return _outcome(spec)

    _install(monkeypatch, execute, sink)
    owner = asyncio.create_task(remote.run_game_execution_spec(_sealed_spec(), execution_root=tmp_path))
    try:
        await _entered(sink)
        for _ in range(2):
            owner.cancel()
            await asyncio.sleep(0)
        assert not owner.done()
    finally:
        sink.release.set()
        assert await owner == 1
    assert [json.loads(line).get("type", "result") for line in sink.getvalue().splitlines()] == ["result"]


@pytest.mark.asyncio
async def test_output_failure_and_caller_cancellation_do_not_cancel_worker_cleanup_twice(tmp_path, monkeypatch):
    cleaning, release, cleaned = asyncio.Event(), asyncio.Event(), asyncio.Event()

    class FailedSink(io.StringIO):
        def write(self, value):
            raise BrokenPipeError("output closed")

    async def execute(spec, *, progress_queue, **kwargs):
        progress_queue.put_nowait((0, 0, '{"type":"engine_state"}'))
        try:
            await asyncio.Event().wait()
        finally:
            cleaning.set()
            await release.wait()
            cleaned.set()

    _install(monkeypatch, execute, FailedSink())
    owner = asyncio.create_task(remote.run_game_execution_spec(_sealed_spec(), execution_root=tmp_path))
    try:
        await asyncio.wait_for(cleaning.wait(), 5)
        for _ in range(2):
            owner.cancel()
            await asyncio.sleep(0)
        assert not owner.done() and not cleaned.is_set()
    finally:
        release.set()
        with pytest.raises(BrokenPipeError, match="output closed"):
            await owner
    assert cleaned.is_set()


@pytest.mark.asyncio
async def test_validation_error_output_is_also_off_loop(tmp_path, monkeypatch):
    sink = BlockingSink("write")
    monkeypatch.setattr(output.sys, "stdout", sink)

    async def command():
        try:
            await remote._remote_run_pair_command(SimpleNamespace(spec_file=str(tmp_path / "missing")))
        except SystemExit as exc:
            return exc.code

    owner = asyncio.create_task(command())
    try:
        await _entered(sink)
        await asyncio.sleep(0.02)
        assert not owner.done()
    finally:
        sink.release.set()
        assert await owner == 2
