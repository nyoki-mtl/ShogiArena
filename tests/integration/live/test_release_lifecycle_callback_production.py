"""Real-subprocess evidence for the public EngineLifecycle callback contract."""

from __future__ import annotations

from pathlib import Path

import pytest
from test_tournament_composition_timeout import _write_engine_script

from shogiarena.engine import EngineLifecycleEvent, UsiThinkRequest, create_engine_from_mapping


@pytest.mark.asyncio
async def test_representative_handler_receives_each_state_transition_once_and_in_order(tmp_path: Path) -> None:
    """公開 factory の実 subprocess で callback 頻度と順序を固定する。"""

    launch_path = _write_engine_script(
        tmp_path,
        "lifecycle_usi_engine",
        delay_s=0.0,
        slow_only_as_white=False,
    )
    engine = await create_engine_from_mapping(
        {
            "name": "LifecycleIntegrationMock",
            "engine_path": str(launch_path),
        }
    )
    events: list[EngineLifecycleEvent] = []
    unregister = engine.register_lifecycle_handler(events.append)
    try:
        await engine.start()
        await engine.new_game()
        result = await engine.think(
            sfen="startpos",
            request=UsiThinkRequest(movetime=100),
        )
        assert result.bestmove is not None
        await engine.close()
    finally:
        if engine.is_running:
            await engine.close()
        unregister()

    state_events = [event for event in events if event.name == "state_changed"]
    state_sequence = [event.state for event in state_events]
    assert state_sequence == [
        "not_ready",
        "waiting_for_readyok",
        "ready",
        "waiting_for_bestmove",
        "ready",
        "will_quit",
        "quit_completed",
    ]

    previous_state = "waiting_for_usiok"
    for event in state_events:
        assert event.state != previous_state, f"duplicate callback for unchanged state: {event.state}"
        previous_state = str(event.state)

    assert [(event.name, event.state) for event in events] == [
        ("process_started", "waiting_for_usiok"),
        ("state_changed", "not_ready"),
        ("usiok", "not_ready"),
        ("options_applied", "not_ready"),
        ("state_changed", "waiting_for_readyok"),
        ("state_changed", "ready"),
        ("readyok", "ready"),
        ("new_game", "ready"),
        ("state_changed", "waiting_for_bestmove"),
        ("think_started", "waiting_for_bestmove"),
        ("state_changed", "ready"),
        ("think_finished", "ready"),
        ("state_changed", "will_quit"),
        ("state_changed", "quit_completed"),
        ("process_exited", "quit_completed"),
    ]
    assert [event.monotonic_ns for event in events] == sorted(event.monotonic_ns for event in events)
    assert events[-1].process_info is not None
