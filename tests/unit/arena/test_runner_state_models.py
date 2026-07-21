from __future__ import annotations

import json
import logging
from datetime import UTC
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
import rsshogi.record

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
from shogiarena._core.contexts.tournament.application.session.state_store import TournamentSessionStateStore
from shogiarena._core.contexts.tournament.domain.tournament_models import GameSpec
from shogiarena._core.shared.kernel.game_results import GameResult
from shogiarena._core.shared.kernel.service_ports import DatabaseServicePort

_STARTPOS = "lnsgkgsnl/1r5b1/ppppppppp/9/9/9/PPPPPPPPP/1B5R1/LNSGKGSNL b - 1"


def _sample_game_spec() -> GameSpec:
    return GameSpec(black_engine="A", white_engine="B", initial_sfen="startpos", game_id="g1")


def _sample_record(game_id: str = "g1") -> rsshogi.record.Record:
    return rsshogi.record.Record.from_dict(
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
async def test_tournament_prepare_domain_defers_resume_setup_until_services_exist(tmp_path: Path) -> None:
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

    assert calls == 0
    assert runner._state.sealed_schedule_hash == "current-schedule"
    assert runner._state.sealed_resume_hash == "current-resume"


def test_tournament_cleanup_existing_run_removes_default_records_dir(tmp_path: Path) -> None:
    runner = object.__new__(TournamentRunner)
    runner.run_dir = tmp_path
    runner.config = SimpleNamespace(records_output=None, openbench=None)

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
        def load_record(self, *, game_id: int | None = None, game_name: str | None = None) -> rsshogi.record.Record:
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
            record: rsshogi.record.Record,
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


@pytest.mark.asyncio
async def test_tournament_stop_services_closes_db_and_writer_when_openbench_stop_fails(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class _OpenBenchStub:
        async def stop(self) -> None:
            raise RuntimeError("openbench stop failed")

    class _CloseStub:
        def __init__(self) -> None:
            self.close_calls = 0

        def close(self) -> None:
            self.close_calls += 1

    db_service = _CloseStub()
    record_writer = _CloseStub()
    runner = object.__new__(TournamentRunner)
    runner._openbench = _OpenBenchStub()
    runner._state = TournamentRunnerState(db_service=cast(Any, db_service))
    runner._record_writer = record_writer

    with caplog.at_level(logging.WARNING):
        await runner._stop_additional_services()

    assert db_service.close_calls == 1
    assert record_writer.close_calls == 1
    assert runner._state.db_service is None
    assert runner._record_writer is None
    assert "Failed to stop OpenBench service" in caplog.text


@pytest.mark.asyncio
async def test_tournament_init_services_cleans_db_before_record_writer_on_openbench_init_failure(
    tmp_path: Path,
) -> None:
    class _OpenBenchStub:
        def __init__(self) -> None:
            self.stop_calls = 0

        async def init(self, **_kwargs: object) -> None:
            raise RuntimeError("openbench init failed")

        async def stop(self) -> None:
            self.stop_calls += 1

    class _DbStub:
        def __init__(self) -> None:
            self.close_calls = 0

        def get_games_with_players(self, *, game_type: str) -> list[object]:
            assert game_type == "arena"
            return []

        def close(self) -> None:
            self.close_calls += 1

    class _WriterStub:
        def __init__(self) -> None:
            self.close_calls = 0

        def close(self) -> None:
            self.close_calls += 1

    db_service = _DbStub()
    record_writer = _WriterStub()
    openbench = _OpenBenchStub()
    runner = object.__new__(TournamentRunner)
    runner.run_dir = tmp_path
    runner.config = SimpleNamespace(
        rating=SimpleNamespace(initial=1500.0, k_factor=16.0),
        sprt=None,
    )
    runner._state = TournamentRunnerState(db_service=cast(Any, db_service))
    runner._record_writer = None
    runner._openbench = openbench
    runner._session_manager = SimpleNamespace(stop_controller=object())
    runner._is_generate_run = lambda: False
    runner._ensure_db_service = lambda: db_service
    runner._create_record_writer = lambda: record_writer
    runner._backfill_records_output = lambda: None

    with pytest.raises(RuntimeError, match="openbench init failed"):
        await runner.init_services()

    assert openbench.stop_calls == 1
    assert db_service.close_calls == 1
    assert record_writer.close_calls == 0
    assert runner._state.db_service is None
    assert runner._record_writer is None


@pytest.mark.asyncio
async def test_tournament_init_services_restores_after_sprt_and_openbench_initialization(tmp_path: Path) -> None:
    events: list[str] = []

    class _OpenBenchStub:
        async def init(self, **_kwargs: object) -> None:
            events.append("openbench-init")

    class _DbStub:
        def get_games_with_players(self, *, game_type: str) -> list[object]:
            assert game_type == "arena"
            return []

    runner = object.__new__(TournamentRunner)
    runner.run_dir = tmp_path
    runner.config = SimpleNamespace(
        rating=SimpleNamespace(initial=1500.0, k_factor=16.0),
        sprt=SimpleNamespace(
            model="gsprt-trinomial-v1",
            elo0=0.0,
            elo1=5.0,
            alpha=0.05,
            beta=0.05,
            min_games=0,
        ),
        engines=[SimpleNamespace(name="Tested"), SimpleNamespace(name="Baseline")],
    )
    runner._state = TournamentRunnerState(db_service=cast(Any, _DbStub()))
    runner._record_writer = None
    runner._openbench = _OpenBenchStub()
    runner._session_manager = SimpleNamespace(stop_controller=object())
    runner._is_generate_run = lambda: False
    runner._ensure_db_service = lambda: runner._state.db_service
    runner._create_record_writer = lambda: None
    runner._backfill_records_output = lambda: events.append("records-backfill")

    async def _try_setup_tournament() -> bool:
        assert runner._state.sprt is not None
        assert events == ["openbench-init"]
        events.append("resume-restore")
        return True

    runner._try_setup_tournament = _try_setup_tournament

    await runner.init_services()

    assert events == ["openbench-init", "resume-restore", "records-backfill"]


@pytest.mark.asyncio
async def test_tournament_init_services_propagates_resume_rejection_before_record_mutation(tmp_path: Path) -> None:
    events: list[str] = []

    class _OpenBenchStub:
        async def init(self, **_kwargs: object) -> None:
            events.append("openbench-init")

        async def stop(self) -> None:
            events.append("openbench-stop")

    class _DbStub:
        def close(self) -> None:
            events.append("db-close")

    runner = object.__new__(TournamentRunner)
    runner.run_dir = tmp_path
    runner.config = SimpleNamespace(
        rating=SimpleNamespace(initial=1500.0, k_factor=16.0),
        sprt=None,
    )
    runner._state = TournamentRunnerState(db_service=cast(Any, _DbStub()))
    runner._record_writer = None
    runner._openbench = _OpenBenchStub()
    runner._session_manager = SimpleNamespace(stop_controller=object())
    runner._ensure_db_service = lambda: runner._state.db_service
    runner._create_record_writer = lambda: events.append("record-writer-created")

    async def _reject_resume() -> bool:
        raise RuntimeError("Configuration changed, cannot resume. Use --no-resume to start a fresh run.")

    runner._try_setup_tournament = _reject_resume

    with pytest.raises(RuntimeError, match="cannot resume.*no-resume"):
        await runner.init_services()

    assert events == ["openbench-init", "openbench-stop", "db-close"]
    assert runner._record_writer is None


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


def test_tournament_cleanup_refuses_to_discard_submitted_openbench_results(tmp_path: Path) -> None:
    # --no-resume の cleanup は state.json を消す前に OpenBench 送信実績を検査する。
    runner = object.__new__(TournamentRunner)
    runner.run_dir = tmp_path
    runner.config = SimpleNamespace(records_output=None, openbench=SimpleNamespace(is_enabled=True))
    runner._state_store = TournamentSessionStateStore()

    (tmp_path / "state.json").write_text(
        json.dumps(
            {
                "schedule_hash": "schedule-hash",
                "resume_hash": "resume-hash",
                "openbench_state": {
                    "submitted": {"wins": 5, "losses": 4, "draws": 1},
                    "claimed_test_id": 3,
                },
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "game.db").write_text("", encoding="utf-8")

    with pytest.raises(RuntimeError, match="double-count"):
        runner._cleanup_existing_run()

    # 検査に失敗した以上、artifact は 1 つも消えていないこと。
    assert (tmp_path / "state.json").exists()
    assert (tmp_path / "game.db").exists()
