from __future__ import annotations

from shogiarena._core.contexts.dashboard.application.engine_io_mediator import EngineIoMediator
from shogiarena._core.contexts.dashboard.application.state_container import DashboardState
from shogiarena._core.shared.kernel.snapshots import EngineIoTailEntry


def test_resolve_snapshot_from_topic_returns_engine_io_snapshot() -> None:
    state = DashboardState()
    logs = state.ensure_engine_io_buffers("g1", maxlen=1000)
    logs["black"].append(EngineIoTailEntry(dir="out", line="info depth 12", ts=111))

    mediator = EngineIoMediator(state=state, log_limit=1000)
    resolved = mediator.resolve_snapshot_from_topic("live.engine.g1.black.io.snapshot")

    assert resolved == (
        "live.engine.g1.black.io.snapshot",
        {
            "gid": "g1",
            "role": "black",
            "entries": [{"dir": "out", "line": "info depth 12", "ts": 111}],
            "limit": 1000,
        },
    )


def test_resolve_snapshot_from_topic_rejects_invalid_topic() -> None:
    state = DashboardState()
    mediator = EngineIoMediator(state=state)

    assert mediator.resolve_snapshot_from_topic("live.engine.g1.green.io.snapshot") is None
    assert mediator.resolve_snapshot_from_topic("live.engine.g1.black") is None
