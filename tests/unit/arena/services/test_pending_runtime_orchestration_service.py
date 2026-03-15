from __future__ import annotations

from dataclasses import dataclass

import pytest

from shogiarena._core.contexts.game_session.application.orchestration.runtime_orchestration_service import (
    PendingRuntimeOrchestrationService,
)


@dataclass(slots=True)
class _DummySpec:
    game_id: str


def _build_specs(*game_ids: str) -> list[_DummySpec]:
    return [_DummySpec(game_id=game_id) for game_id in game_ids]


@dataclass(slots=True)
class _DummyRuntime:
    seen: list[str]
    skip_game_ids: set[str]

    def should_skip_pending_item(self, item: _DummySpec) -> bool:
        return item.game_id in self.skip_game_ids

    async def run_pending_item(self, item: _DummySpec) -> None:
        self.seen.append(item.game_id)


@dataclass(slots=True)
class _FailingRuntime:
    fail_game_id: str

    def should_skip_pending_item(self, item: _DummySpec) -> bool:
        return False

    async def run_pending_item(self, item: _DummySpec) -> None:
        if item.game_id == self.fail_game_id:
            raise RuntimeError(f"boom:{item.game_id}")


def test_collect_pending_specs_excludes_completed_and_cancelled() -> None:
    service = PendingRuntimeOrchestrationService[_DummySpec]()
    state = service.initialize_state(_build_specs("g1", "g2", "g3"))

    pending = service.collect_pending_specs(
        state=state,
        completed_game_ids={"g2"},
        cancelled_game_ids={"g3"},
    )

    assert [spec.game_id for spec in pending] == ["g1"]


@pytest.mark.asyncio
async def test_execute_pending_items_respects_display_order_and_skip() -> None:
    service = PendingRuntimeOrchestrationService[_DummySpec]()
    state = service.initialize_state(_build_specs("g2", "g1", "g3"))
    pending = service.collect_pending_specs(
        state=state,
        completed_game_ids=set(),
        cancelled_game_ids=set(),
    )
    seen: list[str] = []
    runtime = _DummyRuntime(seen=seen, skip_game_ids={"g1"})

    await service.execute_pending_items(
        state=state,
        pending_specs=pending,
        concurrency_limit=1,
        running_tasks=set(),
        worker_tasks=set(),
        runtime=runtime,
    )

    assert seen == ["g2", "g3"]
    assert state.queue is None


@pytest.mark.asyncio
async def test_enqueue_restored_item_merges_schedule_and_enqueues_when_queue_exists() -> None:
    service = PendingRuntimeOrchestrationService[_DummySpec]()
    state = service.initialize_state(_build_specs("g2"))
    service.create_queue_state(state=state)
    seen: list[str] = []
    runtime = _DummyRuntime(seen=seen, skip_game_ids=set())

    await service.enqueue_restored_item(
        state=state,
        spec=_DummySpec(game_id="g1"),
        display_order=0,
    )
    await service.consume_pending_items(
        state=state,
        concurrency_limit=1,
        running_tasks=set(),
        worker_tasks=set(),
        runtime=runtime,
    )
    service.clear_queue_state(state=state)

    assert sorted(spec.game_id for spec in state.schedule.schedule) == ["g1", "g2"]
    assert state.schedule.display_order_by_game_id["g1"] == 0
    assert seen == ["g1"]


@pytest.mark.asyncio
async def test_execute_pending_items_propagates_worker_error_without_hanging() -> None:
    service = PendingRuntimeOrchestrationService[_DummySpec]()
    state = service.initialize_state(_build_specs("g1", "g2", "g3"))
    pending = service.collect_pending_specs(
        state=state,
        completed_game_ids=set(),
        cancelled_game_ids=set(),
    )
    runtime = _FailingRuntime(fail_game_id="g1")

    with pytest.raises(RuntimeError, match="boom:g1"):
        await service.execute_pending_items(
            state=state,
            pending_specs=pending,
            concurrency_limit=2,
            running_tasks=set(),
            worker_tasks=set(),
            runtime=runtime,
        )
