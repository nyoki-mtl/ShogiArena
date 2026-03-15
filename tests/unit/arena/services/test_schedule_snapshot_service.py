from __future__ import annotations

import types

import pytest

from shogiarena._core.contexts.tournament.application.runner_state import TournamentRunnerState
from shogiarena._core.contexts.tournament.application.session.schedule_context import TournamentScheduleContext
from shogiarena._core.contexts.tournament.application.session.schedule_snapshot_service import ScheduleSnapshotService
from shogiarena._core.contexts.tournament.domain.tournament_models import GameSpec


class _StopController:
    is_stop_requested = False
    reason = None

    def request_stop(self, *, reason: str | None = None) -> None:
        self.reason = reason


@pytest.mark.asyncio
async def test_schedule_snapshot_uses_injected_engine_instance_defaults(tmp_path) -> None:
    service = ScheduleSnapshotService()
    state = TournamentRunnerState(
        game_schedule=[
            GameSpec(
                black_engine="black",
                white_engine="white",
                initial_sfen="startpos",
                game_id="g-001",
                round_num=0,
            )
        ],
        original_total_games=1,
        session_phase="waiting",
    )
    ctx = TournamentScheduleContext(
        config=types.SimpleNamespace(
            tournament=types.SimpleNamespace(seed=7),
            engines=[],
            rules=types.SimpleNamespace(),
        ),
        scheduler=types.SimpleNamespace(),
        instance_pool=None,
        run_dir=tmp_path,
        stop_controller=_StopController(),
        is_dashboard_enabled=False,
        engine_instance_defaults={"black": "local", "white": "remote-1"},
    )

    snapshot = await service.get_schedule_snapshot(state, ctx)

    assert snapshot["pending_games"] == 1
    assert snapshot["schedule"][0]["resolved_instance_black"] == "local"
    assert snapshot["schedule"][0]["resolved_instance_white"] == "remote-1"
    assert snapshot["schedule"][0]["assigned_instance"] == "split"
