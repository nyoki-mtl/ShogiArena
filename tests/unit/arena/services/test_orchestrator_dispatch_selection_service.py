from __future__ import annotations

from dataclasses import dataclass

import pytest

from shogiarena._core.contexts.game_session.application.orchestration.decision_service import (
    OrchestratorDispatchDecisionService,
)
from shogiarena._core.contexts.game_session.application.orchestration.selection_service import (
    OrchestratorDispatchSelectionRequest,
    OrchestratorDispatchSelectionService,
)


@dataclass
class _EngineConfig:
    instance_id: str | None


@dataclass
class _RemoteInstance:
    name: str
    is_ssh: bool


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
        )
    )

    assert result.dispatch.black_instance_id == "inst-override"
    assert result.dispatch.white_instance_id == "inst-override"
    assert result.assigned_instance == "inst-override"
    assert result.selected_remote_instance is not None
    assert result.selected_remote_instance.name == "inst-override"
