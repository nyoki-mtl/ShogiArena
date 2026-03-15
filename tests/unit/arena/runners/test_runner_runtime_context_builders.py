from __future__ import annotations

from types import SimpleNamespace

from shogiarena._core.contexts.game_session.application.completion.openbench_context_service import (
    CompletionOpenBenchContextService,
)
from shogiarena._core.contexts.game_session.application.completion.runtime_context_service import (
    CompletionRuntimeContextService,
)
from shogiarena._core.contexts.game_session.application.summary.runtime_context import (
    SummaryRuntimeActionRefs,
    SummaryRuntimeBuildRequest,
    SummaryRuntimeDependencies,
    SummaryRuntimeStateRefs,
)
from shogiarena._core.contexts.game_session.application.summary.runtime_context_service import (
    TournamentSummaryRuntimeContextService,
)
from shogiarena._core.contexts.tournament.adapters.runner_runtime_context_builders import (
    build_tournament_completion_runtime_context,
)
from shogiarena._core.shared.kernel.json_types import JsonObject


class _StopControllerStub:
    def request_stop(self, *, reason: str | None = None) -> None:
        del reason


async def _noop_async() -> None:
    return None


def _noop() -> None:
    return None


class _SummaryApiServerStub:
    def __init__(self) -> None:
        self.snapshots: list[tuple[dict[str, object], str]] = []
        self.updates: list[tuple[dict[str, object], str]] = []

    def broadcast_games_snapshot(self, snapshot: dict[str, object], *, event_type: str = "bulk") -> None:
        self.snapshots.append((snapshot, event_type))

    def broadcast_summary_update(self, payload: dict[str, object], *, source: str) -> None:
        self.updates.append((payload, source))


class _SummaryRecordWriterStub:
    def get_records_summary(self) -> dict[str, int]:
        return {"psv": 2}


class _OpenBenchClientStub:
    def __init__(self, *, is_strict: bool) -> None:
        self.is_strict = is_strict


def test_build_completion_runtime_context_shares_completed_summaries_mapping() -> None:
    completed_summaries: dict[str, JsonObject] = {
        "g0": {
            "game_result": "BLACK_WIN",
            "total_plies": 123,
            "start_time": "2026-03-06T00:00:00+00:00",
            "end_time": "2026-03-06T00:10:00+00:00",
        }
    }

    context = build_tournament_completion_runtime_context(
        completion_runtime_context_service=CompletionRuntimeContextService(),
        completion_openbench_context_service=CompletionOpenBenchContextService(),
        completed_game_summaries=completed_summaries,
        config=SimpleNamespace(records_output=None, experiment_name=None),
        summary_source="tournament",
        is_generate_run=False,
        db_service=None,
        record_writer=None,
        rating_service=None,
        completed_game_ids=set(),
        sprt_service=None,
        sprt_pair=None,
        sprt_min_games=0,
        stop_controller=_StopControllerStub(),
        is_dashboard_enabled=True,
        total_games=2,
        save_run_state_fn=_noop,
        openbench_client=None,
        sync_after_game_fn=_noop_async,
    )

    assert context.state.completed_game_summaries is completed_summaries

    context.state.completed_game_summaries["g1"] = {
        "game_result": "DRAW_BY_REPETITION",
        "total_plies": 85,
        "start_time": "2026-03-06T00:20:00+00:00",
        "end_time": "2026-03-06T00:28:00+00:00",
    }

    assert "g1" in completed_summaries


def test_build_summary_runtime_context_groups_state_and_adapts_dependencies(tmp_path) -> None:
    completed_game_ids = {"g0"}
    cancelled_game_ids = {"g9"}
    schedule = [SimpleNamespace(game_id="g0")]
    api_server = _SummaryApiServerStub()
    record_writer = _SummaryRecordWriterStub()

    context = TournamentSummaryRuntimeContextService().build_runtime_context(
        request=SummaryRuntimeBuildRequest(
            run_dir=tmp_path,
            config=SimpleNamespace(
                engines=[SimpleNamespace(name="engine-a"), SimpleNamespace(name="engine-b")],
                rules=SimpleNamespace(initial_positions=SimpleNamespace(flip_policy="balanced")),
                rating=SimpleNamespace(initial=1500),
                generate=None,
                records_output=None,
            ),
            engine_metadata=[{"name": "engine-a"}],
            engine_time_controls=({"engine-a": "10+0"}, "10+0"),
            summary_source="tournament",
            is_dashboard_enabled=True,
        ),
        state=SummaryRuntimeStateRefs(
            cancelled_game_ids=cancelled_game_ids,
            game_schedule=schedule,
            completed_game_ids=completed_game_ids,
            original_total_games=4,
        ),
        dependencies=SummaryRuntimeDependencies(
            db_service=None,
            api_server=api_server,
            record_writer=record_writer,
            sprt_service=None,
            openbench_client=_OpenBenchClientStub(is_strict=True),
        ),
        actions=SummaryRuntimeActionRefs(
            engine_instance_defaults=lambda: {"engine-a": "inst-1"},
            resolve_tournament_type=lambda: "round_robin",
            build_rules_payload=lambda: {"initial_positions": {"flip_policy": "balanced"}},
            build_sprt_payload=lambda: {"elo0": 0.0},
            is_generate_run=lambda: False,
            get_schedule_snapshot=_noop_async,
            flush_openbench=_noop_async,
            save_run_state=lambda _is_finished: None,
            update_dashboard=_noop_async,
        ),
    )

    assert context.request.summary_source == "tournament"
    assert context.state.completed_game_ids is completed_game_ids
    assert context.state.cancelled_game_ids is cancelled_game_ids
    assert context.dependencies.is_openbench_strict_mode is True

    context.dependencies.api_server.broadcast_games_snapshot({"games": []})
    context.dependencies.api_server.broadcast_summary_update({"ok": True}, source="tournament")
    assert api_server.snapshots == [({"games": []}, "bulk")]
    assert api_server.updates == [({"ok": True}, "tournament")]
    assert context.dependencies.record_writer.get_records_summary() == {"psv": 2}
