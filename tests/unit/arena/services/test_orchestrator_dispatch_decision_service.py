from __future__ import annotations

from dataclasses import dataclass

import pytest

from shogiarena._core.contexts.game_session.application.orchestration.decision_service import (
    OrchestratorDispatchDecisionRequest,
    OrchestratorDispatchDecisionService,
)
from shogiarena._core.contexts.game_session.application.orchestration.game_dispatch import (
    RemoteDispatchRequest,
)


@dataclass
class _EngineConfig:
    instance_id: str | None


@dataclass
class _RemoteInstance:
    is_ssh: bool


@dataclass
class _InstancePool:
    instances: dict[str, _RemoteInstance]

    def get_instance(self, instance_id: str) -> _RemoteInstance | None:
        return self.instances.get(instance_id)


def test_resolve_returns_remote_instance_for_same_ssh_target() -> None:
    service = OrchestratorDispatchDecisionService[_RemoteInstance]()
    decision = service.resolve(
        request=OrchestratorDispatchDecisionRequest(
            instance_pool=_InstancePool({"inst-a": _RemoteInstance(is_ssh=True)}),
            engine_configs={
                "black": _EngineConfig(instance_id="inst-a"),
                "white": _EngineConfig(instance_id="inst-a"),
            },
            dispatch_request=RemoteDispatchRequest(
                black_engine_name="black",
                white_engine_name="white",
            ),
        )
    )

    assert decision.black_instance_id == "inst-a"
    assert decision.white_instance_id == "inst-a"
    assert decision.remote_instance is not None
    assert decision.remote_instance.is_ssh is True


def test_resolve_raises_when_missing_instance_is_required() -> None:
    service = OrchestratorDispatchDecisionService[_RemoteInstance]()
    with pytest.raises(ValueError, match="Instance not found: inst-a"):
        service.resolve(
            request=OrchestratorDispatchDecisionRequest(
                instance_pool=_InstancePool({}),
                engine_configs={
                    "black": _EngineConfig(instance_id="inst-a"),
                    "white": _EngineConfig(instance_id="inst-a"),
                },
                dispatch_request=RemoteDispatchRequest(
                    black_engine_name="black",
                    white_engine_name="white",
                    should_raise_on_missing_instance=True,
                ),
            )
        )
