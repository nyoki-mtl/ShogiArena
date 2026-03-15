from __future__ import annotations

import asyncio
import logging

from shogiarena._core.contexts.game_session.application.orchestration import (
    game_preflight_service as module,
)


class _DummyLimits:
    def __init__(self, spec: str) -> None:
        self._spec = spec

    def to_spec_str(self) -> str:
        return self._spec


def test_should_skip_cancelled_game_without_provider() -> None:
    assert (
        module.should_skip_cancelled_game(
            game_id="g1",
            cancelled_provider=None,
            logger=logging.getLogger(__name__),
        )
        is False
    )


def test_should_skip_cancelled_game_when_game_is_cancelled() -> None:
    assert (
        module.should_skip_cancelled_game(
            game_id="g1",
            cancelled_provider=lambda: {"g1", "g2"},
            logger=logging.getLogger(__name__),
        )
        is True
    )


def test_should_skip_cancelled_game_handles_provider_error() -> None:
    def _raise() -> set[str]:
        raise RuntimeError("boom")

    assert (
        module.should_skip_cancelled_game(
            game_id="g1",
            cancelled_provider=_raise,
            logger=logging.getLogger(__name__),
        )
        is False
    )


def test_emit_game_assigned_event_builds_payload(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    calls: list[tuple[object, str, dict[str, object], int, bool]] = []

    def _capture(
        progress_queue: object,
        game_id: str,
        payload: dict[str, object],
        *,
        fallback_move_count: int,
        should_allow_default_str: bool,
    ) -> None:
        calls.append((progress_queue, game_id, payload, fallback_move_count, should_allow_default_str))

    monkeypatch.setattr(module, "enqueue_progress_event", _capture)

    queue: asyncio.Queue[tuple[int, int, str | None]] = asyncio.Queue()
    module.emit_game_assigned_event(
        progress_queue=queue,
        game_id="g1",
        initial_sfen="sfen lnsgkgsnl/1r5b1/p1ppppppp/9/9/9/PPSPPPPPP/1B5R1/LNSGKGSNL b - 1",
        black_name="black",
        white_name="white",
        black_limits=_DummyLimits("b_tc"),
        white_limits=_DummyLimits("w_tc"),
    )

    assert len(calls) == 1
    progress_queue, game_id, payload, fallback_move_count, should_allow_default_str = calls[0]
    assert progress_queue is queue
    assert game_id == "g1"
    assert fallback_move_count == 0
    assert should_allow_default_str is True
    assert payload["type"] == "game_assigned"
    assert payload["game_id"] == "g1"
    assert payload["black_name"] == "black"
    assert payload["white_name"] == "white"
    assert payload["time_control_black"] == "b_tc"
    assert payload["time_control_white"] == "w_tc"
