from __future__ import annotations

from datetime import UTC
from typing import cast

from shogiarena._core.contexts.spsa.adapters.runner import SpsaRunner
from shogiarena._core.contexts.spsa.application.runner_state import SpsaRunnerState
from shogiarena._core.contexts.spsa.domain.spsa_models import ParamEntry
from shogiarena._core.contexts.spsa.ports.spsa_store_port import (
    SpsaAnalysisPort,
    SpsaGameListingPort,
    SpsaStorePort,
    SpsaSummaryServicePort,
    SpsaUpdateQueryPort,
)
from shogiarena._core.contexts.tournament.adapters.runner import TournamentRunner
from shogiarena._core.contexts.tournament.application.runner_state import TournamentRunnerState
from shogiarena._core.contexts.tournament.domain.tournament_models import GameSpec
from shogiarena._core.shared.kernel.service_ports import DatabaseServicePort


def _sample_game_spec() -> GameSpec:
    return GameSpec(black_engine="A", white_engine="B", initial_sfen="startpos", game_id="g1")


def _sample_param() -> ParamEntry:
    return ParamEntry(
        name="tempo",
        type="float",
        value=1.0,
        min=0.0,
        max=2.0,
        step=0.1,
        delta=0.2,
        comment="sample",
        is_not_used=False,
    )


def test_tournament_runner_state_defaults_are_isolated() -> None:
    left = TournamentRunnerState()
    right = TournamentRunnerState()

    left.game_schedule.append(_sample_game_spec())
    left.completed_game_ids.add("g1")
    left.cancelled_specs["g1"] = _sample_game_spec()
    left.schedule_wait_event.set()

    assert right.game_schedule == []
    assert right.completed_game_ids == set()
    assert right.cancelled_specs == {}
    assert not right.schedule_wait_event.is_set()
    assert left.reschedule_lock is not right.reschedule_lock
    assert left.completion_lock is not right.completion_lock


def test_tournament_runner_state_directly_accessible() -> None:
    runner = object.__new__(TournamentRunner)
    runner._state = TournamentRunnerState()

    spec = _sample_game_spec()

    runner._state.game_schedule = [spec]
    runner._state.completed_game_ids = {"g1"}
    runner._state.cancelled_game_ids = {"g2"}
    runner._state.original_total_games = 7

    assert runner._state.game_schedule == [spec]
    assert runner._state.completed_game_ids == {"g1"}
    assert runner._state.cancelled_game_ids == {"g2"}
    assert runner._state.original_total_games == 7


def test_spsa_runner_state_defaults_are_isolated_and_typed() -> None:
    left = SpsaRunnerState()
    right = SpsaRunnerState()

    left.params = [_sample_param()]

    assert right.params is None
    assert left.completion_lock is not right.completion_lock
    assert left.session_uuid != right.session_uuid
    assert left.session_started_at.tzinfo is UTC
    assert right.session_started_at.tzinfo is UTC


def test_spsa_runner_state_directly_accessible() -> None:
    runner = object.__new__(SpsaRunner)
    runner._state = SpsaRunnerState()

    params = [_sample_param()]
    db_service = cast(DatabaseServicePort, object())
    store = cast(SpsaStorePort, object())
    summary_service = cast(SpsaSummaryServicePort, object())
    update_query_service = cast(SpsaUpdateQueryPort, object())
    game_listing_service = cast(SpsaGameListingPort, object())
    analysis_service = cast(SpsaAnalysisPort, object())

    runner._state.db_service = db_service
    runner._state.params = params
    runner._state.sfens = ["startpos"]
    runner._state.update_items = [1, 2]
    runner._state.spsa_store = store
    runner._state.spsa_summary_service = summary_service
    runner._state.spsa_update_query_service = update_query_service
    runner._state.spsa_game_listing_service = game_listing_service
    runner._state.spsa_analysis_service = analysis_service

    assert runner._state.db_service is db_service
    assert runner._state.params == params
    assert runner._state.sfens == ["startpos"]
    assert runner._state.update_items == [1, 2]
    assert runner._state.spsa_store is store
    assert runner._state.spsa_summary_service is summary_service
    assert runner._state.spsa_update_query_service is update_query_service
    assert runner._state.spsa_game_listing_service is game_listing_service
    assert runner._state.spsa_analysis_service is analysis_service


def test_tournament_runner_get_sprt_status_reads_state_service() -> None:
    class _SprtStub:
        def get_status(self) -> dict[str, str]:
            return {"decision": "accept_h1"}

    runner = object.__new__(TournamentRunner)
    runner._state = TournamentRunnerState()
    runner._state.sprt = cast(object, _SprtStub())

    assert runner.get_sprt_status() == {"decision": "accept_h1"}


def test_spsa_runner_get_sprt_status_is_none() -> None:
    runner = object.__new__(SpsaRunner)
    runner._state = SpsaRunnerState()

    assert runner.get_sprt_status() is None
