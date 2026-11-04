"""Local execution owns its manifest write through cancellation and cleanup."""

from __future__ import annotations

import asyncio
import json
import threading
from types import SimpleNamespace

import pytest

from shogiarena._core.contexts.game_session.adapters.orchestration import game_execution
from shogiarena._core.contexts.game_session.adapters.orchestration.config_engine import EngineConfig
from shogiarena._core.contexts.game_session.application.session.run_failure_record_service import (
    RunFailureRecordService,
)
from shogiarena._core.shared.kernel.time_control import TimeControlLimits


class _ReadyToStart(Exception):
    pass


@pytest.fixture
def execution(tmp_path, monkeypatch):
    owner = SimpleNamespace(
        engine_pool=object(),
        instance_pool=None,
        run_dir=tmp_path,
        session_context=SimpleNamespace(run_id="run"),
        extra_options={},
        config=SimpleNamespace(rules=object()),
        _engine_lifecycle="reuse",
        _timeout_reclassification_enabled=True,
        _game_execution_manifest_lock=asyncio.Lock(),
    )
    config = EngineConfig(name="engine", engine_path=tmp_path / "engine.yaml")
    prepared, starting, cleaned = [], [], []
    manifest_requests = []
    original_persist = game_execution._persist_execution_manifest

    async def persist(lock, **kwargs):
        manifest_requests.append(kwargs["game_id"])
        return await original_persist(lock, **kwargs)

    monkeypatch.setattr(game_execution, "_persist_execution_manifest", persist)

    async def prepare(owner, spec):
        prepared.append(spec.game_id)
        return game_execution._ResourceContext({}, False, {}, config, config)

    async def cleanup(owner, spec, **kwargs):
        cleaned.append(spec.game_id)

    def resolve(**kwargs):
        game_id = kwargs["game_id"]
        return SimpleNamespace(
            spec=SimpleNamespace(black_engine=game_id, white_engine=game_id),
            engine_provenance=(None, None),
            manifest_payload=lambda: {"sequence": game_id},
        )

    def materialize(engine, **kwargs):
        starting.append(engine)
        raise _ReadyToStart

    monkeypatch.setattr(game_execution, "_prepare_resource_context", prepare)
    monkeypatch.setattr(game_execution, "_cleanup_execution", cleanup)
    monkeypatch.setattr(game_execution, "resolve_local_game_execution", resolve)
    layers = SimpleNamespace(artifact_overlay={}, arena={}, declared_overlays={}, inline={})
    monkeypatch.setattr(game_execution, "build_usi_option_layers", lambda *args: layers)
    monkeypatch.setattr(game_execution, "validate_minimum_worker_version", lambda *args, **kwargs: None)
    monkeypatch.setattr(game_execution, "_local_secret_values", lambda *args: {})
    monkeypatch.setattr(game_execution, "_artifact_paths", lambda *args: {})
    monkeypatch.setattr(game_execution, "materialize_engine_config", materialize)

    def start(game_id):
        item = SimpleNamespace(pool_key="engine", config_path=tmp_path / "engine.yaml")
        spec = SimpleNamespace(
            game_id=game_id,
            black_item=item,
            white_item=item,
            initial_sfen="startpos",
            black_limits=TimeControlLimits(fixed_time_ms=10),
            white_limits=TimeControlLimits(fixed_time_ms=10),
            black_variant_options={},
            white_variant_options={},
            black_variant_id=None,
            white_variant_id=None,
            clear_hash_before_game=False,
            after_variant_setoption="none",
        )
        return asyncio.create_task(game_execution.execute_game(owner, spec))

    return SimpleNamespace(
        owner=owner,
        start=start,
        prepared=prepared,
        starting=starting,
        cleaned=cleaned,
        manifest_requests=manifest_requests,
    )


async def _wait_until(predicate):
    async with asyncio.timeout(5):
        while not predicate():
            await asyncio.sleep(0.001)


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
@pytest.mark.parametrize("fail", [False, True])
async def test_manifest_is_drained_before_start_or_cleanup(execution, monkeypatch, cancel, fail):
    entered, release = threading.Event(), threading.Event()
    original = game_execution.persist_game_execution_manifest

    def save(**kwargs):
        entered.set()
        assert release.wait(5)
        if fail:
            raise OSError("manifest disk failure")
        return original(**kwargs)

    monkeypatch.setattr(game_execution, "persist_game_execution_manifest", save)
    task = execution.start("first")
    try:
        await _wait_until(entered.is_set)
        if cancel:
            for _ in range(2):
                task.cancel("stop")
                await asyncio.sleep(0)
        assert not task.done()
        assert execution.starting == execution.cleaned == []
        assert execution.owner._game_execution_manifest_lock.locked()
    finally:
        release.set()
        expected = OSError if fail else asyncio.CancelledError if cancel else _ReadyToStart
        with pytest.raises(expected):
            await task
    assert execution.cleaned == ["first"]
    assert execution.starting == (["first"] if not cancel and not fail else [])
    paths = list(execution.owner.run_dir.glob("game_execution_specs/*.json"))
    assert len(paths) == (0 if fail else 1)
    if paths:
        assert json.loads(paths[0].read_text(encoding="utf-8"))["game_id"] == "first"
    assert not execution.owner._game_execution_manifest_lock.locked()


@pytest.mark.asyncio
async def test_manifest_order_includes_cancelled_lock_waiter(execution, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    order = []
    original = game_execution.persist_game_execution_manifest

    def save(**kwargs):
        game_id = kwargs["game_id"]
        if game_id == "first":
            entered.set()
            assert release.wait(5)
        result = original(**kwargs)
        order.append(game_id)
        return result

    monkeypatch.setattr(game_execution, "persist_game_execution_manifest", save)
    tasks = [execution.start("first")]
    try:
        await _wait_until(entered.is_set)
        tasks.append(execution.start("second"))
        await _wait_until(lambda: len(execution.manifest_requests) == 2)
        await asyncio.sleep(0)
        tasks[1].cancel("stop waiting")
        await asyncio.sleep(0)
        tasks[1].cancel("stop again")
        tasks.append(execution.start("third"))
        await _wait_until(lambda: len(execution.manifest_requests) == 3)
        assert execution.starting == execution.cleaned == order == []
        assert not any(task.done() for task in tasks)
    finally:
        release.set()
        results = await asyncio.gather(*tasks, return_exceptions=True)
    assert isinstance(results[0], _ReadyToStart)
    assert isinstance(results[1], asyncio.CancelledError)
    assert isinstance(results[2], _ReadyToStart)
    assert order == ["first", "second", "third"]
    assert execution.starting == ["first", "third"]
    assert sorted(execution.cleaned) == ["first", "second", "third"]


@pytest.mark.asyncio
@pytest.mark.parametrize("diagnostic_fails", [False, True])
async def test_manifest_failure_survives_cancellation_during_diagnostic_write(execution, monkeypatch, diagnostic_fails):
    entered, release = threading.Event(), threading.Event()
    original_append = RunFailureRecordService.append_failure
    failure = OSError("manifest disk failure")

    def save(**kwargs):
        raise failure

    def append(self, **kwargs):
        entered.set()
        assert release.wait(5)
        if diagnostic_fails:
            raise OSError("diagnostic disk failure")
        return original_append(self, **kwargs)

    monkeypatch.setattr(game_execution, "persist_game_execution_manifest", save)
    monkeypatch.setattr(RunFailureRecordService, "append_failure", append)
    task = execution.start("first")
    try:
        await _wait_until(entered.is_set)
        for _ in range(2):
            task.cancel("stop during diagnostic write")
            await asyncio.sleep(0)
        assert not task.done()
        assert execution.starting == execution.cleaned == []
    finally:
        release.set()
        with pytest.raises(OSError, match="manifest disk failure") as raised:
            await task
    assert raised.value is failure
    assert execution.cleaned == ["first"]
    assert execution.starting == []
    records = RunFailureRecordService().load_failures(execution.owner.run_dir)
    assert len(records) == (0 if diagnostic_fails else 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_spec_resolution_yields_and_is_drained_before_cleanup(execution, monkeypatch, cancel):
    entered, release = threading.Event(), threading.Event()
    original = game_execution.resolve_local_game_execution
    main_thread = threading.get_ident()

    def resolve(**kwargs):
        assert threading.get_ident() != main_thread
        entered.set()
        assert release.wait(5)
        return original(**kwargs)

    monkeypatch.setattr(game_execution, "resolve_local_game_execution", resolve)
    task = execution.start("first")
    try:
        await _wait_until(entered.is_set)
        if cancel:
            for _ in range(2):
                task.cancel("cancel resolving")
                await asyncio.sleep(0)
        assert not task.done()
        assert execution.starting == execution.cleaned == execution.manifest_requests == []
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError if cancel else _ReadyToStart):
            await task
    assert execution.cleaned == ["first"]
