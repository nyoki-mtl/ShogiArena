import pytest

from shogiarena.arena.instances.models import Instance, InstanceConfig, InstanceType
from shogiarena.arena.instances.pool import InstancePool, ResourceRequest
from shogiarena.arena.instances.slot_policy import estimate_required_slots


class DummySpec:
    def __init__(self, options: dict[str, object] | None = None) -> None:
        self.options = options or {}


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

    assert instance.acquire_resources(slots=2, engines=0)
    assert instance.metrics.in_use_slots == 2
    assert instance.metrics.in_use_engines == 0

    assert instance.acquire_resources(slots=0, engines=3)
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

    inst_b.drain = True
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
    instance.drain = True
    assert not instance.acquire_resources(slots=1, engines=1)


def test_auto_slots_and_engines() -> None:
    cfg = InstanceConfig(name="auto", type=InstanceType.LOCAL, engine_dir="", slots=0, max_engines=0)
    instance = Instance(config=cfg)
    instance.metrics.cpu_count = 8
    assert instance.available_slots == 8
    assert instance.available_engines == 8
    assert instance.acquire_resources(slots=3, engines=2)
    info = instance.to_dict()
    assert info["available_slots"] == 5
    assert info["engine_limit"] == 8
    assert info["slot_capacity"] == 8


def test_slot_limit_enforced_when_max_engines_unlimited() -> None:
    cfg = InstanceConfig(name="limited", type=InstanceType.LOCAL, engine_dir="", slots=3, max_engines=0)
    instance = Instance(config=cfg)
    assert instance.acquire_resources(slots=2, engines=2)
    assert not instance.acquire_resources(slots=2, engines=1)
