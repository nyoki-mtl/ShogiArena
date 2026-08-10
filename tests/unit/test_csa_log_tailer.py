"""追記中の CSA イベントログを読む tailer と watcher（0067）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from shogiarena._core.contexts.csa_watch.adapters.jsonl_log_source import JsonlLogCursor, JsonlLogSource
from shogiarena._core.contexts.csa_watch.application.run_watcher import CsaRunWatcher, read_run_once
from shogiarena._core.contexts.csa_watch.ports.log_source_ports import DiscoveredRun

RUN_A = "1800000000"
RUN_B = "1800001000"


def _record(seq: int, **body: object) -> str:
    payload: dict[str, object] = {"seq": seq, "at": seq * 10, "ts": 1_800_000_000_000 + seq * 10, "type": "state"}
    payload.setdefault("phase", "playing")
    payload.update(body)
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _write(path: Path, text: str, *, append: bool = False) -> None:
    mode = "a" if append else "w"
    with path.open(mode, encoding="utf-8", newline="") as handle:
        handle.write(text)


class TestCursor:
    def test_only_newline_terminated_lines_are_returned(self, tmp_path: Path) -> None:
        path = tmp_path / f"{RUN_A}-events.jsonl"
        _write(path, _record(1) + "\n" + _record(2)[:20])
        cursor = JsonlLogSource(tmp_path).open_cursor(DiscoveredRun(run_id=RUN_A, path=path))

        chunk = cursor.read_new_lines()
        assert len(chunk.lines) == 1
        assert chunk.has_partial_line

    def test_the_held_back_line_is_delivered_once_it_completes(self, tmp_path: Path) -> None:
        path = tmp_path / f"{RUN_A}-events.jsonl"
        full = _record(2)
        _write(path, _record(1) + "\n" + full[:20])
        cursor = JsonlLogSource(tmp_path).open_cursor(DiscoveredRun(run_id=RUN_A, path=path))
        cursor.read_new_lines()

        _write(path, full[20:] + "\n", append=True)
        chunk = cursor.read_new_lines()
        assert chunk.lines == (full,)
        assert not chunk.has_partial_line

    def test_carriage_returns_are_stripped(self, tmp_path: Path) -> None:
        path = tmp_path / f"{RUN_A}-events.jsonl"
        _write(path, _record(1) + "\r\n")
        cursor = JsonlLogSource(tmp_path).open_cursor(DiscoveredRun(run_id=RUN_A, path=path))
        assert cursor.read_new_lines().lines == (_record(1),)

    def test_undecodable_bytes_do_not_end_the_read(self, tmp_path: Path) -> None:
        path = tmp_path / f"{RUN_A}-events.jsonl"
        path.write_bytes(b"\xff\xfe not json\n" + (_record(2) + "\n").encode("utf-8"))
        cursor = JsonlLogSource(tmp_path).open_cursor(DiscoveredRun(run_id=RUN_A, path=path))
        chunk = cursor.read_new_lines()
        assert len(chunk.lines) == 2
        assert chunk.lines[1] == _record(2)

    def test_a_character_split_across_two_polls_survives(self, tmp_path: Path) -> None:
        """A poll can land inside a multi-byte character; decoding halves corrupts it."""
        path = tmp_path / f"{RUN_A}-events.jsonl"
        line = _record(1, phase="playing", detail="日本語テスト")
        data = (line + "\n").encode("utf-8")
        split = data.index("日".encode()) + 1
        path.write_bytes(data[:split])
        cursor = JsonlLogSource(tmp_path).open_cursor(DiscoveredRun(run_id=RUN_A, path=path))
        cursor.read_new_lines()

        with path.open("ab") as handle:
            handle.write(data[split:])
        chunk = cursor.read_new_lines()
        assert chunk.lines == (line,)
        assert "�" not in chunk.lines[0]

    def test_a_truncated_file_restarts_from_the_beginning(self, tmp_path: Path) -> None:
        path = tmp_path / f"{RUN_A}-events.jsonl"
        _write(path, _record(1) + "\n" + _record(2) + "\n")
        cursor = JsonlLogSource(tmp_path).open_cursor(DiscoveredRun(run_id=RUN_A, path=path))
        assert len(cursor.read_new_lines().lines) == 2

        _write(path, _record(1) + "\n")
        assert cursor.read_new_lines().lines == (_record(1),)

    def test_an_atomic_same_size_replacement_restarts_from_the_beginning(self, tmp_path: Path) -> None:
        path = tmp_path / f"{RUN_A}-events.jsonl"
        original = _record(1, phase="playing") + "\n"
        replacement = _record(1, phase="warming") + "\n"
        assert len(original.encode("utf-8")) == len(replacement.encode("utf-8"))
        _write(path, original)
        cursor = JsonlLogSource(tmp_path).open_cursor(DiscoveredRun(run_id=RUN_A, path=path))
        cursor.read_new_lines()

        replacement_path = tmp_path / "replacement.jsonl"
        _write(replacement_path, replacement)
        replacement_path.replace(path)

        chunk = cursor.read_new_lines()
        assert chunk.restarted is True
        assert chunk.lines == (replacement.rstrip("\n"),)

    def test_an_atomic_larger_replacement_restarts_from_the_beginning(self, tmp_path: Path) -> None:
        path = tmp_path / f"{RUN_A}-events.jsonl"
        _write(path, _record(1, phase="playing") + "\n")
        cursor = JsonlLogSource(tmp_path).open_cursor(DiscoveredRun(run_id=RUN_A, path=path))
        cursor.read_new_lines()

        replacement_lines = (_record(1, phase="warming"), _record(2, phase="playing"))
        replacement_path = tmp_path / "larger-replacement.jsonl"
        _write(replacement_path, "\n".join(replacement_lines) + "\n")
        replacement_path.replace(path)

        chunk = cursor.read_new_lines()
        assert chunk.restarted is True
        assert chunk.lines == replacement_lines

    def test_an_in_place_same_size_rewrite_restarts_from_the_beginning(self, tmp_path: Path) -> None:
        path = tmp_path / f"{RUN_A}-events.jsonl"
        original = _record(1, phase="playing") + "\n"
        replacement = _record(1, phase="warming") + "\n"
        _write(path, original)
        cursor = JsonlLogSource(tmp_path).open_cursor(DiscoveredRun(run_id=RUN_A, path=path))
        cursor.read_new_lines()

        _write(path, replacement)

        chunk = cursor.read_new_lines()
        assert chunk.restarted is True
        assert chunk.lines == (replacement.rstrip("\n"),)

    def test_a_missing_file_is_reported_as_no_new_lines(self, tmp_path: Path) -> None:
        path = tmp_path / f"{RUN_A}-events.jsonl"
        cursor = JsonlLogSource(tmp_path).open_cursor(DiscoveredRun(run_id=RUN_A, path=path))
        assert cursor.read_new_lines().lines == ()


class TestDiscovery:
    def test_runs_are_reported_newest_first(self, tmp_path: Path) -> None:
        for run_id in (RUN_A, RUN_B):
            _write(tmp_path / f"{run_id}-events.jsonl", _record(1) + "\n")
        (tmp_path / f"{RUN_A}-wire.log").write_text("ignored", encoding="utf-8")

        runs = JsonlLogSource(tmp_path).discover_runs()
        assert [run.run_id for run in runs] == [RUN_B, RUN_A]

    def test_an_empty_directory_yields_no_runs(self, tmp_path: Path) -> None:
        assert JsonlLogSource(tmp_path).discover_runs() == ()


class TestWatcher:
    @pytest.mark.asyncio
    async def test_two_existing_runs_in_one_directory_get_distinct_stable_workers(self, tmp_path: Path) -> None:
        for run_id in (RUN_A, RUN_B):
            _write(tmp_path / f"{run_id}-events.jsonl", _record(1) + "\n")
        watcher = CsaRunWatcher(JsonlLogSource(tmp_path))
        try:
            await watcher.refresh()
            first_indices = {view.state.run_id: view.worker_idx for view in watcher.views()}
            assert first_indices == {RUN_B: 0, RUN_A: 1}

            await watcher.refresh()
            assert {view.state.run_id: view.worker_idx for view in watcher.views()} == first_indices
        finally:
            watcher.close()

    @pytest.mark.asyncio
    async def test_worker_indices_survive_a_new_run_appearing(self, tmp_path: Path) -> None:
        _write(tmp_path / f"{RUN_A}-events.jsonl", _record(1) + "\n")
        watcher = CsaRunWatcher(JsonlLogSource(tmp_path))
        try:
            await watcher.refresh()
            first = watcher.view_for(RUN_A)
            assert first is not None
            assert first.worker_idx == 0

            _write(tmp_path / f"{RUN_B}-events.jsonl", _record(1) + "\n")
            await watcher.refresh()

            unchanged = watcher.view_for(RUN_A)
            newcomer = watcher.view_for(RUN_B)
            assert unchanged is not None and unchanged.worker_idx == 0
            assert newcomer is not None and newcomer.worker_idx == 1
        finally:
            watcher.close()

    @pytest.mark.asyncio
    async def test_refresh_reports_only_runs_that_moved(self, tmp_path: Path) -> None:
        path = tmp_path / f"{RUN_A}-events.jsonl"
        _write(path, _record(1) + "\n")
        watcher = CsaRunWatcher(JsonlLogSource(tmp_path))
        try:
            assert len(await watcher.refresh()) == 1
            assert await watcher.refresh() == ()

            _write(path, _record(2, phase="closing") + "\n", append=True)
            changed = await watcher.refresh()
            assert len(changed) == 1
            assert changed[0].state.phase == "closing"
            assert changed[0].applied_records == 1
        finally:
            watcher.close()

    @pytest.mark.asyncio
    async def test_log_restart_increments_generation_and_is_reported_even_without_old_state(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / f"{RUN_A}-events.jsonl"
        _write(path, _record(1) + "\n" + _record(2) + "\n")
        watcher = CsaRunWatcher(JsonlLogSource(tmp_path))
        try:
            first = await watcher.refresh()
            assert first[0].stream_generation == 0

            _write(path, _record(1, phase="warming") + "\n")
            restarted = await watcher.refresh()

            assert restarted[0].restarted is True
            assert restarted[0].stream_generation == 1
            assert restarted[0].state.phase == "warming"
            assert watcher.view_for(RUN_A).stream_generation == 1  # type: ignore[union-attr]
        finally:
            watcher.close()

    @pytest.mark.asyncio
    async def test_same_size_replacement_resets_the_folded_run_state(self, tmp_path: Path) -> None:
        path = tmp_path / f"{RUN_A}-events.jsonl"
        original = _record(1, phase="playing") + "\n"
        replacement = _record(1, phase="warming") + "\n"
        _write(path, original)
        watcher = CsaRunWatcher(JsonlLogSource(tmp_path))
        try:
            first = await watcher.refresh()
            assert first[0].state.phase == "playing"

            replacement_path = tmp_path / "replacement.jsonl"
            _write(replacement_path, replacement)
            replacement_path.replace(path)
            restarted = await watcher.refresh()

            assert restarted[0].restarted is True
            assert restarted[0].stream_generation == 1
            assert restarted[0].state.phase == "warming"
        finally:
            watcher.close()

    @pytest.mark.asyncio
    async def test_read_run_once_folds_to_the_end_of_the_file(self, tmp_path: Path) -> None:
        path = tmp_path / f"{RUN_A}-events.jsonl"
        _write(path, _record(1) + "\n" + _record(2, phase="closing") + "\n")
        view = await read_run_once(JsonlLogSource(tmp_path), DiscoveredRun(run_id=RUN_A, path=path))
        assert view.state.phase == "closing"
        assert not view.has_partial_line


def test_a_shrunk_log_tells_the_caller_to_start_over(tmp_path: Path) -> None:
    """A shrink means "this is not the file I was reading".

    Resetting the byte offset alone — which is what this replaced — leaves the
    folded `RunState` in place, so the two sessions merge into one run and their
    games, alerts, ponder tallies and liveness are silently mixed.
    """
    path = tmp_path / "1-events.jsonl"
    path.write_text('{"a": 1}\n{"a": 2}\n', encoding="utf-8")
    cursor = JsonlLogCursor(DiscoveredRun(run_id="1", path=path))

    first = cursor.read_new_lines()
    assert len(first.lines) == 2
    assert first.restarted is False

    # The bridge restarted and took the same name: a shorter, unrelated stream.
    path.write_text('{"b": 1}\n', encoding="utf-8")
    second = cursor.read_new_lines()

    assert second.restarted is True
    assert len(second.lines) == 1
