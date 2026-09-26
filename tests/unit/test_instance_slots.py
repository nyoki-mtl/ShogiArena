import asyncio
from types import SimpleNamespace

import pytest

from shogiarena._core.contexts.game_session.adapters.orchestration.config_engine import EngineConfig
from shogiarena._core.contexts.game_session.adapters.orchestration.remote_lifecycle import (
    manage_remote_pair_instance_lifecycle,
)
from shogiarena._core.contexts.game_session.adapters.orchestration.resource_control import (
    await_instance_resources,
    preflight_parallel_resource_capacity,
)
from shogiarena._core.contexts.instances.application.instance_models import (
    Instance,
    InstanceConfig,
    InstanceType,
)
from shogiarena._core.contexts.instances.application.instance_pool import InstancePool, ResourceRequest
from shogiarena._core.contexts.instances.application.instance_runtime_serialization import serialize_instance
from shogiarena._core.contexts.instances.application.slot_policy import estimate_required_slots
from shogiarena._core.contexts.tournament.domain.tournament_models import GameSpec


class DummySpec:
    def __init__(self, options: dict[str, object] | None = None) -> None:
        self.options = options or {}


class DummyOwner:
    def __init__(self, engine_configs: dict[str, EngineConfig]) -> None:
        self.engine_configs = engine_configs
        self.extra_options = None


def _parallel_owner() -> DummyOwner:
    return DummyOwner(
        {
            "dev": EngineConfig(name="dev", options={"Threads": 4, "USI_Ponder": "false"}),
            "base": EngineConfig(name="base", options={"Threads": 4, "USI_Ponder": "false"}),
        }
    )


def _game_specs(count: int) -> list[GameSpec]:
    return [GameSpec.create("dev", "base", "startpos", index, "seed") for index in range(count)]


def test_estimate_required_slots_uses_threads_and_ponder_flag() -> None:
    spec = DummySpec({"Threads": "4", "USI_Ponder": "false"})
    assert estimate_required_slots(spec) == 2

    spec.options["USI_Ponder"] = "true"
    assert estimate_required_slots(spec) == 4


def test_estimate_required_slots_prefers_extra_options() -> None:
    spec = DummySpec({"Threads": 2})
    extra = {"Threads": 8, "Ponder": "on"}
    assert estimate_required_slots(spec, extra) == 8


def test_estimate_required_slots_defaults_to_one() -> None:
    spec = DummySpec()
    assert estimate_required_slots(spec) == 1


def test_try_acquire_and_release_resources() -> None:
    cfg = InstanceConfig(name="local", type=InstanceType.LOCAL, engine_dir="", slots=4, max_engines=3)
    pool = InstancePool()
    pool.add_instance(cfg)

    requirements = {"local": ResourceRequest(slots=3, engines=2)}
    assert pool.try_acquire_resources(requirements)

    instance = pool.get_instance("local")
    assert instance is not None
    assert instance.metrics.in_use_slots == 3
    assert instance.metrics.in_use_engines == 2

    # Cannot acquire beyond slot capacity
    assert not pool.try_acquire_resources({"local": ResourceRequest(slots=2, engines=1)})
    # Cannot acquire beyond engine capacity
    assert not pool.try_acquire_resources({"local": ResourceRequest(slots=1, engines=2)})

    pool.release_resources(requirements)
    assert instance.metrics.in_use_slots == 0
    assert instance.metrics.in_use_engines == 0


def test_multi_instance_wait_does_not_block_available_single_instance() -> None:
    pool = InstancePool()
    for name in ("a", "b"):
        pool.add_instance(InstanceConfig(name=name, type=InstanceType.LOCAL, engine_dir="", slots=1))
    a_request = {"a": ResourceRequest(slots=1, engines=1)}
    b_request = {"b": ResourceRequest(slots=1, engines=1)}
    both_request = {**a_request, **b_request}
    assert pool.try_acquire_resources(a_request)

    first = pool.register_resource_wait(both_request)
    second = pool.register_resource_wait(b_request)
    assert not pool.try_acquire_resources(both_request, ticket=first)
    assert pool.try_acquire_resources(b_request, ticket=second)
    pool.release_resources(b_request)
    pool.release_resources(a_request)
    assert pool.try_acquire_resources(both_request, ticket=first)
    pool.release_resources(both_request)


def test_multi_instance_wait_keeps_priority_when_first_is_runnable() -> None:
    pool = InstancePool()
    for name in ("a", "b"):
        pool.add_instance(InstanceConfig(name=name, type=InstanceType.LOCAL, engine_dir="", slots=1))
    both_request = {name: ResourceRequest(slots=1, engines=1) for name in ("a", "b")}
    b_request = {"b": ResourceRequest(slots=1, engines=1)}
    first = pool.register_resource_wait(both_request)
    second = pool.register_resource_wait(b_request)
    assert not pool.try_acquire_resources(b_request, ticket=second)
    assert pool.try_acquire_resources(both_request, ticket=first)
    pool.release_resources(both_request)
    assert pool.try_acquire_resources(b_request, ticket=second)
    pool.release_resources(b_request)


@pytest.mark.asyncio
async def test_multi_instance_wait_does_not_time_out_available_instance() -> None:
    pool = InstancePool()
    for name in ("a", "b"):
        pool.add_instance(InstanceConfig(name=name, type=InstanceType.LOCAL, engine_dir="", slots=1))
    a_request = {"a": ResourceRequest(slots=1, engines=1)}
    b_request = {"b": ResourceRequest(slots=1, engines=1)}
    assert pool.try_acquire_resources(a_request)
    owner = SimpleNamespace(
        _stop_event=asyncio.Event(),
        _resource_poll_interval=0.001,
        _resource_poll_max_interval=0.001,
        _resource_allocation_timeout=0.1,
    )
    first = asyncio.create_task(await_instance_resources(owner, pool, {**a_request, **b_request}, "first"))
    await asyncio.sleep(0)
    try:
        await asyncio.wait_for(await_instance_resources(owner, pool, b_request, "second"), timeout=0.05)
        pool.release_resources(b_request)
    finally:
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        pool.release_resources(a_request)


@pytest.mark.asyncio
async def test_multi_instance_bypass_stops_before_oldest_waiter_times_out() -> None:
    pool = InstancePool()
    for name in ("a", "b"):
        pool.add_instance(InstanceConfig(name=name, type=InstanceType.LOCAL, engine_dir="", slots=1))
    a_request = {"a": ResourceRequest(slots=1, engines=1)}
    b_request = {"b": ResourceRequest(slots=1, engines=1)}
    both_request = {**a_request, **b_request}
    assert pool.try_acquire_resources(a_request)

    first = pool.register_resource_wait(both_request, allocation_timeout=0.1)
    second = pool.register_resource_wait(b_request)
    assert pool.try_acquire_resources(b_request, ticket=second)
    pool.release_resources(b_request)

    await asyncio.sleep(0.06)
    third = pool.register_resource_wait(b_request)
    assert not pool.try_acquire_resources(b_request, ticket=third)
    pool.release_resources(a_request)
    assert pool.try_acquire_resources(both_request, ticket=first)
    pool.release_resources(both_request)
    assert pool.try_acquire_resources(b_request, ticket=third)
    pool.release_resources(b_request)


@pytest.mark.asyncio
async def test_resource_waiters_acquire_in_arrival_order_despite_different_poll_rates() -> None:
    pool = InstancePool()
    pool.add_instance(InstanceConfig(name="local", type=InstanceType.LOCAL, engine_dir="", slots=2))
    request = {"local": ResourceRequest(slots=2, engines=2)}
    assert pool.try_acquire_resources(request)

    def owner(poll: float) -> SimpleNamespace:
        return SimpleNamespace(
            _stop_event=asyncio.Event(),
            _resource_poll_interval=poll,
            _resource_poll_max_interval=poll,
            _resource_allocation_timeout=1.0,
        )

    first = asyncio.create_task(await_instance_resources(owner(0.05), pool, request, "first"))
    await asyncio.sleep(0)
    second = asyncio.create_task(await_instance_resources(owner(0.001), pool, request, "second"))
    await asyncio.sleep(0.005)
    pool.release_resources(request)
    await first
    assert not second.done()
    pool.release_resources(request)
    await second
    pool.release_resources(request)


@pytest.mark.asyncio
async def test_timed_out_resource_waiter_does_not_block_next_game() -> None:
    pool = InstancePool()
    pool.add_instance(InstanceConfig(name="local", type=InstanceType.LOCAL, engine_dir="", slots=2))
    request = {"local": ResourceRequest(slots=2, engines=2)}
    assert pool.try_acquire_resources(request)
    first_owner = SimpleNamespace(
        _stop_event=asyncio.Event(),
        _resource_poll_interval=0.005,
        _resource_poll_max_interval=0.005,
        _resource_allocation_timeout=0.025,
    )
    second_owner = SimpleNamespace(
        _stop_event=asyncio.Event(),
        _resource_poll_interval=0.005,
        _resource_poll_max_interval=0.005,
        _resource_allocation_timeout=1.0,
    )
    first = asyncio.create_task(await_instance_resources(first_owner, pool, request, "first"))
    await asyncio.sleep(0)
    second = asyncio.create_task(await_instance_resources(second_owner, pool, request, "second"))
    with pytest.raises(TimeoutError):
        await first
    pool.release_resources(request)
    await second
    pool.release_resources(request)


def test_try_acquire_resources_rejects_unknown_instance() -> None:
    pool = InstancePool()
    with pytest.raises(KeyError):
        pool.try_acquire_resources({"unknown": ResourceRequest(slots=1, engines=1)})


def test_max_engines_defaults_to_slots() -> None:
    cfg = InstanceConfig(name="local", type=InstanceType.LOCAL, engine_dir="", slots=5)
    pool = InstancePool()
    pool.add_instance(cfg)
    instance = pool.get_instance("local")
    assert instance is not None
    assert instance.max_engine_capacity == 5


def test_partial_resource_reservations() -> None:
    cfg = InstanceConfig(name="local", type=InstanceType.LOCAL, engine_dir="", slots=4, max_engines=4)
    pool = InstancePool()
    pool.add_instance(cfg)
    instance = pool.get_instance("local")
    assert instance is not None

    assert instance.try_acquire_resources(slots=2, engines=0)
    assert instance.metrics.in_use_slots == 2
    assert instance.metrics.in_use_engines == 0

    assert instance.try_acquire_resources(slots=0, engines=3)
    assert instance.metrics.in_use_slots == 2
    assert instance.metrics.in_use_engines == 3

    instance.release_resources(slots=2, engines=1)
    assert instance.metrics.in_use_slots == 0
    assert instance.metrics.in_use_engines == 2


def test_try_acquire_resources_rolls_back_on_failure() -> None:
    cfg_a = InstanceConfig(name="a", type=InstanceType.LOCAL, engine_dir="", slots=2, max_engines=2)
    cfg_b = InstanceConfig(name="b", type=InstanceType.LOCAL, engine_dir="", slots=1, max_engines=1)
    pool = InstancePool()
    pool.add_instance(cfg_a)
    pool.add_instance(cfg_b)

    inst_a = pool.get_instance("a")
    inst_b = pool.get_instance("b")
    assert inst_a is not None and inst_b is not None

    inst_b.is_draining = True
    requirements = {
        "a": ResourceRequest(slots=1, engines=1),
        "b": ResourceRequest(slots=1, engines=1),
    }
    assert not pool.try_acquire_resources(requirements)
    assert inst_a.metrics.in_use_slots == 0
    assert inst_a.metrics.in_use_engines == 0


def test_acquire_resources_respects_drain() -> None:
    cfg = InstanceConfig(name="local", type=InstanceType.LOCAL, engine_dir="", slots=2, max_engines=2)
    pool = InstancePool()
    pool.add_instance(cfg)
    instance = pool.get_instance("local")
    assert instance is not None
    instance.is_draining = True
    assert not instance.try_acquire_resources(slots=1, engines=1)


def test_auto_slots_and_engines() -> None:
    cfg = InstanceConfig(name="auto", type=InstanceType.LOCAL, engine_dir="", slots=None, max_engines=0)
    instance = Instance(config=cfg)
    instance.metrics.cpu_count = 8
    assert instance.available_slots == 8
    assert instance.available_engines == 8
    assert instance.try_acquire_resources(slots=3, engines=2)
    info = serialize_instance(instance)
    assert info["available_slots"] == 5
    assert info["engine_limit"] == 8
    assert info["slot_capacity"] == 8


def test_slot_limit_enforced_when_max_engines_unlimited() -> None:
    cfg = InstanceConfig(name="limited", type=InstanceType.LOCAL, engine_dir="", slots=3, max_engines=0)
    instance = Instance(config=cfg)
    assert instance.try_acquire_resources(slots=2, engines=2)
    assert not instance.try_acquire_resources(slots=2, engines=1)


def test_parallel_resource_preflight_rejects_silent_slot_throttling() -> None:
    pool = InstancePool()
    pool.add_instance(InstanceConfig(name="local", type=InstanceType.LOCAL, engine_dir="", slots=8, max_engines=8))

    with pytest.raises(RuntimeError, match="would require 12 slot"):
        preflight_parallel_resource_capacity(_parallel_owner(), pool, _game_specs(3), 3, mode="error")


def test_parallel_resource_preflight_warn_mode_allows_throttling(
    caplog: pytest.LogCaptureFixture,
) -> None:
    pool = InstancePool()
    pool.add_instance(InstanceConfig(name="local", type=InstanceType.LOCAL, engine_dir="", slots=8, max_engines=8))

    with caplog.at_level("WARNING"):
        preflight_parallel_resource_capacity(_parallel_owner(), pool, _game_specs(3), 3, mode="warn")

    assert "tournament.num_parallel=3 cannot be satisfied" in caplog.text


def test_parallel_resource_preflight_passes_when_capacity_matches_parallelism() -> None:
    pool = InstancePool()
    pool.add_instance(InstanceConfig(name="local", type=InstanceType.LOCAL, engine_dir="", slots=12, max_engines=8))

    preflight_parallel_resource_capacity(_parallel_owner(), pool, _game_specs(3), 3, mode="error")


def test_parallel_resource_preflight_rejects_engine_capacity() -> None:
    pool = InstancePool()
    pool.add_instance(InstanceConfig(name="local", type=InstanceType.LOCAL, engine_dir="", slots=16, max_engines=3))

    with pytest.raises(RuntimeError, match="would require 4 engine"):
        preflight_parallel_resource_capacity(_parallel_owner(), pool, _game_specs(2), 2, mode="error")


@pytest.mark.asyncio
async def test_resource_allocation_rejects_unknown_capacity_without_polling() -> None:
    pool = InstancePool()
    pool.add_instance(
        InstanceConfig(
            name="worker",
            type=InstanceType.SSH,
            engine_dir="",
            host="worker",
            slots=None,
        )
    )
    owner = type(
        "Owner",
        (),
        {
            "_stop_event": asyncio.Event(),
            "_resource_poll_interval": 0.001,
            "_resource_poll_max_interval": 0.001,
            "_resource_allocation_timeout": 0.01,
        },
    )()

    with pytest.raises(RuntimeError, match="slot capacity is unknown"):
        await await_instance_resources(
            owner,
            pool,
            {"worker": ResourceRequest(slots=2, engines=2)},
            game_id="game",
        )


@pytest.mark.asyncio
async def test_resource_allocation_has_bounded_deadline() -> None:
    pool = InstancePool()
    instance = pool.add_instance(
        InstanceConfig(
            name="worker",
            type=InstanceType.SSH,
            engine_dir="",
            host="worker",
            slots=2,
            max_engines=2,
        )
    )
    instance.metrics.in_use_slots = 2
    instance.metrics.in_use_engines = 2
    owner = type(
        "Owner",
        (),
        {
            "_stop_event": asyncio.Event(),
            "_resource_poll_interval": 0.001,
            "_resource_poll_max_interval": 0.001,
            "_resource_allocation_timeout": 0.01,
        },
    )()

    with pytest.raises(TimeoutError, match="Timed out after 0.0s"):
        await await_instance_resources(
            owner,
            pool,
            {"worker": ResourceRequest(slots=2, engines=2)},
            game_id="game",
        )


@pytest.mark.asyncio
async def test_remote_pair_lifecycle_shares_capacity_and_active_game_registration() -> None:
    pool = InstancePool()
    instance = pool.add_instance(
        InstanceConfig(
            name="worker",
            type=InstanceType.SSH,
            engine_dir="",
            host="worker",
            slots=2,
            max_engines=2,
        )
    )
    owner = type(
        "Owner",
        (),
        {
            "instance_pool": pool,
            "engine_configs": {
                "black": EngineConfig(name="black", options={"Threads": 1}),
                "white": EngineConfig(name="white", options={"Threads": 1}),
            },
            "extra_options": None,
            "_stop_event": asyncio.Event(),
            "_resource_poll_interval": 0.001,
            "_resource_poll_max_interval": 0.001,
            "_resource_allocation_timeout": 0.1,
        },
    )()
    black_item = type(
        "Item",
        (),
        {"pool_key": "black", "instance_override": None, "extra_options": None},
    )()
    white_item = type(
        "Item",
        (),
        {"pool_key": "white", "instance_override": None, "extra_options": None},
    )()

    async with manage_remote_pair_instance_lifecycle(
        owner,
        game_id="spsa-game",
        initial_sfen="startpos",
        round_index=1,
        instance_id="worker",
        black_engine_name="black",
        white_engine_name="white",
        black_pool_key="black",
        white_pool_key="white",
        black_item=black_item,
        white_item=white_item,
        black_limits=None,
        white_limits=None,
    ):
        assert instance.metrics.in_use_slots == 2
        assert instance.metrics.in_use_engines == 2
        assert "spsa-game" in instance.active_game_by_id
        assert not pool.try_acquire_resources({"worker": ResourceRequest(slots=1, engines=1)})

    assert instance.metrics.in_use_slots == 0
    assert instance.metrics.in_use_engines == 0
    assert instance.active_game_by_id == {}
