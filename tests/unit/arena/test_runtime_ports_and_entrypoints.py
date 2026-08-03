from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from shogiarena._core.contexts.game_session.adapters.run_storage import FilesystemRunStorage
from shogiarena._core.contexts.game_session.ports.session_runtime import SessionRuntimePort
from shogiarena._core.contexts.spsa.adapters import runtime_adapter as spsa_runtime_adapter_module
from shogiarena._core.contexts.spsa.adapters.runtime_adapter import SpsaRuntimeAdapter
from shogiarena._core.contexts.spsa.application.entrypoints import (
    build_spsa_run_config,
    create_spsa_run_storage,
    preflight_spsa_dry_run,
    run_spsa_session,
    spsa_engine_trace_logger_names,
)
from shogiarena._core.contexts.spsa.ports.dashboard_factory import DashboardSpsaServicesFactory
from shogiarena._core.contexts.spsa.ports.spsa_runtime_port import SpsaRunConfigBuildRequest, SpsaRuntimePort
from shogiarena._core.contexts.tournament.adapters import runtime_adapter as tournament_runtime_adapter_module
from shogiarena._core.contexts.tournament.adapters.runtime_adapter import TournamentRuntimeAdapter
from shogiarena._core.contexts.tournament.application.entrypoints import (
    build_tournament_run_config,
    create_tournament_run_storage,
    run_tournament_session,
)
from shogiarena._core.contexts.tournament.ports.tournament_runtime_port import (
    TournamentRunConfigBuildRequest,
    TournamentRuntimePort,
)
from tests.helpers.run_storage import TempRunStorage


class _RuntimeStub:
    def __init__(self) -> None:
        self.build_calls: list[tuple[dict[str, object], object]] = []
        self.run_calls: list[tuple[object, object, bool, object | None]] = []
        self.storage_calls: list[Path] = []
        self.preflight_calls: list[tuple[object, Path, object | None]] = []

    def build_run_config(self, payload: dict[str, object], *, request: object) -> dict[str, object]:
        self.build_calls.append((payload, request))
        return {"payload": payload, "request": request}

    async def run_session(
        self,
        config: object,
        *,
        storage: object,
        should_skip_resume: bool,
        instance_pool: object | None,
    ) -> None:
        self.run_calls.append((config, storage, should_skip_resume, instance_pool))

    def create_run_storage(self, run_dir: Path) -> str:
        self.storage_calls.append(run_dir)
        return f"storage:{run_dir}"

    def engine_trace_logger_names(self) -> tuple[str, ...]:
        return ("logger.a", "logger.b")

    def preflight_dry_run(self, config: object, *, work_dir: Path, instance_pool: object | None) -> None:
        self.preflight_calls.append((config, work_dir, instance_pool))


class _InstancePoolStub:
    def get_instance(self, _name: str) -> None:
        return None

    def ensure_local_instance(self) -> object:
        return object()


class _DashboardServiceFactoryStub:
    def create_services(self, **_kwargs: object) -> object:
        return SimpleNamespace(
            store=None,
            summary_service=None,
            update_query_service=None,
            game_listing_service=None,
            analysis_service=None,
        )


def test_tournament_build_request_is_frozen() -> None:
    request = TournamentRunConfigBuildRequest(base_dir=Path("/tmp/base"), source_path=Path("/tmp/config.yml"))

    with pytest.raises(FrozenInstanceError):
        request.base_dir = Path("/tmp/other")  # type: ignore[misc]


def test_spsa_build_request_is_frozen() -> None:
    request = SpsaRunConfigBuildRequest(source_path=Path("/tmp/config.yml"))

    with pytest.raises(FrozenInstanceError):
        request.source_path = Path("/tmp/other")  # type: ignore[misc]


@pytest.mark.asyncio
async def test_tournament_entrypoints_forward_typed_request() -> None:
    runtime = _RuntimeStub()
    tournament_runtime = cast(TournamentRuntimePort, runtime)
    payload = {"games": 42}
    base_dir = Path("/tmp/base")
    source_path = Path("/tmp/tournament.yml")

    config = build_tournament_run_config(
        payload,
        base_dir=base_dir,
        source_path=source_path,
        runtime=tournament_runtime,
    )
    storage = create_tournament_run_storage(Path("/tmp/run"), runtime=tournament_runtime)
    await run_tournament_session(
        config,
        storage=storage,
        should_skip_resume=True,
        instance_pool=None,
        runtime=tournament_runtime,
    )

    assert isinstance(runtime, SessionRuntimePort)
    assert len(runtime.build_calls) == 1
    build_payload, request = runtime.build_calls[0]
    assert build_payload == payload
    assert isinstance(request, TournamentRunConfigBuildRequest)
    assert request.base_dir == base_dir
    assert request.source_path == source_path
    assert runtime.storage_calls == [Path("/tmp/run")]
    assert runtime.run_calls == [(config, storage, True, None)]


@pytest.mark.asyncio
async def test_spsa_entrypoints_forward_typed_request_and_engine_trace_extension() -> None:
    runtime = _RuntimeStub()
    spsa_runtime = cast(SpsaRuntimePort, runtime)
    payload = {"num_updates": 10}
    source_path = Path("/tmp/spsa.yml")

    config = build_spsa_run_config(payload, source_path=source_path, runtime=spsa_runtime)
    storage = create_spsa_run_storage(Path("/tmp/spsa-run"), runtime=spsa_runtime)
    preflight_spsa_dry_run(
        config,
        work_dir=Path("/tmp/spsa-preflight"),
        instance_pool="pool",
        runtime=spsa_runtime,
    )
    await run_spsa_session(
        config,
        storage=storage,
        should_skip_resume=False,
        instance_pool="pool",
        runtime=spsa_runtime,
    )

    assert isinstance(runtime, SessionRuntimePort)
    assert spsa_engine_trace_logger_names(runtime=spsa_runtime) == ("logger.a", "logger.b")
    assert len(runtime.build_calls) == 1
    build_payload, request = runtime.build_calls[0]
    assert build_payload == payload
    assert isinstance(request, SpsaRunConfigBuildRequest)
    assert request.source_path == source_path
    assert runtime.preflight_calls == [(config, Path("/tmp/spsa-preflight"), "pool")]
    assert runtime.run_calls == [(config, storage, False, "pool")]


def test_tournament_runtime_adapter_satisfies_runtime_port_and_storage_contract(tmp_path: Path) -> None:
    adapter = TournamentRuntimeAdapter(
        engine_factory_service=cast(Any, SimpleNamespace()),
        init_dashboard_html=lambda *_args, **_kwargs: None,
        api_server_factory=lambda *_args, **_kwargs: None,
    )

    storage = adapter.create_run_storage(tmp_path / "tournament-run")

    assert isinstance(adapter, SessionRuntimePort)
    assert isinstance(adapter, TournamentRuntimePort)
    assert isinstance(storage, FilesystemRunStorage)
    assert storage.run_dir == (tmp_path / "tournament-run").resolve()


def test_spsa_runtime_adapter_satisfies_runtime_port_and_engine_trace_contract(tmp_path: Path) -> None:
    adapter = SpsaRuntimeAdapter(
        engine_factory_service=cast(Any, SimpleNamespace()),
        init_dashboard_html=lambda *_args, **_kwargs: None,
        api_server_factory=lambda *_args, **_kwargs: None,
        dashboard_service_factory=cast(DashboardSpsaServicesFactory, _DashboardServiceFactoryStub()),
    )

    storage = adapter.create_run_storage(tmp_path / "spsa-run")

    assert isinstance(adapter, SessionRuntimePort)
    assert isinstance(adapter, SpsaRuntimePort)
    assert isinstance(storage, FilesystemRunStorage)
    assert adapter.engine_trace_logger_names() == ("shogiarena",)


def test_spsa_runtime_adapter_dry_run_checks_fixed_options(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []
    adapter = SpsaRuntimeAdapter(
        engine_factory_service=cast(Any, SimpleNamespace()),
        init_dashboard_html=lambda *_args, **_kwargs: None,
        api_server_factory=lambda *_args, **_kwargs: None,
        dashboard_service_factory=cast(DashboardSpsaServicesFactory, _DashboardServiceFactoryStub()),
    )
    config = SimpleNamespace(space_path=tmp_path / "space.yaml", tuned=[SimpleNamespace(name="tuned")])
    config.space_path.write_text(
        "\n".join(
            [
                "schema_version: shogiarena.spsa.space.v1",
                "target: {protocol: usi_options}",
                "parameters: [{}]",
                "",
            ]
        ),
        encoding="utf-8",
    )
    params = [SimpleNamespace(engine_option_name="ParamA", is_not_used=False)]

    monkeypatch.setattr(
        spsa_runtime_adapter_module,
        "load_spsa_space_spec",
        lambda _path: SimpleNamespace(to_param_entries=lambda: params),
    )
    monkeypatch.setattr(
        spsa_runtime_adapter_module,
        "run_yaneuraou_fixed_option_preflight",
        lambda **kwargs: calls.append(kwargs),
    )

    instance_pool = _InstancePoolStub()
    adapter.preflight_dry_run(
        cast(Any, config),
        work_dir=tmp_path / "preflight",
        instance_pool=instance_pool,
    )

    assert len(calls) == 1
    fixed_options = calls[0]
    assert fixed_options["run_dir"] == tmp_path / "preflight"
    assert tuple(cast(object, fixed_options["target_option_names"])) == ("ParamA",)
    assert fixed_options["instance_pool"] is instance_pool


def test_spsa_runtime_adapter_dry_run_defers_manifest_selection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = SpsaRuntimeAdapter(
        engine_factory_service=cast(Any, SimpleNamespace()),
        init_dashboard_html=lambda *_args, **_kwargs: None,
        api_server_factory=lambda *_args, **_kwargs: None,
        dashboard_service_factory=cast(DashboardSpsaServicesFactory, _DashboardServiceFactoryStub()),
    )
    space_path = tmp_path / "space.yaml"
    space_path.write_text(
        "\n".join(
            [
                "schema_version: shogiarena.spsa.space.v1",
                "target:",
                "  protocol: usi_options",
                "  tunable_manifest:",
                "    required: true",
                "select: [aspiration_window_1]",
                "",
            ]
        ),
        encoding="utf-8",
    )
    config = SimpleNamespace(space_path=space_path, tuned=[SimpleNamespace(name="tuned")])

    monkeypatch.setattr(
        spsa_runtime_adapter_module,
        "run_yaneuraou_fixed_option_preflight",
        lambda **_kwargs: pytest.fail("selection must be resolved from the live manifest"),
    )

    adapter.preflight_dry_run(
        cast(Any, config),
        work_dir=tmp_path / "preflight",
        instance_pool=_InstancePoolStub(),
    )


def test_tournament_runtime_adapter_build_run_config_uses_request_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def _fake_from_mapping(payload: dict[str, object], *, base_dir: Path, source_path: Path | None) -> object:
        captured["payload"] = payload
        captured["base_dir"] = base_dir
        captured["source_path"] = source_path
        return "tournament-config"

    monkeypatch.setattr(
        tournament_runtime_adapter_module.TournamentRunConfig,
        "from_mapping",
        staticmethod(_fake_from_mapping),
    )

    adapter = TournamentRuntimeAdapter(
        engine_factory_service=cast(Any, SimpleNamespace()),
        init_dashboard_html=lambda *_args, **_kwargs: None,
        api_server_factory=lambda *_args, **_kwargs: None,
    )
    request = TournamentRunConfigBuildRequest(base_dir=Path("/tmp/base"), source_path=Path("/tmp/source.yml"))

    result = adapter.build_run_config({"games": 4, "label": "abc"}, request=request)

    assert result == "tournament-config"
    assert captured == {
        "payload": {"games": 4, "label": "abc"},
        "base_dir": request.base_dir,
        "source_path": request.source_path,
    }


def test_spsa_runtime_adapter_build_run_config_uses_request_source_path(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def _fake_build(payload: dict[str, object], *, source_path: Path | None) -> object:
        captured["payload"] = payload
        captured["source_path"] = source_path
        return "spsa-config"

    monkeypatch.setattr(spsa_runtime_adapter_module, "parse_spsa_config_mapping", _fake_build)

    adapter = SpsaRuntimeAdapter(
        engine_factory_service=cast(Any, SimpleNamespace()),
        init_dashboard_html=lambda *_args, **_kwargs: None,
        api_server_factory=lambda *_args, **_kwargs: None,
        dashboard_service_factory=cast(DashboardSpsaServicesFactory, _DashboardServiceFactoryStub()),
    )
    request = SpsaRunConfigBuildRequest(source_path=Path("/tmp/source.yml"))

    result = adapter.build_run_config({"num_updates": 8}, request=request)

    assert result == "spsa-config"
    assert captured == {
        "payload": {"num_updates": 8},
        "source_path": request.source_path,
    }


@pytest.mark.asyncio
async def test_tournament_runtime_adapter_run_session_constructs_runner_and_calls_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class _FakeRunner:
        def __init__(self, config: object, **kwargs: object) -> None:
            captured["config"] = config
            captured["kwargs"] = kwargs

        async def run(self) -> None:
            captured["run"] = True

    monkeypatch.setattr(tournament_runtime_adapter_module, "TournamentRunner", _FakeRunner)

    adapter = TournamentRuntimeAdapter(
        engine_factory_service=cast(Any, SimpleNamespace(name="factory")),
        init_dashboard_html=lambda *_args, **_kwargs: None,
        api_server_factory=lambda *_args, **_kwargs: None,
    )
    storage = TempRunStorage()
    pool = _InstancePoolStub()

    await adapter.run_session(
        cast(Any, "cfg"),
        storage=storage,
        should_skip_resume=True,
        instance_pool=pool,
    )

    assert captured["config"] == "cfg"
    assert captured["run"] is True
    assert captured["kwargs"] == {
        "instance_pool": pool,
        "storage": storage,
        "should_skip_resume": True,
        "engine_factory_service": adapter._engine_factory_service,
        "init_dashboard_html": adapter._init_dashboard_html,
        "api_server_factory": adapter._api_server_factory,
    }


@pytest.mark.asyncio
async def test_spsa_runtime_adapter_run_session_constructs_runner_and_calls_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class _FakeRunner:
        def __init__(self, config: object, **kwargs: object) -> None:
            captured["config"] = config
            captured["kwargs"] = kwargs

        async def run(self) -> None:
            captured["run"] = True

    monkeypatch.setattr(spsa_runtime_adapter_module, "SpsaRunner", _FakeRunner)

    adapter = SpsaRuntimeAdapter(
        engine_factory_service=cast(Any, SimpleNamespace(name="factory")),
        init_dashboard_html=lambda *_args, **_kwargs: None,
        api_server_factory=lambda *_args, **_kwargs: None,
        dashboard_service_factory=cast(DashboardSpsaServicesFactory, _DashboardServiceFactoryStub()),
    )
    storage = TempRunStorage()
    pool = _InstancePoolStub()

    await adapter.run_session(
        cast(Any, "cfg"),
        storage=storage,
        should_skip_resume=False,
        instance_pool=pool,
    )

    assert captured["config"] == "cfg"
    assert captured["run"] is True
    assert captured["kwargs"] == {
        "instance_pool": pool,
        "storage": storage,
        "should_skip_resume": False,
        "engine_factory_service": adapter._engine_factory_service,
        "init_dashboard_html": adapter._init_dashboard_html,
        "api_server_factory": adapter._api_server_factory,
        "dashboard_service_factory": adapter._dashboard_service_factory,
    }


@pytest.mark.asyncio
async def test_tournament_runtime_adapter_rejects_invalid_instance_pool() -> None:
    adapter = TournamentRuntimeAdapter(
        engine_factory_service=cast(Any, SimpleNamespace()),
        init_dashboard_html=lambda *_args, **_kwargs: None,
        api_server_factory=lambda *_args, **_kwargs: None,
    )

    with pytest.raises(TypeError, match="instance_pool must expose get_instance\\(\\) and ensure_local_instance\\(\\)"):
        await adapter.run_session(
            cast(Any, "cfg"),
            storage=TempRunStorage(),
            should_skip_resume=False,
            instance_pool=object(),
        )
