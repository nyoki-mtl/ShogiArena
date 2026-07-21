from __future__ import annotations

from pathlib import Path
from typing import Any, get_args, get_type_hints

import pytest

import shogiarena.engine
import shogiarena.tournament
from shogiarena._core.contexts.game_session.adapters.results.run_result_models import (
    TournamentRunResultBuilder,
)
from shogiarena._core.contexts.game_session.domain.summary_models import TournamentResults

EXPECTED_ENGINE_EXPORTS = {
    "AnalysisHandle",
    "AsyncUsiProcessBridgePort",
    "EngineLifecycleEvent",
    "EngineProcessInfo",
    "InstancePool",
    "JsonObject",
    "JsonValue",
    "PonderHandle",
    "PonderHitTimings",
    "UsiAnalyzeItem",
    "UsiAnalyzePosition",
    "UsiAnalyzeResetPolicy",
    "UsiBound",
    "UsiEngineConfig",
    "UsiEngineSession",
    "UsiEngineStartError",
    "UsiEngineState",
    "UsiEvalValue",
    "UsiIoEvent",
    "UsiMateResult",
    "UsiOption",
    "UsiOptionValidationMode",
    "UsiProtocolParser",
    "UsiThinkPV",
    "UsiThinkRequest",
    "UsiThinkResult",
    "create_engine",
    "create_engine_from_mapping",
    "move_from_usi",
}

EXPECTED_TOURNAMENT_EXPORTS = {
    "DefaultRoot",
    "EngineWdlCounts",
    "FilesystemRunStorage",
    "GameSpec",
    "InstancePool",
    "JsonValue",
    "ProgressReporterPort",
    "RunStoragePort",
    "SprtDecision",
    "SprtResult",
    "TournamentResults",
    "TournamentRunConfig",
    "TournamentRunResult",
    "TournamentRunner",
    "build_tournament_runner",
    "create_run_storage",
    "load_tournament_config",
    "run_tournament",
}


def _contains_any(annotation: object) -> bool:
    return annotation is Any or any(_contains_any(argument) for argument in get_args(annotation))


def _assert_no_any_annotation(function: object) -> None:
    hints = get_type_hints(function)
    assert hints
    for name, hint in hints.items():
        assert not _contains_any(hint), f"{function!r} exposes Any inside {name}"


def test_public_facades_export_exact_contract() -> None:
    assert set(shogiarena.engine.__all__) == EXPECTED_ENGINE_EXPORTS
    assert set(shogiarena.tournament.__all__) == EXPECTED_TOURNAMENT_EXPORTS
    for module, names in (
        (shogiarena.engine, EXPECTED_ENGINE_EXPORTS),
        (shogiarena.tournament, EXPECTED_TOURNAMENT_EXPORTS),
    ):
        for name in names:
            assert hasattr(module, name), f"{module.__name__}.{name} is not importable"


def test_removed_concrete_runtime_types_are_not_module_attributes() -> None:
    for name in ("AsyncUsiEngine", "AsyncUsiProcess", "SpawnerBackedUSIBridge"):
        assert not hasattr(shogiarena.engine, name)
    assert not hasattr(shogiarena.tournament, "RunStorage")


@pytest.mark.parametrize(
    "function",
    [
        shogiarena.engine.create_engine,
        shogiarena.engine.create_engine_from_mapping,
        shogiarena.tournament.create_run_storage,
        shogiarena.tournament.build_tournament_runner,
        shogiarena.tournament.load_tournament_config,
        shogiarena.tournament.run_tournament,
        shogiarena.tournament.TournamentRunner.run,
        shogiarena.tournament.TournamentRunner.calculate_results,
        shogiarena.tournament.TournamentRunner.finalize_tournament,
    ],
)
def test_public_facade_signatures_do_not_expose_any(function: object) -> None:
    _assert_no_any_annotation(function)


def test_tournament_result_annotations_are_concrete() -> None:
    facade_hint = get_type_hints(shogiarena.tournament.run_tournament)["return"]
    runner_hint = get_type_hints(shogiarena.tournament.TournamentRunner.run)["return"]
    expected = shogiarena.tournament.TournamentRunResult | None
    assert facade_hint == expected
    assert runner_hint == expected
    assert get_type_hints(shogiarena.tournament.TournamentRunner.calculate_results)["return"] is TournamentResults
    assert get_type_hints(shogiarena.tournament.TournamentRunner.finalize_tournament)["results"] is TournamentResults


def test_production_result_builder_returns_public_result_type(tmp_path: Path) -> None:
    storage = shogiarena.tournament.create_run_storage(tmp_path)
    tournament = TournamentResults(
        engine_stats={},
        pair_results={},
        completed_games=[],
        total_games=0,
        completed_games_count=0,
    )
    result = TournamentRunResultBuilder(
        run_id="contract-test",
        run_dir=tmp_path,
        storage=storage,
    ).build_tournament_run_result(tournament, None)

    assert isinstance(result, shogiarena.tournament.TournamentRunResult)
    assert result.tournament is tournament
