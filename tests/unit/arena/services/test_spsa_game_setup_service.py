from __future__ import annotations

from dataclasses import dataclass, field

from shogiarena._core.contexts.game_session.application.orchestration.decision_service import (
    OrchestratorDispatchDecisionService,
)
from shogiarena._core.contexts.game_session.application.orchestration.game_assignment_service import (
    OrchestratorGameAssignmentService,
)
from shogiarena._core.contexts.game_session.application.orchestration.game_setup_service import (
    SpsaGameSetupRequest,
    SpsaGameSetupService,
)
from shogiarena._core.contexts.game_session.application.orchestration.selection_service import (
    OrchestratorDispatchSelectionService,
)


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


def _build_service() -> SpsaGameSetupService[_RemoteInstance]:
    return SpsaGameSetupService(
        assignment_service=OrchestratorGameAssignmentService(),
        dispatch_selection_service=OrchestratorDispatchSelectionService(
            dispatch_decision_service=OrchestratorDispatchDecisionService(),
        ),
    )


def test_resolve_builds_context_assignment_dispatch_and_event_payload() -> None:
    service = _build_service()

    result = service.resolve(
        request=SpsaGameSetupRequest(
            preassigned_game_id=None,
            scheduler_worker_idx=1,
            game_to_worker={7: 5},
            update_idx=3,
            phase="plus",
            phase_suffix="+",
            tuned_token="v000003",
            baseline_token="v000002",
            is_tuned_as_black=True,
            tuned_engine_name="tuned",
            baseline_engine_name="base",
            event_family="spsa",
            instance_pool=_InstancePool({"inst-a": _RemoteInstance(name="inst-a", is_ssh=True)}),
            engine_configs={
                "tuned": _EngineConfig(instance_id="inst-a"),
                "base": _EngineConfig(instance_id="inst-a"),
            },
            should_force_enginepool=False,
            scheduling_policy="explicit",
        ),
        resolve_game_id=lambda: "game-generated",
        to_numeric_game_id=lambda game_id: 7,
        preassign_worker=lambda numeric_id: 2,
    )

    assert result.context.black_engine_name == "tuned"
    assert result.context.white_engine_name == "base"
    assert result.context.black_player_label == "v000003-plus"
    assert result.context.white_player_label == "v000002-base"
    assert result.assignment.game_id == "game-generated"
    assert result.assignment.resolved_worker_idx == 2
    assert result.dispatch_selection.dispatch.black_instance_id == "inst-a"
    assert result.dispatch_selection.dispatch.white_instance_id == "inst-a"
    assert result.dispatch_selection.assigned_instance == "inst-a"
    assert result.dispatch_selection.selected_remote_instance is not None
    assert result.dispatch_selection.selected_remote_instance.name == "inst-a"
    assert result.event_common["event"] == "game_scheduled"
    assert result.event_common["game_id"] == "game-generated"
    assert result.event_common["worker_idx"] == 2
    assert result.event_common["assigned_instance"] == "inst-a"
