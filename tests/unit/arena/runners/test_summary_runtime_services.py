from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from shogiarena._core.contexts.game_session.application.summary.finalize_service import (
    TournamentSummaryFinalizeService,
)
from shogiarena._core.contexts.game_session.application.summary.runtime_context import (
    SummaryRuntimeActionRefs,
    SummaryRuntimeBuildRequest,
    SummaryRuntimeDependencies,
    SummaryRuntimeStateRefs,
    TournamentSummaryRuntimeContext,
)
from shogiarena._core.contexts.game_session.application.summary.seed_payload_service import (
    TournamentSummarySeedPayloadService,
)
from shogiarena._core.contexts.game_session.domain.summary_models import TournamentResults


class _PayloadStub:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def to_json_object(self) -> dict[str, object]:
        return dict(self._payload)


class _ArtifactServiceStub:
    def __init__(self) -> None:
        self.results_writes: list[tuple[Path, dict[str, object]]] = []
        self.summary_writes: list[tuple[Path, dict[str, object], str]] = []

    def write_tournament_results(self, run_dir: Path, payload: dict[str, object]) -> None:
        self.results_writes.append((run_dir, payload))

    def write_summary_btd(self, run_dir: Path, payload: dict[str, object], *, log_context: str) -> None:
        self.summary_writes.append((run_dir, payload, log_context))


class _ReportingServiceStub:
    def __init__(self) -> None:
        self.logged_results: list[TournamentResults] = []
        self.logged_btd: list[tuple[TournamentResults, object, list[str]]] = []

    def log_tournament_results(self, results: TournamentResults) -> None:
        self.logged_results.append(results)

    def estimate_btd(self, *, games: list[object], anchor_name: str | None, engine_names: list[str]) -> object:
        del games, anchor_name, engine_names
        return SimpleNamespace(anchor="engine-a")

    def log_btd_estimates(self, *, results: TournamentResults, btd: object, engine_names: list[str]) -> None:
        self.logged_btd.append((results, btd, engine_names))


class _FinalPayloadServiceStub:
    def build_results_payload(self, results: TournamentResults) -> _PayloadStub:
        return _PayloadStub({"completed": results.completed_games_count})

    def build_final_btd_payload(self, *, results: TournamentResults, btd: object) -> _PayloadStub:
        del btd
        return _PayloadStub({"total": results.total_games})


class _DbServiceStub:
    def get_games_with_players(self, *, game_type: str) -> list[object]:
        assert game_type == "arena"
        return []


def _build_runtime(run_dir: Path) -> TournamentSummaryRuntimeContext:
    return TournamentSummaryRuntimeContext(
        request=SummaryRuntimeBuildRequest(
            run_dir=run_dir,
            config=SimpleNamespace(
                engines=[SimpleNamespace(name="engine-a"), SimpleNamespace(name="engine-b")],
                rules=SimpleNamespace(initial_positions=SimpleNamespace(flip_policy="balanced")),
                rating=SimpleNamespace(initial=1600),
                generate=None,
                records_output=None,
            ),
            engine_metadata=[{"name": "engine-a"}],
            engine_time_controls=({"engine-a": "10+0"}, "10+0"),
            summary_source="tournament",
            is_dashboard_enabled=True,
        ),
        state=SummaryRuntimeStateRefs(
            cancelled_game_ids=set(),
            game_schedule=[],
            completed_game_ids={"g1"},
            original_total_games=2,
        ),
        dependencies=SummaryRuntimeDependencies(
            db_service=_DbServiceStub(),
            api_server=None,
            record_writer=None,
            sprt_service=None,
            is_openbench_strict_mode=False,
        ),
        actions=SummaryRuntimeActionRefs(
            engine_instance_defaults=lambda: {"engine-a": "inst-a"},
            resolve_tournament_type=lambda: "round_robin",
            build_rules_payload=lambda: {"initial_positions": {"flip_policy": "balanced"}},
            build_sprt_payload=lambda: {},
            is_generate_run=lambda: False,
            get_schedule_snapshot=_noop_async,
            flush_openbench=_noop_async,
            save_run_state=lambda _is_finished: None,
            update_dashboard=_noop_async,
        ),
    )


async def _noop_async() -> None:
    return None


def test_seed_payload_service_reads_grouped_runtime_context(tmp_path: Path) -> None:
    payload = TournamentSummarySeedPayloadService().build(_build_runtime(tmp_path)).to_json_object()

    assert payload["tournament_type"] == "round_robin"
    assert payload["mode"] == "tournament"
    assert payload["run_dir"] == str(tmp_path)
    assert payload["engine_instances"] == {"engine-a": "inst-a"}


@pytest.mark.asyncio
async def test_finalize_service_uses_grouped_runtime_actions(tmp_path: Path) -> None:
    flush_calls: list[str] = []
    save_calls: list[bool] = []
    dashboard_calls: list[str] = []
    runtime = _build_runtime(tmp_path)
    runtime.actions = SummaryRuntimeActionRefs(
        engine_instance_defaults=runtime.actions.engine_instance_defaults,
        resolve_tournament_type=runtime.actions.resolve_tournament_type,
        build_rules_payload=runtime.actions.build_rules_payload,
        build_sprt_payload=runtime.actions.build_sprt_payload,
        is_generate_run=runtime.actions.is_generate_run,
        get_schedule_snapshot=runtime.actions.get_schedule_snapshot,
        flush_openbench=lambda: _record_async(flush_calls, "flush"),
        save_run_state=lambda is_finished: save_calls.append(is_finished),
        update_dashboard=lambda: _record_async(dashboard_calls, "dashboard"),
    )
    artifact_service = _ArtifactServiceStub()
    reporting_service = _ReportingServiceStub()
    service = TournamentSummaryFinalizeService(
        payload_service=_FinalPayloadServiceStub(),
        artifact_service=artifact_service,
        reporting_service=reporting_service,
    )
    results = TournamentResults(
        engine_stats={},
        pair_results={},
        completed_games=["g1"],
        total_games=2,
        completed_games_count=1,
        cancelled_games_count=0,
    )

    await service.finalize(runtime, results=results)

    assert flush_calls == ["flush"]
    assert save_calls == [True]
    assert dashboard_calls == ["dashboard"]
    assert artifact_service.results_writes == [(tmp_path, {"completed": 1})]
    assert artifact_service.summary_writes == [(tmp_path, {"total": 2}, "final")]
    assert (tmp_path / "completed.flag").exists()
    assert reporting_service.logged_results == [results]


async def _record_async(calls: list[str], value: str) -> None:
    calls.append(value)
