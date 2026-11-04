from __future__ import annotations

import asyncio
import signal
import threading
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
import rsshogi.record

from shogiarena._core.contexts.game_session.application.orchestration.update_batch_execution_service import (
    SpsaUpdateBatchExecutionRequest,
    SpsaUpdateBatchExecutionService,
)
from shogiarena._core.contexts.game_session.application.session.run_controller import RunController
from shogiarena._core.contexts.spsa.adapters.runner import SpsaRunner
from shogiarena._core.contexts.spsa.adapters.runner_session_lifecycle import persist_spsa_game_completion
from shogiarena._core.platform.db.store.arena_db_adapter import ArenaDBAdapter
from shogiarena._core.platform.db.store.repository_factory import SQLiteShogiDBFactory
from shogiarena._core.shared.kernel.game_results import GameResult
from shogiarena._core.shared.kernel.participation_records import (
    GameParticipationRecord,
    serialize_participation_records,
)
from shogiarena._core.shared.kernel.session_hooks import GameCompletionEvent


def _event(game_id="game"):
    identity = {
        "run_id": "run",
        "update_idx": 1,
        "pair_id": "pair",
        "attempt_id": f"attempt-{game_id}",
        "observation_kind": "SPSA",
    }
    participation = [
        GameParticipationRecord(role=role, engine_name=role, run_id="run", extra={"spsa_identity": identity})
        for role in ("black", "white")
    ]
    record = rsshogi.record.Record.from_usi_main_line(
        "startpos",
        ["7g7f", "3c3d"],
        result=GameResult.BLACK_WIN,
        metadata=rsshogi.record.RecordMetadata(
            game_name=game_id,
            game_type="spsa",
            black_player="black",
            white_player="white",
            updated_date=datetime.now(UTC).isoformat(),
            attributes={"_arena_participation": serialize_participation_records(participation)},
        ),
    )
    return GameCompletionEvent(game_id=game_id, game_info=record, payload=None)


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_spsa_db_write_yields_and_drains_before_ledger(tmp_path, cancel):
    db = ArenaDBAdapter(SQLiteShogiDBFactory(tmp_path / "game.db"))
    db.ensure_schema()
    entered, release = threading.Event(), threading.Event()
    main_thread = threading.get_ident()
    order = []
    lock = asyncio.Lock()
    original = db.append_record_with_participation

    def slow_append(record, *, participation):
        assert threading.get_ident() != main_thread
        entered.set()
        assert release.wait(5)
        result = original(record, participation=participation)
        order.append("db-committed")
        return result

    def insert(observation):
        assert threading.get_ident() == main_thread
        assert lock.locked()
        assert observation.game_db_id > 0
        order.append("ledger-committed")

    db.append_record_with_participation = slow_append
    runner = SimpleNamespace(
        _state=SimpleNamespace(
            db_service=db,
            completion_lock=lock,
            observation_ledger=SimpleNamespace(insert_committed=insert),
            live_ledger_writer=None,
        ),
        _request_derived_json_projection=lambda: None,
        progress=SimpleNamespace(on_game_complete=lambda _: None),
    )
    payload = SimpleNamespace(update_idx=1, phase="plus", winner_code=1)
    task = asyncio.create_task(SpsaRunner._handle_game_completion(runner, _event(), payload))
    try:
        async with asyncio.timeout(3):
            while not entered.is_set():
                await asyncio.sleep(0.001)
        # This task could not resume here if the production path blocked the loop.
        assert not task.done() and order == []
        if cancel:
            task.cancel()
            await asyncio.sleep(0.01)
            task.cancel()  # repeated stop must not abandon the writer
            await asyncio.sleep(0.01)
            assert not task.done() and lock.locked()
        release.set()
        if cancel:
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            assert await task is None
        assert order == ["db-committed", "ledger-committed"]
        assert not lock.locked()
        loaded = db.load_record(game_name="game")
        assert loaded is not None and len(loaded.moves) == 2
        assert len(db.get_spsa_game_database_records(run_id="run")) == 1
    finally:
        release.set()
        if not task.done():
            await task
        db.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("stop_signal", [signal.SIGINT, signal.SIGTERM])
async def test_spsa_signal_drains_terminal_games_waiting_for_completion_lock(tmp_path, monkeypatch, stop_signal):
    db = ArenaDBAdapter(SQLiteShogiDBFactory(tmp_path / "game.db"))
    db.ensure_schema()
    entered, release = threading.Event(), threading.Event()
    second_entered, second_release = threading.Event(), threading.Event()
    waiting, shutdown_started = asyncio.Event(), asyncio.Event()
    order, observations, cleanup = [], [], []
    original = db.append_record_with_participation
    main_thread = threading.get_ident()

    class CompletionLock(asyncio.Lock):
        async def acquire(self):
            if self.locked():
                waiting.set()
            return await super().acquire()

    lock = CompletionLock()

    def slow_append(record, *, participation):
        assert threading.get_ident() != main_thread
        if record.game_name == "first":
            entered.set()
            assert release.wait(5)
        else:
            second_entered.set()
            assert second_release.wait(5)
        result = original(record, participation=participation)
        order.append((record.game_name, "db"))
        return result

    def insert(observation):
        assert threading.get_ident() == main_thread and lock.locked()
        observations.append(observation)
        order.append((observation.game_id, "ledger"))

    db.append_record_with_participation = slow_append
    runner = SimpleNamespace(
        _state=SimpleNamespace(
            db_service=db,
            completion_lock=lock,
            observation_ledger=SimpleNamespace(insert_committed=insert),
            live_ledger_writer=None,
        ),
        _request_derived_json_projection=lambda: None,
        progress=SimpleNamespace(on_game_complete=lambda _: None),
    )
    payload = SimpleNamespace(update_idx=1, phase="plus", winner_code=1)
    workers = []

    async def run_pair(request):
        workers.append(asyncio.current_task())
        await SpsaRunner._handle_game_completion(runner, _event(request.start_sfen), payload)
        return 0.0, None, None

    async def run():
        await SpsaUpdateBatchExecutionService().execute(
            request=SpsaUpdateBatchExecutionRequest(
                update_idx=1,
                batch_size=2,
                num_workers=2,
                inflight_factor=1,
                sfens=["first", "second"],
                opening_indices=[0, 1],
            ),
            tuned_plus=[],
            tuned_minus=[],
            tuned_plus_options=None,
            tuned_minus_options=None,
            current_params=[],
            reserve_pending_game=lambda request: f"{request.pair_id}-{request.game_slot}",
            run_game_pair=run_pair,
        )

    async def shutdown():
        # SPSA batch tasks are owned by execute(), not registered with the base
        # orchestrator. Shutdown cannot compensate for an undrained batch.
        shutdown_started.set()

    async def stop_services():
        cleanup.append([observation.game_id for observation in observations])

    handlers = {}
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(
        loop, "add_signal_handler", lambda sig, callback, *args: handlers.update({sig: (callback, args)})
    )
    monkeypatch.setattr(loop, "remove_signal_handler", lambda _sig: None)
    controller = RunController(
        attach_orchestrator=lambda _: None,
        detach_orchestrator=lambda: None,
        stop_services=stop_services,
    )
    orchestrator = SimpleNamespace(request_stop=lambda: None, shutdown=shutdown)
    task = asyncio.create_task(controller.run_orchestrator(orchestrator, run()))
    try:
        async with asyncio.timeout(3):
            await waiting.wait()
            while not entered.is_set():
                await asyncio.sleep(0.001)
            callback, args = handlers[stop_signal]
            callback(*args)
            await shutdown_started.wait()
            await asyncio.sleep(0)
            assert not task.done() and lock.locked() and cleanup == []
            release.set()
            while not second_entered.is_set():
                await asyncio.sleep(0.001)
            assert not task.done() and cleanup == []
            second_release.set()
            assert await task is None
        assert order == [("first", "db"), ("first", "ledger"), ("second", "db"), ("second", "ledger")]
        assert cleanup == [["first", "second"]]
        assert all(worker.cancelled() for worker in workers)
        assert not lock.locked()
        for game_id in ("first", "second"):
            loaded = db.load_record(game_name=game_id)
            assert loaded is not None and len(loaded.moves) == 2
        records = db.get_spsa_game_database_records(run_id="run")
        assert len(records) == 2
        assert {record.game_db_id for record in records} == {item.game_db_id for item in observations}
    finally:
        release.set()
        second_release.set()
        await asyncio.gather(task, *workers, return_exceptions=True)
        db.close()


@pytest.mark.asyncio
async def test_spsa_db_failure_does_not_insert_observation():
    def fail(*args, **kwargs):
        raise OSError("disk failure")

    inserted = []
    with pytest.raises(OSError, match="disk failure"):
        await persist_spsa_game_completion(
            db_service=SimpleNamespace(append_record_with_participation=fail),
            event=_event(),
            observation_ledger=SimpleNamespace(insert_committed=inserted.append),
        )
    assert inserted == []
