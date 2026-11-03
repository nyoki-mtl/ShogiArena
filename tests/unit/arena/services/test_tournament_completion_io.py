"""Completion I/O must yield, remain ordered, and drain before shutdown."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
import rsshogi.record

from shogiarena._core.contexts.game_session.application.completion.session_service import (
    TournamentSessionCompletionService,
)
from shogiarena._core.contexts.game_session.application.orchestration.runtime_orchestration_service import (
    PendingRuntimeOrchestrationService,
)
from shogiarena._core.contexts.tournament.adapters.runner import TournamentRunner
from shogiarena._core.contexts.tournament.application.runner_state import TournamentRunnerState
from shogiarena._core.contexts.tournament.application.session import state_store
from shogiarena._core.contexts.tournament.application.session.schedule_context import TournamentScheduleContext
from shogiarena._core.contexts.tournament.application.session.schedule_mutation_service import ScheduleMutationService
from shogiarena._core.contexts.tournament.application.session.state_store import TournamentSessionStateStore
from shogiarena._core.platform.db.store import record_store
from shogiarena._core.platform.db.store.arena_db_adapter import ArenaDBAdapter
from shogiarena._core.platform.db.store.repository_factory import SQLiteShogiDBFactory
from shogiarena._core.platform.records.binary_writer import RecordBinaryWriter, RecordBinaryWriterConfig
from shogiarena._core.shared.kernel.game_results import GameResult
from shogiarena._core.shared.kernel.session_hooks import SessionStopController
from tests.unit.arena.services.test_completion_persistence_invariants import _context
from tests.unit.arena.services.test_tournament_state_store import (
    _build_save_context,
    _make_config,
    _make_game,
    _OpenBenchStub,
    _SchedulerStub,
)


def _record(game_id):
    record = rsshogi.record.Record.from_usi_main_line(
        "startpos",
        ["7g7f", "3c3d"],
        result=GameResult.BLACK_WIN,
        metadata=rsshogi.record.RecordMetadata(
            game_name=game_id,
            black_player="EngineA",
            white_player="EngineB",
            updated_date=datetime.now(UTC).isoformat(),
        ),
    )
    payload = record.to_dict()
    for move in payload["moves"]:
        move["engine_info"] = {"eval": 42}
    return rsshogi.record.Record.from_dict(payload)


@pytest.fixture
def runtime(tmp_path):
    db = ArenaDBAdapter(SQLiteShogiDBFactory(tmp_path / "game.db"))
    db.ensure_schema()
    state = TournamentRunnerState(game_schedule=[_make_game("first"), _make_game("second")])
    config = _make_config()
    for engine in config.engines:
        engine.instance_id = None
    store = TournamentSessionStateStore()
    save_context = _build_save_context(run_dir=tmp_path, config=config, state=state, openbench=_OpenBenchStub())
    context = _context(db=db)
    context.state.completed_game_ids = state.completed_game_ids
    context.state.completed_game_summaries = state.completed_game_summaries
    context.state.save_run_state = lambda: store.save_run_state(save_context)
    progress = []
    runner = SimpleNamespace(
        _state=state,
        _completion_service=TournamentSessionCompletionService(),
        _build_completion_runtime_context=lambda: context,
        progress=SimpleNamespace(on_game_complete=progress.append),
    )
    yield SimpleNamespace(
        db=db,
        state=state,
        config=config,
        store=store,
        save_context=save_context,
        context=context,
        runner=runner,
        progress=progress,
    )
    db.close()


async def _complete(runtime, game_id):
    await TournamentRunner._handle_game_completion(
        runtime.runner,
        _make_game(game_id),
        _record(game_id),
        is_stop_requested=False,
    )


async def _wait_for(event):
    async with asyncio.timeout(5):
        while not event.is_set():
            await asyncio.sleep(0.001)


def _schedule_context(runtime, tmp_path):
    return TournamentScheduleContext(
        config=runtime.config,
        scheduler=_SchedulerStub([_make_game("new")]),
        instance_pool=None,
        run_dir=tmp_path,
        stop_controller=SessionStopController(),
        is_dashboard_enabled=False,
        engine_instance_defaults={},
        save_run_state=lambda: runtime.store.save_run_state(runtime.save_context),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("sink", ["db", "state", "psv", "sbinpack"])
async def test_400ms_completion_write_keeps_loop_alive(runtime, tmp_path, monkeypatch, sink):
    entered, finished = threading.Event(), threading.Event()
    main_thread = threading.get_ident()

    def delay(write):
        def slow(*args, **kwargs):
            assert threading.get_ident() != main_thread
            entered.set()
            time.sleep(0.4)
            try:
                return write(*args, **kwargs)
            finally:
                finished.set()

        return slow

    writer = None
    if sink == "db":
        monkeypatch.setattr(runtime.db, "append_record_list", delay(runtime.db.append_record_list))
    elif sink == "state":
        monkeypatch.setattr(state_store, "write_json_atomic", delay(state_store.write_json_atomic))
    else:
        writer = RecordBinaryWriter(
            RecordBinaryWriterConfig(
                format_id=sink,
                output_dir=tmp_path / "records",
                max_positions_per_file=100,
                max_games_per_file=1,
                file_prefix="records",
            )
        )
        runtime.context.persistence.record_writer = writer
        monkeypatch.setattr(writer, "_flush_and_sync", delay(writer._flush_and_sync))

    task = asyncio.create_task(_complete(runtime, "first"))
    try:
        await _wait_for(entered)
        # A timer must run while the production persistence operation is blocked.
        # Inline execution reaches this line only after finished has been set.
        assert not finished.is_set()
        await asyncio.sleep(0.02)
        assert not finished.is_set() and not task.done()
        await task
        assert runtime.state.completed_game_ids == {"first"}
        assert runtime.db.load_record(game_name="first") is not None
        assert json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))["completed_games_count"] == 1
        if writer is not None:
            assert writer.written_game_ids() == {"first"}
            assert writer.get_records_summary()["total_games"] == 1
    finally:
        await task
        if writer is not None:
            writer.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("duplicate", [False, True])
async def test_repeated_cancel_drains_writer_and_waiting_completion(runtime, monkeypatch, duplicate):
    entered, release = threading.Event(), threading.Event()
    original = runtime.db.append_record_list
    writes = []

    def slow(records):
        entered.set()
        assert release.wait(5)
        original(records)
        writes.append(records[0].game_name)

    monkeypatch.setattr(runtime.db, "append_record_list", slow)
    first = asyncio.create_task(_complete(runtime, "first"))
    second = None
    try:
        await _wait_for(entered)
        second = asyncio.create_task(_complete(runtime, "first" if duplicate else "second"))
        # Allow the owned completion to reach the occupied lock before cancelling.
        await asyncio.sleep(0.01)
        for _ in range(2):
            first.cancel()
            second.cancel()
            await asyncio.sleep(0.01)
        assert runtime.state.completion_lock.locked()
        assert not first.done() and not second.done() and writes == []
    finally:
        release.set()
        results = await asyncio.gather(first, *([second] if second is not None else []), return_exceptions=True)
    assert all(isinstance(result, asyncio.CancelledError) for result in results)
    expected = {"first"} if duplicate else {"first", "second"}
    assert set(writes) == expected and len(writes) == len(expected)
    assert runtime.state.completed_game_ids == expected
    assert len(runtime.db.get_games_with_players(game_type="arena")) == len(expected)
    assert not runtime.state.completion_lock.locked()
    assert runtime.state.pending_completion_counts == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_save", [False, True])
async def test_dashboard_cancel_and_completion_state_writes_are_ordered(
    runtime,
    tmp_path,
    monkeypatch,
    cancel_save,
):
    entered, release = threading.Event(), threading.Event()
    original = state_store.write_json_atomic
    snapshots = []

    def slow(path, payload):
        if not snapshots:
            entered.set()
            assert release.wait(5)
        original(path, payload)
        snapshots.append(payload)

    monkeypatch.setattr(state_store, "write_json_atomic", slow)
    first = asyncio.create_task(_complete(runtime, "first"))
    mutation = None
    try:
        await _wait_for(entered)
        schedule_context = SimpleNamespace(
            config=runtime.config,
            instance_pool=None,
            run_dir=tmp_path,
            is_dashboard_enabled=False,
            is_generate_run=lambda: False,
            save_run_state=lambda: runtime.store.save_run_state(runtime.save_context),
        )
        mutation = asyncio.create_task(ScheduleMutationService().cancel_game(runtime.state, schedule_context, "second"))
        await asyncio.sleep(0.02)
        assert runtime.state.cancelled_game_ids == {"second"}
        assert not mutation.done() and snapshots == []
        if cancel_save:
            mutation.cancel()
            await asyncio.sleep(0.01)
            mutation.cancel()
            await asyncio.sleep(0.01)
            assert not mutation.done()
    finally:
        release.set()
        await first
        if mutation is not None:
            if cancel_save:
                with pytest.raises(asyncio.CancelledError):
                    await mutation
            else:
                await mutation
    assert len(snapshots) == 2
    assert snapshots[0]["cancelled_game_ids"] == []
    assert snapshots[1]["cancelled_game_ids"] == ["second"]
    saved = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert saved["cancelled_game_ids"] == ["second"]
    assert saved["completed_games_count"] == 1
    assert not (tmp_path / ".state.json.tmp").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("sink", ["db", "state"])
async def test_persistence_failure_propagates_after_cancel_without_releasing_writer(
    runtime,
    monkeypatch,
    sink,
):
    entered, release = threading.Event(), threading.Event()

    def fail(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        raise OSError("injected disk failure")

    if sink == "db":
        monkeypatch.setattr(runtime.db, "append_record_list", fail)
    else:
        monkeypatch.setattr(state_store, "write_json_atomic", fail)
    task = asyncio.create_task(_complete(runtime, "first"))
    try:
        await _wait_for(entered)
        task.cancel()
        await asyncio.sleep(0.01)
        assert not task.done() and runtime.state.completion_lock.locked()
    finally:
        release.set()
        with pytest.raises(OSError, match="injected disk failure"):
            await task
    assert runtime.progress == []
    assert not runtime.state.completion_lock.locked()
    assert (runtime.db.load_record(game_name="first") is not None) == (sink == "state")
    assert ("first" in runtime.state.completed_game_ids) == (sink == "state")
    assert runtime.state.pending_completion_counts == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("bulk", ["cancel", "reschedule"])
async def test_saving_and_lock_waiting_terminal_games_cannot_be_cancelled_or_reassigned(
    runtime,
    tmp_path,
    monkeypatch,
    bulk,
):
    entered, release = threading.Event(), threading.Event()
    original = runtime.db.append_record_list

    def slow(records):
        entered.set()
        assert release.wait(5)
        original(records)

    monkeypatch.setattr(runtime.db, "append_record_list", slow)
    tasks = [asyncio.create_task(_complete(runtime, "first"))]
    try:
        await _wait_for(entered)
        tasks.append(asyncio.create_task(_complete(runtime, "second")))
        async with asyncio.timeout(5):
            while "second" not in runtime.state.pending_completion_counts:
                await asyncio.sleep(0.001)
        ctx = _schedule_context(runtime, tmp_path)
        service = ScheduleMutationService()
        for game_id in ("first", "second"):
            for method in (service.cancel_game, service.restore_game, service.set_game_instance):
                with pytest.raises(RuntimeError, match="is completing"):
                    await method(runtime.state, ctx, game_id)

        runtime.state.game_schedule.append(_make_game("third"))
        if bulk == "cancel":
            result = await service.cancel_pending_games(runtime.state, ctx)
            assert result["cancelled_games"] == ["third"]
            assert runtime.state.cancelled_game_ids == {"third"}
        else:
            ctx.orchestrator = SimpleNamespace(request_stop=lambda: None)
            await service.request_reschedule(runtime.state, ctx, seed="42")
            assert [spec.game_id for spec in runtime.state.pending_reschedule] == ["first", "second", "new"]
    finally:
        release.set()
        await asyncio.gather(*tasks)
    assert runtime.state.completed_game_ids == {"first", "second"}
    assert not runtime.state.completed_game_ids & runtime.state.cancelled_game_ids
    assert runtime.state.pending_completion_counts == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("sink", ["db", "state"])
async def test_failed_completion_remains_eligible_after_queued_reschedule(runtime, tmp_path, monkeypatch, sink):
    entered, release = threading.Event(), threading.Event()

    def fail(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        raise OSError("injected persistence failure")

    ctx = _schedule_context(runtime, tmp_path)
    ctx.orchestrator = SimpleNamespace(request_stop=lambda: None)
    service = ScheduleMutationService()
    with monkeypatch.context() as patch:
        if sink == "db":
            patch.setattr(runtime.db, "append_record_list", fail)
        else:
            patch.setattr(state_store, "write_json_atomic", fail)
        task = asyncio.create_task(_complete(runtime, "first"))
        try:
            await _wait_for(entered)
            await service.request_reschedule(runtime.state, ctx, seed="42")
            assert [spec.game_id for spec in runtime.state.pending_reschedule] == ["first", "new"]
        finally:
            release.set()
            with pytest.raises(OSError, match="injected persistence failure"):
                await task

    assert runtime.state.pending_completion_counts == {}
    assert runtime.progress == []
    assert (runtime.db.load_record(game_name="first") is not None) == (sink == "state")
    assert runtime.state.completed_game_ids == ({"first"} if sink == "state" else set())
    # Failure aborts the run, rather than silently applying a pending schedule.
    # Even if the queued schedule is subsequently applied, only real completed
    # IDs exclude work; a protected prefix does not mark its entries completed.
    assert await service.has_applied_pending_reschedule(runtime.state, ctx)
    pending_service = PendingRuntimeOrchestrationService()
    pending = pending_service.collect_pending_specs(
        state=pending_service.initialize_state(runtime.state.game_schedule),
        completed_game_ids=runtime.state.completed_game_ids,
        cancelled_game_ids=runtime.state.cancelled_game_ids,
    )
    assert [spec.game_id for spec in pending] == (["first", "new"] if sink == "db" else ["new"])
    saved = json.loads((tmp_path / "schedule.json").read_text(encoding="utf-8"))
    assert [game["game_id"] for game in saved["games"]] == ["first", "new"]


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["cancel_pending", "restore", "apply_reschedule"])
async def test_cancel_during_state_save_finishes_mutation_stop_or_notification(
    runtime,
    tmp_path,
    monkeypatch,
    mutation,
):
    entered, release = threading.Event(), threading.Event()
    original = state_store.write_json_atomic

    def slow(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        original(*args, **kwargs)

    monkeypatch.setattr(state_store, "write_json_atomic", slow)
    ctx = _schedule_context(runtime, tmp_path)
    service = ScheduleMutationService()
    if mutation == "cancel_pending":
        operation = service.cancel_pending_games(runtime.state, ctx)
    elif mutation == "restore":
        runtime.state.game_schedule = [_make_game("first")]
        runtime.state.cancelled_game_ids.add("second")
        runtime.state.cancelled_specs["second"] = _make_game("second")
        operation = service.restore_game(runtime.state, ctx, "second")
    else:
        runtime.state.pending_reschedule = [_make_game("new")]
        operation = service.has_applied_pending_reschedule(runtime.state, ctx)
    task = asyncio.create_task(operation)
    try:
        await _wait_for(entered)
        task.cancel()
        await asyncio.sleep(0.01)
        task.cancel()
        await asyncio.sleep(0.01)
        assert not task.done()
        assert ctx.stop_controller.reason is None
        assert not runtime.state.schedule_wait_event.is_set()
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    if mutation == "cancel_pending":
        assert ctx.stop_controller.reason == "cancelled"
    else:
        assert runtime.state.schedule_wait_event.is_set()


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["cancel", "cancel_pending", "restore", "reschedule"])
async def test_mutation_rechecks_lifecycle_after_waiting_for_schedule_lock(runtime, tmp_path, mutation):
    waiting = asyncio.Event()

    class ObservedLock(asyncio.Lock):
        async def acquire(self):
            if self.locked():
                waiting.set()
            return await super().acquire()

    runtime.state.reschedule_lock = ObservedLock()
    ctx = _schedule_context(runtime, tmp_path)
    active = {}
    instance = SimpleNamespace(name="local", active_game_by_id=active)
    if mutation != "reschedule":
        ctx.instance_pool = SimpleNamespace(list_instances=lambda: [instance])
    else:
        ctx.orchestrator = SimpleNamespace(request_stop=lambda: None)
    service = ScheduleMutationService()
    operations = {
        "cancel": lambda: service.cancel_game(runtime.state, ctx, "first"),
        "cancel_pending": lambda: service.cancel_pending_games(runtime.state, ctx),
        "restore": lambda: service.restore_game(runtime.state, ctx, "first"),
        "reschedule": lambda: service.request_reschedule(runtime.state, ctx, seed="42"),
    }
    await runtime.state.reschedule_lock.acquire()
    task = asyncio.create_task(operations[mutation]())
    try:
        await asyncio.wait_for(waiting.wait(), 5)
        if mutation == "reschedule":
            # Actual completion is independent of the dashboard schedule lock.
            await _complete(runtime, "first")
        else:
            active["first"] = object()
    finally:
        runtime.state.reschedule_lock.release()
    if mutation in ("cancel", "restore"):
        with pytest.raises(RuntimeError, match="currently running"):
            await task
        assert not runtime.state.cancelled_game_ids
    elif mutation == "cancel_pending":
        result = await task
        assert result["cancelled_games"] == ["second"]
        assert "first" not in runtime.state.cancelled_game_ids
    else:
        await task
        assert [spec.game_id for spec in runtime.state.pending_reschedule] == ["first", "new"]


@pytest.mark.asyncio
async def test_cancel_during_initial_state_save_finishes_schedule_and_notification(runtime, tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original = state_store.write_json_atomic
    order = []

    def slow(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        original(*args, **kwargs)
        order.append("state")

    monkeypatch.setattr(state_store, "write_json_atomic", slow)
    service = ScheduleMutationService()
    schedule_ctx = _schedule_context(runtime, tmp_path)

    def write_schedule(schedule):
        service.write_schedule_file(runtime.state, schedule_ctx, schedule)
        order.append("schedule")

    ctx = SimpleNamespace(
        run_dir=tmp_path,
        config=runtime.config,
        scheduler=_SchedulerStub([_make_game("new")]),
        state=runtime.state,
        reorder_and_shuffle=lambda schedule: schedule,
        reset_schedule_tracking=lambda: None,
        build_save_context=lambda: runtime.save_context,
        write_schedule_file=write_schedule,
        notify_schedule_available=lambda: order.append("notify"),
    )
    task = asyncio.create_task(runtime.store.setup_new_tournament(ctx))
    try:
        await _wait_for(entered)
        task.cancel()
        await asyncio.sleep(0.01)
        assert not task.done() and order == []
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert order == ["state", "schedule", "notify"]
    assert json.loads((tmp_path / "schedule.json").read_text(encoding="utf-8"))["games"][0]["game_id"] == "new"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["metadata", "illegal_move"])
async def test_failed_worker_write_releases_board_before_exception_crosses_threads(runtime, monkeypatch, failure):
    created, destroyed = [], []
    original_board = record_store.Board

    class TrackedBoard:
        def __init__(self):
            self.inner = original_board()
            created.append(threading.get_ident())

        def __getattr__(self, name):
            return getattr(self.inner, name)

        def __del__(self):
            destroyed.append(threading.get_ident())

    record = _record("failed")
    record.update_metadata({"game_type": "arena"})

    class InvalidRecord:
        def __getattr__(self, name):
            return getattr(record, name)

    invalid = InvalidRecord()
    if failure == "metadata":
        invalid.metadata = SimpleNamespace(
            attributes=record.metadata.attributes,
            black_player=None,
            white_player="EngineB",
        )
        expected = "Record player names must be defined"
    else:
        # A white move in startpos reaches the real Board.push_move error path.
        invalid.moves = [record.moves[1]]
        expected = "legal move payload"

    monkeypatch.setattr(record_store, "Board", TrackedBoard)
    with pytest.raises(ValueError, match=expected) as caught:
        await asyncio.to_thread(runtime.db.append_record_list, [invalid])
    # Keep the traceback and cause alive on the event-loop thread. They must no
    # longer hold Board refs, even for the nested illegal-move exception chain.
    assert caught.value.__traceback__ is not None
    assert created and created[0] != threading.get_ident()
    assert destroyed == created
    if failure == "illegal_move":
        assert caught.value.__cause__ is not None
    assert runtime.db.load_record(game_name="failed") is None
