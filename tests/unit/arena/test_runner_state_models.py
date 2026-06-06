from __future__ import annotations

from datetime import UTC
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
import rshogi.record

from shogiarena._core.contexts.game_session.application.session.run_metadata_persistence_service import (
    RunManifestSealError,
)
from shogiarena._core.contexts.game_session.ports.session_lifecycle_ports import RunOptions
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
from shogiarena._core.shared.kernel.game_results import GameResult
from shogiarena._core.shared.kernel.service_ports import DatabaseServicePort

_STARTPOS = "lnsgkgsnl/1r5b1/ppppppppp/9/9/9/PPPPPPPPP/1B5R1/LNSGKGSNL b - 1"


def _sample_game_spec() -> GameSpec:
    return GameSpec(black_engine="A", white_engine="B", initial_sfen="startpos", game_id="g1")


def _sample_record(game_id: str = "g1") -> rshogi.record.GameRecord:
    return rshogi.record.GameRecord.from_dict(
        {
            "metadata": {
                "black_player": "A",
                "white_player": "B",
                "attributes": {"game_name": game_id, "game_type": "generate"},
            },
            "init_position_sfen": _STARTPOS,
            "moves": [],
            "result": {"result": GameResult.DRAW_BY_REPETITION.name, "ply_count": 0},
        }
    )


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


@pytest.mark.asyncio
async def test_tournament_prepare_domain_routes_seal_mismatch_to_resume_setup(tmp_path: Path) -> None:
    class _ConfigStub:
        def model_dump(self, *, mode: str) -> dict[str, object]:
            assert mode == "json"
            return {"experiment_name": "changed"}

    class _RunMetadataStub:
        def seal_provenance_manifest(self, **_kwargs: object) -> object:
            raise RunManifestSealError("manifest inputs_hash changed")

        def build_sealed_hashes(self, **_kwargs: object) -> object:
            return SimpleNamespace(schedule_hash="current-schedule", resume_hash="current-resume")

    runner = object.__new__(TournamentRunner)
    runner.run_dir = tmp_path
    runner.config = _ConfigStub()
    runner._state = TournamentRunnerState()
    runner._run_options = RunOptions(should_skip_resume=False)
    runner._run_metadata_service = _RunMetadataStub()
    runner._frozen_run_config_payload = {"experiment_name": "original"}
    runner._seal_artifact_engine_configs = lambda: None
    runner._ensure_db_service = lambda: None
    (tmp_path / "state.json").write_text("{}", encoding="utf-8")
    calls = 0

    async def _try_setup_tournament() -> bool:
        nonlocal calls
        calls += 1
        return False

    runner._try_setup_tournament = _try_setup_tournament

    await runner.prepare_domain()

    assert calls == 1
    assert runner._state.sealed_schedule_hash == "current-schedule"
    assert runner._state.sealed_resume_hash == "current-resume"


def test_tournament_cleanup_existing_run_removes_default_records_dir(tmp_path: Path) -> None:
    runner = object.__new__(TournamentRunner)
    runner.run_dir = tmp_path
    runner.config = SimpleNamespace(records_output=None)

    records_dir = tmp_path / "records"
    records_dir.mkdir()
    (records_dir / "records_manifest.json").write_text("{}", encoding="utf-8")

    runner._cleanup_existing_run()

    assert not records_dir.exists()


def test_tournament_records_output_rejects_existing_external_output_for_new_run(tmp_path: Path) -> None:
    runner = object.__new__(TournamentRunner)
    runner.run_dir = tmp_path / "run"
    runner._run_options = RunOptions(should_skip_resume=False)

    output_dir = tmp_path / "shared-records"
    output_dir.mkdir()
    (output_dir / "records_manifest.json").write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="already contains files"):
        runner._validate_records_output_dir(output_dir)


def test_tournament_backfills_missing_records_output_from_game_db(tmp_path: Path) -> None:
    class _DbStub:
        def load_record(self, *, game_id: int | None = None, game_name: str | None = None) -> rshogi.record.GameRecord:
            assert game_id is None
            assert game_name == "g1"
            return _sample_record(str(game_name))

    class _WriterStub:
        def __init__(self) -> None:
            self.appended: list[tuple[str | None, str | None]] = []

        def written_game_ids(self) -> set[str]:
            return set()

        def append_record(
            self,
            record: rshogi.record.GameRecord,
            *,
            game_id: str | None = None,
            game_type: str | None = None,
        ) -> None:
            del record
            self.appended.append((game_id, game_type))

    writer = _WriterStub()
    runner = object.__new__(TournamentRunner)
    runner.run_dir = tmp_path
    runner._state = TournamentRunnerState(completed_game_ids={"g1"}, db_service=cast(Any, _DbStub()))
    runner._record_writer = writer
    runner._mode_strategy = SimpleNamespace(is_generate_run=lambda: True)

    runner._backfill_records_output()

    assert writer.appended == [("g1", "generate")]


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
