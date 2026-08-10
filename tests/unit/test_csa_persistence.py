from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from shogiarena._core.contexts.csa_watch.adapters.rsshogi_replay import RsshogiBoardReplay
from shogiarena._core.contexts.csa_watch.application.game_persistence import CsaGamePersister
from shogiarena._core.contexts.csa_watch.application.run_watcher import RunView
from shogiarena._core.contexts.csa_watch.domain.game_identity import csa_game_key
from shogiarena._core.contexts.csa_watch.ports.record_sink_ports import CsaRecordStoreRetryableError

from .test_csa_event_fold import EVEN_COMPLETE, fold_fixture


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class _Store:
    def __init__(self, failures: list[Exception] | None = None) -> None:
        self.failures = list(failures or [])
        self.calls = 0
        self.records: list[object] = []

    def persist(self, _record: object) -> None:
        self.calls += 1
        if self.failures:
            raise self.failures.pop(0)
        self.records.append(_record)

    def close(self) -> None:
        return None


def _view(*, restarted: bool = False) -> RunView:
    return RunView(
        worker_idx=0,
        state=fold_fixture(EVEN_COMPLETE),
        path=Path("fixture-events.jsonl"),
        restarted=restarted,
        stream_generation=1 if restarted else 0,
    )


def test_retryable_failure_uses_backoff_before_succeeding() -> None:
    clock = _Clock()
    store = _Store([CsaRecordStoreRetryableError("database is locked")])
    persister = CsaGamePersister(store, RsshogiBoardReplay(), monotonic=clock)

    persister.persist_finished([_view()])
    game_id = csa_game_key(_view().state.run_id, _view().state.games[-1].game_id)
    assert store.calls == 1
    assert persister.snapshot()["games"][game_id]["state"] == "retrying"

    clock.now = 0.5
    persister.persist_finished([_view()])
    assert store.calls == 1

    clock.now = 1.0
    persister.persist_finished([_view()])
    assert store.calls == 2
    assert persister.snapshot()["games"][game_id]["state"] == "persisted"
    assert not persister.has_deferred


def test_nonretryable_failure_is_reported_without_a_tight_retry_loop() -> None:
    store = _Store([ValueError("schema mismatch")])
    persister = CsaGamePersister(store, RsshogiBoardReplay())

    persister.persist_finished([_view()])
    persister.persist_finished([_view()])

    game_id = csa_game_key(_view().state.run_id, _view().state.games[-1].game_id)
    assert store.calls == 1
    assert persister.snapshot()["games"][game_id]["state"] == "failed"
    assert not persister.has_deferred


def test_retry_backoff_doubles_and_is_capped_at_sixty_seconds() -> None:
    clock = _Clock()
    store = _Store([CsaRecordStoreRetryableError("locked") for _ in range(8)])
    persister = CsaGamePersister(store, RsshogiBoardReplay(), monotonic=clock)
    delays = [1, 2, 4, 8, 16, 32, 60, 60]

    for attempt, delay in enumerate(delays, start=1):
        persister.persist_finished([_view()])
        game_id = csa_game_key(_view().state.run_id, _view().state.games[-1].game_id)
        status = persister.status_for_game(game_id)
        assert status is not None
        assert status.attempts == attempt
        assert status.next_retry_at == clock.now + delay
        clock.now += delay


def test_log_restart_suspends_database_writes_until_rebuild() -> None:
    store = _Store()
    persister = CsaGamePersister(store, RsshogiBoardReplay())

    view = _view(restarted=True)
    persister.persist_finished([view])

    assert store.calls == 0
    status = persister.snapshot()["runs"][view.state.run_id]
    assert status["state"] == "failed"
    assert "再構築" in status["detail"]


def test_same_server_game_id_in_two_runs_is_persisted_twice() -> None:
    store = _Store()
    persister = CsaGamePersister(store, RsshogiBoardReplay())
    first = _view()
    second = RunView(
        worker_idx=1,
        state=replace(fold_fixture(EVEN_COMPLETE), run_id="second-run"),
        path=Path("second-events.jsonl"),
    )

    persister.persist_finished([first, second])

    assert store.calls == 2
    game_names = {record.game_name for record in store.records}  # type: ignore[attr-defined]
    assert len(game_names) == 2
