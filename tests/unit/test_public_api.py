from __future__ import annotations

import dataclasses
import importlib
import inspect
from collections.abc import Callable
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, cast, get_args, get_type_hints

import pytest

import shogiarena.cli
import shogiarena.composition
import shogiarena.engine
import shogiarena.tournament
from shogiarena._core.contexts.game_session.adapters.results.run_result_models import (
    TournamentRunResultBuilder,
)
from shogiarena._core.contexts.game_session.domain.summary_models import TournamentResults
from shogiarena.engine import UsiEngineConfig
from shogiarena.tournament import GameSpec, TournamentRunResult

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

# 位置引数での構築を許したまま v1 に入れると、field 追加が破壊的変更になる。
# 例外は provisional 面の単純な構築子に限り、理由をここに残す。
_POSITIONAL_CONSTRUCTION_EXEMPTIONS = {
    # provisional: run ディレクトリ 1 つだけを取る構築子で、field 追加の予定がない。
    "FilesystemRunStorage",
    # provisional: composition root の内部組み立て結果で、利用者は build_default_root() 経由で得る。
    "DefaultRoot",
}


def _contains_any(annotation: object) -> bool:
    return annotation is Any or any(_contains_any(argument) for argument in get_args(annotation))


def _assert_no_any_annotation(function: object) -> None:
    hints = get_type_hints(function)
    assert hints
    for name, hint in hints.items():
        assert not _contains_any(hint), f"{function!r} exposes Any inside {name}"


def _public_dataclasses(module: ModuleType) -> list[tuple[str, type[object]]]:
    found: list[tuple[str, type[object]]] = []
    for name in module.__all__:
        exported = getattr(module, name)
        if isinstance(exported, type) and dataclasses.is_dataclass(exported):
            found.append((name, exported))
    return found


def _assert_keyword_only(model: type[object]) -> None:
    signature = inspect.signature(model)
    assert signature.parameters
    assert all(parameter.kind is inspect.Parameter.KEYWORD_ONLY for parameter in signature.parameters.values())


def test_public_cli_and_composition_modules_export_expected_surface() -> None:
    assert set(shogiarena.cli.__all__) == {"CliArgumentError", "CliError", "build_parser", "main"}
    assert set(shogiarena.composition.__all__) == {"DefaultRoot", "build_default_root"}


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
    "module_name",
    [
        "shogiarena.contexts",
        "shogiarena.platform",
        "shogiarena.interfaces",
        "shogiarena.shared",
    ],
)
def test_legacy_top_level_internal_modules_are_not_importable(module_name: str) -> None:
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(module_name)


def test_public_engine_factory_return_annotations_are_session_contract() -> None:
    signature = inspect.signature(shogiarena.engine.create_engine)
    mapping_signature = inspect.signature(shogiarena.engine.create_engine_from_mapping)
    assert signature.return_annotation == "UsiEngineSession"
    assert mapping_signature.return_annotation == "UsiEngineSession"


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


@pytest.mark.parametrize("module", [shogiarena.engine, shogiarena.tournament], ids=lambda m: m.__name__)
def test_every_exported_dataclass_is_keyword_only(module: ModuleType) -> None:
    exported = _public_dataclasses(module)
    assert exported, f"{module.__name__} exports no dataclass; the ratchet would silently pass"
    for name, model in exported:
        if name in _POSITIONAL_CONSTRUCTION_EXEMPTIONS:
            continue
        _assert_keyword_only(model)


def test_stable_result_field_types_are_importable_from_the_same_module() -> None:
    hints = get_type_hints(TournamentRunResult)
    exported = set(shogiarena.tournament.__all__)
    for field in dataclasses.fields(TournamentRunResult):
        hint = hints[field.name]
        for referenced in (hint, *getattr(hint, "__args__", ())):
            name = getattr(referenced, "__name__", None)
            if name is None or referenced.__module__.startswith(("builtins", "collections", "datetime", "pathlib")):
                continue
            assert name in exported, f"TournamentRunResult.{field.name} references {name} outside shogiarena.tournament"


def test_usi_engine_config_constructor_hides_private_provenance() -> None:
    signature = inspect.signature(UsiEngineConfig)
    assert "_raw_engine_path" not in signature.parameters
    assert "_raw_working_directory" not in signature.parameters
    assert "_raw_options" not in signature.parameters
    constructor = cast(Callable[..., UsiEngineConfig], UsiEngineConfig)
    with pytest.raises(TypeError):
        constructor(name="engine", engine_path="engine.exe", _raw_engine_path="hidden")


def test_usi_engine_config_preserves_provenance_across_public_operations(tmp_path: Path) -> None:
    first_output_dir = tmp_path / "first-output"
    second_output_dir = tmp_path / "second-output"
    engine_dir = tmp_path / "engines"
    config = UsiEngineConfig.from_mapping(
        {
            "name": "engine",
            "engine_path": "{engine_dir}/engine.exe",
            "working_directory": "{output_dir}/work",
            "options": {"EvalDir": "{output_dir}/eval"},
        }
    )

    first = config.resolve_paths(output_dir=first_output_dir, engine_dir=engine_dir)
    overridden = first.with_overrides(options={"EvalDir": "{output_dir}/book"}, output_dir=first_output_dir)
    second = overridden.resolve_paths(output_dir=second_output_dir, engine_dir=engine_dir)

    assert second.engine_path == str((engine_dir / "engine.exe").resolve())
    assert second.working_directory == str((second_output_dir / "work").resolve())
    assert second.options["EvalDir"] == str((second_output_dir / "book").resolve())


def test_direct_usi_engine_config_construction_initializes_provenance(tmp_path: Path) -> None:
    config = UsiEngineConfig(
        name="engine",
        engine_path="{engine_dir}/engine.exe",
        options={"EvalDir": "{output_dir}/eval"},
    )
    resolved = config.resolve_paths(output_dir=tmp_path / "output", engine_dir=tmp_path / "engines")

    assert resolved.engine_path == str((tmp_path / "engines" / "engine.exe").resolve())
    assert resolved.options["EvalDir"] == str((tmp_path / "output" / "eval").resolve())


def test_game_spec_rejects_positional_construction() -> None:
    constructor = cast(Callable[..., GameSpec], GameSpec)
    with pytest.raises(TypeError):
        constructor("black", "white", "startpos", "g0001-test")


def test_resolve_instance_pool_injects_default_local_or_preserves_explicit() -> None:
    from shogiarena._core.contexts.instances.application.instance_pool import InstancePool
    from shogiarena.engine import _resolve_instance_pool

    pool = _resolve_instance_pool(None, None)
    assert isinstance(pool, InstancePool)
    assert pool.ensure_local_instance().is_local

    explicit = InstancePool()
    assert _resolve_instance_pool(None, explicit) is explicit
    assert _resolve_instance_pool("worker-1", None) is None


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


def test_load_tournament_config_from_mapping_uses_runtime_request(tmp_path: Path) -> None:
    captured: dict[str, object] = {}
    expected = object()

    class _Runtime:
        def build_run_config(self, payload: object, *, request: object) -> object:
            captured["payload"] = payload
            captured["request"] = request
            return expected

    root = SimpleNamespace(tournament_runtime=_Runtime())
    source_path = tmp_path / "config.yaml"
    payload = {"engines": [], "rules": {}}

    result = shogiarena.tournament.load_tournament_config(
        payload,
        base_dir=tmp_path,
        source_path=source_path,
        root=root,
    )

    assert result is expected
    request = captured["request"]
    assert isinstance(request, shogiarena.tournament.TournamentRunConfigBuildRequest)
    assert request.base_dir == tmp_path.resolve()
    assert request.source_path == source_path.resolve()
    assert captured["payload"] == payload


@pytest.mark.asyncio
async def test_run_tournament_builds_runner_and_executes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    config = object()
    storage = object()
    result = object()
    root = object()
    captured: dict[str, object] = {}

    class _Runner:
        async def run(self) -> object:
            return result

    def _fake_load_tournament_config(config_source: object, *, root: object) -> object:
        captured["config_source"] = config_source
        captured["root"] = root
        return config

    def _fake_create_run_storage(run_dir: str | Path) -> object:
        captured["run_dir"] = Path(run_dir)
        return storage

    def _fake_build_tournament_runner(
        built_config: object,
        *,
        storage: object,
        instance_pool: object | None,
        progress_reporter: object | None,
        is_dashboard_enabled: bool | None,
        should_skip_resume: bool,
        root: object,
    ) -> _Runner:
        captured["built_config"] = built_config
        captured["storage"] = storage
        captured["instance_pool"] = instance_pool
        captured["progress_reporter"] = progress_reporter
        captured["is_dashboard_enabled"] = is_dashboard_enabled
        captured["should_skip_resume"] = should_skip_resume
        captured["runner_root"] = root
        return _Runner()

    monkeypatch.setattr(shogiarena.tournament, "load_tournament_config", _fake_load_tournament_config)
    monkeypatch.setattr(shogiarena.tournament, "create_run_storage", _fake_create_run_storage)
    monkeypatch.setattr(shogiarena.tournament, "build_tournament_runner", _fake_build_tournament_runner)

    actual = await shogiarena.tournament.run_tournament(
        {"engines": [], "rules": {}},
        run_dir=tmp_path / "run",
        is_dashboard_enabled=False,
        should_skip_resume=True,
        root=root,
    )

    assert actual is result
    assert captured["config_source"] == {"engines": [], "rules": {}}
    assert captured["root"] is root
    assert captured["run_dir"] == (tmp_path / "run")
    assert captured["built_config"] is config
    assert captured["storage"] is storage
    assert captured["is_dashboard_enabled"] is False
    assert captured["should_skip_resume"] is True
    assert captured["runner_root"] is root
