from __future__ import annotations

from shogiarena._core.contexts.game_session.application.orchestration.local_execution_spec_service import (
    LocalExecutionSpecRequest,
    build_local_execution_spec,
)
from shogiarena._core.contexts.game_session.ports.session_runner_ports import (
    BeforeGameHookPort,
    BeforeGameHookRequest,
    BeforeGameHookResult,
)
from shogiarena._core.shared.kernel.time_control import TimeControlLimits


def _limits() -> TimeControlLimits:
    return TimeControlLimits(time_ms=10_000, increment_ms=100)


def test_build_local_execution_spec_without_optional_fields() -> None:
    payload = build_local_execution_spec(
        request=LocalExecutionSpecRequest(
            black_item="black-item",
            white_item="white-item",
            initial_sfen="startpos",
            game_id="g1",
            black_limits=_limits(),
            white_limits=_limits(),
        ),
        game_execution_spec_factory=lambda **kwargs: kwargs,
    )

    assert payload["black_item"] == "black-item"
    assert payload["white_item"] == "white-item"
    assert payload["game_id"] == "g1"
    assert payload["initial_sfen"] == "startpos"
    assert "game_round" not in payload
    assert "before_game_hook" not in payload
    assert "on_game_start" not in payload


def test_build_local_execution_spec_with_optional_fields() -> None:
    class _StubHook:
        async def run(self, request: BeforeGameHookRequest) -> BeforeGameHookResult | None:
            return None

    _hook: BeforeGameHookPort = _StubHook()

    async def _on_game_start() -> None:
        return None

    payload = build_local_execution_spec(
        request=LocalExecutionSpecRequest(
            black_item="black-item",
            white_item="white-item",
            initial_sfen="startpos",
            game_id="g2",
            black_limits=_limits(),
            white_limits=_limits(),
            game_round=5,
            before_game_hook=_hook,
            on_game_start=_on_game_start,
        ),
        game_execution_spec_factory=lambda **kwargs: kwargs,
    )

    assert payload["game_round"] == 5
    assert payload["before_game_hook"] is _hook
    assert payload["on_game_start"] is _on_game_start
