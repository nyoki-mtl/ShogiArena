from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from shogiarena._core.contexts.game_session.adapters.orchestration import game_execution
from shogiarena._core.contexts.game_session.adapters.orchestration.config_engine import EngineConfig
from shogiarena._core.contexts.game_session.adapters.orchestration.game_execution import execute_game
from shogiarena._core.contexts.instances.application.instance_models import InstanceConfig, InstanceType
from shogiarena._core.contexts.instances.application.instance_pool import InstancePool
from shogiarena._core.shared.kernel.time_control import TimeControlLimits


class _EngineItem:
    pool_key = "engine-a"
    config_path = Path("engine-a.yaml")
    extra_options = None
    instance_override = None


class _GameSpec:
    black_item = _EngineItem()
    white_item = _EngineItem()
    initial_sfen = "startpos"
    game_id = "g-cancelled"
    black_limits = TimeControlLimits(fixed_time_ms=10)
    white_limits = TimeControlLimits(fixed_time_ms=10)
    before_game_hook = None
    game_round = None
    schedule_metadata = None
    on_game_start = None


class _EnginePool:
    async def acquire_pair_sorted(self, *_args: object, **_kwargs: object) -> tuple[object, object]:
        raise asyncio.CancelledError()


class _Owner:
    def __init__(self, run_dir: Path) -> None:
        self.run_dir = run_dir
        self.engine_pool = _EnginePool()
        self.game_runner = object()
        self.instance_pool = None


class _ConfigLike:
    instance_id = "local"
    options: dict[str, object] = {}


class _UnusedEnginePool:
    async def acquire_pair_sorted(self, *_args: object, **_kwargs: object) -> tuple[object, object]:
        raise AssertionError("engine acquisition should not run after prepare failure")


class _ResourceOwner:
    def __init__(self, run_dir: Path, instance_pool: InstancePool) -> None:
        self.run_dir = run_dir
        self.engine_pool = _UnusedEnginePool()
        self.game_runner = object()
        self.instance_pool = instance_pool
        self.engine_configs = {"engine-a": _ConfigLike()}
        self._resource_poll_interval = 0.001
        self._resource_poll_max_interval = 0.001
        self._stop_event = asyncio.Event()


@pytest.mark.asyncio
async def test_execute_game_records_user_interruption(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    owner = _Owner(tmp_path / "run")
    monkeypatch.setattr(
        game_execution,
        "_prepare_resource_context",
        lambda *_args: _cancelled_resource_context(),
    )

    with pytest.raises(asyncio.CancelledError):
        await execute_game(owner, _GameSpec())

    payload = json.loads((owner.run_dir / "failures" / "run_failures.json").read_text(encoding="utf-8"))
    assert payload["failures"][0]["failure_phase"] == "user_interruption"
    assert payload["failures"][0]["game_id"] == "g-cancelled"


async def _cancelled_resource_context() -> object:
    raise asyncio.CancelledError()


@pytest.mark.asyncio
async def test_execute_game_releases_resources_when_prepare_fails_after_reservation(tmp_path: Path) -> None:
    pool = InstancePool()
    pool.add_instance(InstanceConfig(name="local", type=InstanceType.LOCAL, engine_dir="", slots=8, max_engines=8))
    owner = _ResourceOwner(tmp_path / "run", pool)

    with pytest.raises(TypeError, match="engine_configs entries must be EngineConfig"):
        await execute_game(owner, _GameSpec())

    instance = pool.get_instance("local")
    assert instance is not None
    assert instance.metrics.in_use_slots == 0
    assert instance.metrics.in_use_engines == 0
    assert instance.active_game_by_id == {}


@pytest.mark.asyncio
async def test_local_execution_rejects_worker_version_before_engine_acquisition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = _Owner(tmp_path / "run")
    owner.extra_options = {}
    owner.session_context = SimpleNamespace(run_id="run")
    owner.config = SimpleNamespace(rules=object())
    owner._engine_lifecycle = "reuse"
    owner._timeout_reclassification_enabled = True
    engine_config = EngineConfig(name="engine", engine_path=tmp_path / "engine.yaml")
    resource_context = game_execution._ResourceContext(
        resource_requirements={},
        is_slots_reserved=False,
        instance_role_map={},
        black_engine_spec=engine_config,
        white_engine_spec=engine_config,
    )
    game_spec = SimpleNamespace(
        black_item=_GameSpec.black_item,
        white_item=_GameSpec.white_item,
        initial_sfen=_GameSpec.initial_sfen,
        game_id=_GameSpec.game_id,
        black_limits=_GameSpec.black_limits,
        white_limits=_GameSpec.white_limits,
        black_variant_options={},
        white_variant_options={},
        black_variant_id=None,
        white_variant_id=None,
        clear_hash_before_game=False,
        after_variant_setoption="none",
        before_game_hook=None,
        game_round=None,
        schedule_metadata=None,
        on_game_start=None,
    )
    layers = SimpleNamespace(
        artifact_overlay={},
        arena={},
        declared_overlays={},
        inline={},
    )

    async def _prepare(*_args: object) -> object:
        return resource_context

    monkeypatch.setattr(game_execution, "_prepare_resource_context", _prepare)
    monkeypatch.setattr(game_execution, "build_usi_option_layers", lambda *_args: layers)
    monkeypatch.setattr(
        game_execution,
        "resolve_local_game_execution",
        lambda **_kwargs: SimpleNamespace(spec=object()),
    )

    def _reject_version(*_args: object, **_kwargs: object) -> None:
        raise ValueError("worker version 1.2.0 does not satisfy minimum 1.2.1")

    monkeypatch.setattr(
        game_execution,
        "validate_minimum_worker_version",
        _reject_version,
    )

    with pytest.raises(ValueError, match="does not satisfy minimum"):
        await execute_game(owner, game_spec)
