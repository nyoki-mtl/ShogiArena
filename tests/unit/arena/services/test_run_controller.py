from __future__ import annotations

import asyncio
import signal
from typing import Any, cast

import pytest

from shogiarena._core.contexts.game_session.application.session.run_controller import RunController


class _OrchestratorStub:
    def __init__(self) -> None:
        self.request_stop_calls = 0
        self.shutdown_calls = 0
        self.run_started = asyncio.Event()
        self.shutdown_started = asyncio.Event()
        self.shutdown_release = asyncio.Event()
        self.run_cancelled = False

    def request_stop(self) -> None:
        self.request_stop_calls += 1

    async def shutdown(self) -> None:
        self.shutdown_calls += 1
        self.shutdown_started.set()
        await self.shutdown_release.wait()

    async def run(self) -> None:
        self.run_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.run_cancelled = True
            raise


@pytest.mark.asyncio
async def test_run_controller_gracefully_stops_orchestrator_on_sigint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attached: list[object] = []
    detached: list[str] = []
    stop_services_calls: list[str] = []
    signal_handlers: dict[signal.Signals, Any] = {}
    removed_signals: list[signal.Signals] = []

    async def _stop_services() -> None:
        stop_services_calls.append("called")

    controller = RunController(
        attach_orchestrator=attached.append,
        detach_orchestrator=lambda: detached.append("done"),
        stop_services=_stop_services,
    )
    orchestrator = _OrchestratorStub()
    loop = asyncio.get_running_loop()

    monkeypatch.setattr(
        loop, "add_signal_handler", lambda sig, callback, *args: signal_handlers.__setitem__(sig, (callback, args))
    )
    monkeypatch.setattr(loop, "remove_signal_handler", lambda sig: removed_signals.append(sig))

    run_task = asyncio.create_task(controller.run_orchestrator(cast(Any, orchestrator), orchestrator.run()))
    await orchestrator.run_started.wait()

    callback, args = signal_handlers[signal.SIGINT]
    callback(*args)
    await orchestrator.shutdown_started.wait()

    orchestrator.shutdown_release.set()
    result = await run_task

    assert result is None
    assert attached == [orchestrator]
    assert detached == ["done"]
    assert stop_services_calls == ["called"]
    assert orchestrator.request_stop_calls >= 1
    assert orchestrator.shutdown_calls == 1
    assert orchestrator.run_cancelled is True
    assert removed_signals == [signal.SIGINT, signal.SIGTERM]


@pytest.mark.asyncio
async def test_run_controller_shuts_down_orchestrator_after_normal_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    detached: list[str] = []
    stop_services_calls: list[str] = []
    signal_handlers: dict[signal.Signals, Any] = {}

    async def _stop_services() -> None:
        stop_services_calls.append("called")

    controller = RunController(
        attach_orchestrator=lambda _orch: None,
        detach_orchestrator=lambda: detached.append("done"),
        stop_services=_stop_services,
    )
    orchestrator = _OrchestratorStub()
    loop = asyncio.get_running_loop()

    monkeypatch.setattr(
        loop, "add_signal_handler", lambda sig, callback, *args: signal_handlers.__setitem__(sig, (callback, args))
    )
    monkeypatch.setattr(loop, "remove_signal_handler", lambda _sig: None)

    async def _run_once() -> str:
        return "finished"

    orchestrator.shutdown_release.set()
    result = await controller.run_orchestrator(cast(Any, orchestrator), _run_once())

    assert result == "finished"
    assert detached == ["done"]
    assert stop_services_calls == []
    assert orchestrator.request_stop_calls == 0
    assert orchestrator.shutdown_calls == 1


@pytest.mark.asyncio
async def test_run_controller_shuts_down_on_unexpected_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    detached: list[str] = []
    stop_services_calls: list[str] = []
    signal_handlers: dict[signal.Signals, Any] = {}

    async def _stop_services() -> None:
        stop_services_calls.append("called")

    controller = RunController(
        attach_orchestrator=lambda _orch: None,
        detach_orchestrator=lambda: detached.append("done"),
        stop_services=_stop_services,
    )
    orchestrator = _OrchestratorStub()
    orchestrator.shutdown_release.set()  # do not block shutdown
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(
        loop, "add_signal_handler", lambda sig, callback, *args: signal_handlers.__setitem__(sig, (callback, args))
    )
    monkeypatch.setattr(loop, "remove_signal_handler", lambda _sig: None)

    async def _failing_run() -> None:
        raise ValueError("boom")

    # A non-cancellation failure must still release the orchestrator and services and re-raise
    # (regression for the missing except branch that leaked them).
    with pytest.raises(ValueError, match="boom"):
        await controller.run_orchestrator(cast(Any, orchestrator), _failing_run())

    assert stop_services_calls == ["called"]
    assert detached == ["done"]
    assert orchestrator.request_stop_calls >= 1
    assert orchestrator.shutdown_calls == 1


class _UnresponsiveShutdownOrchestrator(_OrchestratorStub):
    """Orchestrator whose shutdown ignores cancellation (asyncio.wait_for would hang on it)."""

    async def shutdown(self) -> None:
        self.shutdown_calls += 1
        self.shutdown_started.set()
        while not self.shutdown_release.is_set():
            try:
                await asyncio.sleep(0.02)
            except asyncio.CancelledError:
                # Swallow cancellation and keep going; only shutdown_release can end this.
                continue


@pytest.mark.asyncio
async def test_run_controller_abandons_shutdown_after_hard_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import shogiarena._core.contexts.game_session.application.session.run_controller as rc

    monkeypatch.setattr(rc, "_GRACEFUL_SHUTDOWN_HARD_TIMEOUT_SECONDS", 0.05)

    detached: list[str] = []
    stop_services_calls: list[str] = []
    signal_handlers: dict[signal.Signals, Any] = {}

    async def _stop_services() -> None:
        stop_services_calls.append("called")

    controller = RunController(
        attach_orchestrator=lambda _orch: None,
        detach_orchestrator=lambda: detached.append("done"),
        stop_services=_stop_services,
    )
    # Shutdown ignores cancellation, so awaiting the cancellation (as wait_for does) would hang.
    orchestrator = _UnresponsiveShutdownOrchestrator()
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(
        loop, "add_signal_handler", lambda sig, callback, *args: signal_handlers.__setitem__(sig, (callback, args))
    )
    monkeypatch.setattr(loop, "remove_signal_handler", lambda _sig: None)

    async def _failing_run() -> None:
        raise ValueError("boom")

    # The run loop must return promptly even though the shutdown swallows cancellation.
    with pytest.raises(ValueError, match="boom"):
        await asyncio.wait_for(
            controller.run_orchestrator(cast(Any, orchestrator), _failing_run()),
            timeout=2.0,
        )

    assert stop_services_calls == ["called"]
    assert detached == ["done"]
    assert orchestrator.shutdown_calls == 1

    # Release the abandoned background shutdown so it can finish cleanly.
    orchestrator.shutdown_release.set()
    await asyncio.sleep(0.05)
