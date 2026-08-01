from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from shogiarena._core.contexts.game_session.application.orchestration.decision_service import (
    OrchestratorDispatchDecisionService,
)
from shogiarena._core.contexts.game_session.application.orchestration.selection_service import (
    OrchestratorDispatchSelectionRequest,
    OrchestratorDispatchSelectionService,
)
from shogiarena._core.contexts.instances.application.instance_models import (
    Instance,
    InstanceConfig,
    InstanceType,
)
from shogiarena._core.contexts.instances.application.instance_pool import InstancePool


@dataclass
class _EngineConfig:
    instance_id: str | None


@dataclass
class _RemoteConfig:
    tags: list[str]
    operating_system: str = "linux"
    architecture: str = "x86_64"


@dataclass
class _RemoteMetrics:
    is_reachable: bool = True
    in_use_slots: int = 0
    in_use_engines: int = 0


@dataclass
class _RemoteInstance:
    name: str
    is_ssh: bool
    config: _RemoteConfig = field(default_factory=lambda: _RemoteConfig(tags=[]))
    metrics: _RemoteMetrics = field(default_factory=_RemoteMetrics)
    is_draining: bool = False
    effective_slots: int | None = 8
    max_engine_capacity: int = 4
    is_engine_capacity_known: bool = True


@dataclass
class _InstancePool:
    instances: dict[str, _RemoteInstance]

    def get_instance(self, instance_id: str) -> _RemoteInstance | None:
        return self.instances.get(instance_id)


def _build_service() -> OrchestratorDispatchSelectionService[_RemoteInstance]:
    return OrchestratorDispatchSelectionService(
        dispatch_decision_service=OrchestratorDispatchDecisionService(),
    )


def test_resolve_selects_remote_instance_when_force_enginepool_disabled() -> None:
    service = _build_service()

    result = service.resolve(
        request=OrchestratorDispatchSelectionRequest(
            instance_pool=_InstancePool({"inst-a": _RemoteInstance(name="inst-a", is_ssh=True)}),
            engine_configs={
                "black": _EngineConfig(instance_id="inst-a"),
                "white": _EngineConfig(instance_id="inst-a"),
            },
            black_engine_name="black",
            white_engine_name="white",
            should_raise_on_missing_instance=True,
            should_force_enginepool=False,
            scheduling_policy="explicit",
        )
    )

    assert result.dispatch.black_instance_id == "inst-a"
    assert result.dispatch.white_instance_id == "inst-a"
    assert result.assigned_instance == "inst-a"
    assert result.selected_remote_instance is not None
    assert result.selected_remote_instance.name == "inst-a"


def test_resolve_skips_remote_selection_when_force_enginepool_enabled() -> None:
    service = _build_service()

    result = service.resolve(
        request=OrchestratorDispatchSelectionRequest(
            instance_pool=_InstancePool({"inst-a": _RemoteInstance(name="inst-a", is_ssh=True)}),
            engine_configs={
                "black": _EngineConfig(instance_id="inst-a"),
                "white": _EngineConfig(instance_id="inst-a"),
            },
            black_engine_name="black",
            white_engine_name="white",
            should_raise_on_missing_instance=True,
            should_force_enginepool=True,
            scheduling_policy="explicit",
        )
    )

    assert result.assigned_instance == "inst-a"
    assert result.dispatch.remote_instance is not None
    assert result.selected_remote_instance is None


def test_resolve_raises_when_required_instance_is_missing() -> None:
    service = _build_service()

    with pytest.raises(ValueError, match="Instance not found: inst-a"):
        service.resolve(
            request=OrchestratorDispatchSelectionRequest(
                instance_pool=_InstancePool({}),
                engine_configs={
                    "black": _EngineConfig(instance_id="inst-a"),
                    "white": _EngineConfig(instance_id="inst-a"),
                },
                black_engine_name="black",
                white_engine_name="white",
                should_raise_on_missing_instance=True,
                should_force_enginepool=False,
                scheduling_policy="explicit",
            )
        )


def test_resolve_uses_instance_overrides_for_tournament_style_dispatch() -> None:
    service = _build_service()

    result = service.resolve(
        request=OrchestratorDispatchSelectionRequest(
            instance_pool=_InstancePool({"inst-override": _RemoteInstance(name="inst-override", is_ssh=True)}),
            engine_configs={
                "black": _EngineConfig(instance_id=None),
                "white": _EngineConfig(instance_id=None),
            },
            black_engine_name="black",
            white_engine_name="white",
            black_item_instance_override="inst-override",
            white_item_instance_override="inst-override",
            scheduling_policy="explicit",
        )
    )

    assert result.dispatch.black_instance_id == "inst-override"
    assert result.dispatch.white_instance_id == "inst-override"
    assert result.assigned_instance == "inst-override"
    assert result.selected_remote_instance is not None
    assert result.selected_remote_instance.name == "inst-override"


def test_auto_policy_filters_health_tags_capacity_and_uses_least_loaded_worker() -> None:
    pool = InstancePool()
    first = pool.add_instance(
        InstanceConfig(
            name="worker-a",
            type=InstanceType.SSH,
            engine_dir="",
            host="worker-a",
            slots=8,
            max_engines=4,
            tags=["spsa"],
        )
    )
    second = pool.add_instance(
        InstanceConfig(
            name="worker-b",
            type=InstanceType.SSH,
            engine_dir="",
            host="worker-b",
            slots=8,
            max_engines=4,
            tags=["spsa"],
        )
    )
    first.metrics.in_use_slots = 4
    service: OrchestratorDispatchSelectionService[Instance] = OrchestratorDispatchSelectionService(
        dispatch_decision_service=OrchestratorDispatchDecisionService()
    )

    result = service.resolve(
        request=OrchestratorDispatchSelectionRequest(
            instance_pool=pool,
            engine_configs={
                "black": _EngineConfig(instance_id=None),
                "white": _EngineConfig(instance_id=None),
            },
            black_engine_name="black",
            white_engine_name="white",
            scheduling_policy="auto",
            required_tags=("spsa",),
        )
    )

    assert result.assigned_instance == second.name
    assert result.dispatch.black_instance_id == second.name
    assert result.dispatch.white_instance_id == second.name


def test_local_policy_rejects_remote_instance_ids() -> None:
    service = _build_service()

    with pytest.raises(ValueError, match="policy=explicit"):
        service.resolve(
            request=OrchestratorDispatchSelectionRequest(
                instance_pool=_InstancePool({"inst-a": _RemoteInstance(name="inst-a", is_ssh=True)}),
                engine_configs={
                    "black": _EngineConfig(instance_id="inst-a"),
                    "white": _EngineConfig(instance_id="inst-a"),
                },
                black_engine_name="black",
                white_engine_name="white",
            )
        )


def test_local_policy_allows_named_local_instance() -> None:
    pool = InstancePool()
    pool.add_instance(
        InstanceConfig(
            name="local-dev",
            type=InstanceType.LOCAL,
            engine_dir="",
            slots=4,
        )
    )
    service: OrchestratorDispatchSelectionService[Instance] = OrchestratorDispatchSelectionService(
        dispatch_decision_service=OrchestratorDispatchDecisionService()
    )

    result = service.resolve(
        request=OrchestratorDispatchSelectionRequest(
            instance_pool=pool,
            engine_configs={
                "black": _EngineConfig(instance_id="local-dev"),
                "white": _EngineConfig(instance_id="local-dev"),
            },
            black_engine_name="black",
            white_engine_name="white",
        )
    )

    assert result.selected_remote_instance is None
    assert result.assigned_instance is None


def test_auto_policy_rejects_unknown_capacity_with_operator_diagnostics() -> None:
    pool = InstancePool()
    pool.add_instance(
        InstanceConfig(
            name="worker",
            type=InstanceType.SSH,
            engine_dir="",
            host="worker",
            slots=None,
            tags=["spsa"],
        )
    )
    service: OrchestratorDispatchSelectionService[Instance] = OrchestratorDispatchSelectionService(
        dispatch_decision_service=OrchestratorDispatchDecisionService()
    )

    with pytest.raises(RuntimeError, match="slot capacity is unknown"):
        service.resolve(
            request=OrchestratorDispatchSelectionRequest(
                instance_pool=pool,
                engine_configs={
                    "black": _EngineConfig(instance_id=None),
                    "white": _EngineConfig(instance_id=None),
                },
                black_engine_name="black",
                white_engine_name="white",
                scheduling_policy="auto",
                required_tags=("spsa",),
            )
        )


def test_explicit_policy_rejects_missing_engine_assignments() -> None:
    service = _build_service()

    with pytest.raises(ValueError, match="requires instance_id for both"):
        service.resolve(
            request=OrchestratorDispatchSelectionRequest(
                instance_pool=_InstancePool({}),
                engine_configs={
                    "black": _EngineConfig(instance_id=None),
                    "white": _EngineConfig(instance_id=None),
                },
                black_engine_name="black",
                white_engine_name="white",
                scheduling_policy="explicit",
            )
        )
