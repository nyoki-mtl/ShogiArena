from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from shogiarena._core.contexts.game_session.adapters.orchestration.game_execution import execute_game
from shogiarena._core.contexts.instances.application.instance_models import InstanceConfig, InstanceType
from shogiarena._core.contexts.instances.application.instance_pool import InstancePool


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
    black_limits = None
    white_limits = None
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
async def test_execute_game_records_user_interruption(tmp_path: Path) -> None:
    owner = _Owner(tmp_path / "run")

    with pytest.raises(asyncio.CancelledError):
        await execute_game(owner, _GameSpec())

    payload = json.loads((owner.run_dir / "failures" / "run_failures.json").read_text(encoding="utf-8"))
    assert payload["failures"][0]["failure_phase"] == "user_interruption"
    assert payload["failures"][0]["game_id"] == "g-cancelled"


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
