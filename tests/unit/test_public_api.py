from __future__ import annotations

import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest

import shogiarena.cli
import shogiarena.composition
import shogiarena.engine
import shogiarena.tournament


def test_public_cli_module_exports_expected_surface() -> None:
    assert set(shogiarena.cli.__all__) == {"CliArgumentError", "CliError", "build_parser", "main"}


def test_public_composition_module_exports_expected_surface() -> None:
    assert set(shogiarena.composition.__all__) == {"DefaultRoot", "build_default_root"}


def test_public_engine_module_exports_expected_symbols() -> None:
    exported = set(shogiarena.engine.__all__)
    assert "AsyncUsiEngine" in exported
    assert "UsiEngineConfig" in exported
    assert "UsiThinkRequest" in exported
    assert "create_engine" in exported
    assert "create_engine_from_mapping" in exported


def test_public_tournament_module_exports_expected_symbols() -> None:
    exported = set(shogiarena.tournament.__all__)
    assert "TournamentRunConfig" in exported
    assert "TournamentRunner" in exported
    assert "create_run_storage" in exported
    assert "build_tournament_runner" in exported
    assert "load_tournament_config" in exported
    assert "run_tournament" in exported


@pytest.mark.parametrize(
    ("module_name",),
    [
        ("shogiarena.contexts",),
        ("shogiarena.platform",),
        ("shogiarena.interfaces",),
        ("shogiarena.shared",),
    ],
)
def test_legacy_top_level_internal_modules_are_not_importable(module_name: str) -> None:
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(module_name)


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

    async def _run() -> object:
        return result

    class _Runner:
        async def run(self) -> object:
            return await _run()

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
