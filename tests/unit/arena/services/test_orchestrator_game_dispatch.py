from __future__ import annotations

from dataclasses import dataclass

import pytest

from shogiarena._core.contexts.game_session.application.orchestration.game_dispatch import (
    RemoteDispatchRequest,
    WorkerResolutionRequest,
    decide_remote_dispatch,
    resolve_completion_worker_idx,
    resolve_required_worker_idx,
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


def test_decide_remote_dispatch_returns_remote_instance_for_same_ssh_target() -> None:
    dispatch = decide_remote_dispatch(
        _InstancePool({"inst-a": _RemoteInstance(is_ssh=True)}),
        engine_configs={
            "black": _EngineConfig(instance_id="inst-a"),
            "white": _EngineConfig(instance_id="inst-a"),
        },
        request=RemoteDispatchRequest(
            black_engine_name="black",
            white_engine_name="white",
        ),
    )

    assert dispatch.black_instance_id == "inst-a"
    assert dispatch.white_instance_id == "inst-a"
    assert dispatch.remote_instance is not None
    assert dispatch.remote_instance.is_ssh is True


def test_decide_remote_dispatch_respects_instance_override() -> None:
    dispatch = decide_remote_dispatch(
        _InstancePool({"inst-b": _RemoteInstance(is_ssh=True)}),
        engine_configs={
            "black": _EngineConfig(instance_id="inst-a"),
            "white": _EngineConfig(instance_id="inst-a"),
        },
        request=RemoteDispatchRequest(
            black_engine_name="black",
            white_engine_name="white",
            black_item_instance_override="inst-b",
            white_item_instance_override="inst-b",
        ),
    )

    assert dispatch.black_instance_id == "inst-b"
    assert dispatch.white_instance_id == "inst-b"
    assert dispatch.remote_instance is not None


def test_decide_remote_dispatch_ignores_non_ssh_instance() -> None:
    dispatch = decide_remote_dispatch(
        _InstancePool({"inst-a": _RemoteInstance(is_ssh=False)}),
        engine_configs={
            "black": _EngineConfig(instance_id="inst-a"),
            "white": _EngineConfig(instance_id="inst-a"),
        },
        request=RemoteDispatchRequest(
            black_engine_name="black",
            white_engine_name="white",
        ),
    )

    assert dispatch.black_instance_id == "inst-a"
    assert dispatch.white_instance_id == "inst-a"
    assert dispatch.remote_instance is None


def test_decide_remote_dispatch_raises_when_missing_instance_is_required() -> None:
    with pytest.raises(ValueError, match="Instance not found: inst-a"):
        decide_remote_dispatch(
            _InstancePool({}),
            engine_configs={
                "black": _EngineConfig(instance_id="inst-a"),
                "white": _EngineConfig(instance_id="inst-a"),
            },
            request=RemoteDispatchRequest(
                black_engine_name="black",
                white_engine_name="white",
                should_raise_on_missing_instance=True,
            ),
        )


def test_resolve_completion_worker_idx_prefers_preassigned() -> None:
    resolved = resolve_completion_worker_idx(
        request=WorkerResolutionRequest(
            preassigned_worker=2,
            game_to_worker={11: 1},
            numeric_game_id=11,
            fallback_worker_idx=0,
        ),
    )
    assert resolved == 2


def test_resolve_required_worker_idx_uses_mapping_when_preassign_missing() -> None:
    resolved = resolve_required_worker_idx(
        request=WorkerResolutionRequest(
            preassigned_worker=None,
            game_to_worker={11: 1},
            numeric_game_id=11,
            fallback_worker_idx=0,
        ),
    )
    assert resolved == 1


def test_resolve_required_worker_idx_uses_fallback_when_mapping_missing() -> None:
    resolved = resolve_required_worker_idx(
        request=WorkerResolutionRequest(
            preassigned_worker=None,
            game_to_worker={},
            numeric_game_id=11,
            fallback_worker_idx=3,
        ),
    )
    assert resolved == 3


def test_resolve_required_worker_idx_raises_when_unresolvable() -> None:
    with pytest.raises(ValueError, match="Worker index could not be resolved"):
        resolve_required_worker_idx(
            request=WorkerResolutionRequest(
                preassigned_worker=None,
                game_to_worker={},
                numeric_game_id=11,
                fallback_worker_idx=None,
            ),
        )
