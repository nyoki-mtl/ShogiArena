"""Pending schedule coordination service shared by tournament orchestrators."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Generic, TypeVar

from shogiarena._core.contexts.game_session.ports.pending_runtime import (
    PendingScheduleCollectRequest,
    PendingScheduleDisplayOrderRequest,
    PendingScheduleRestoreRequest,
    PendingScheduleSpecPort,
    PendingScheduleState,
)

TPendingSpec = TypeVar("TPendingSpec", bound=PendingScheduleSpecPort)


class PendingScheduleService(Generic[TPendingSpec]):
    """Build and maintain pending schedule ordering for queue execution."""

    def initialize_state(self, schedule: Sequence[TPendingSpec]) -> PendingScheduleState[TPendingSpec]:
        ordered_schedule = list(schedule)
        return PendingScheduleState(
            schedule=ordered_schedule,
            display_order_by_game_id={spec.game_id: idx for idx, spec in enumerate(ordered_schedule)},
        )

    def collect_pending_specs(
        self,
        request: PendingScheduleCollectRequest[TPendingSpec],
    ) -> list[TPendingSpec]:
        state = request.state
        return [
            spec
            for spec in state.schedule
            if spec.game_id not in request.completed_game_ids and spec.game_id not in request.cancelled_game_ids
        ]

    def resolve_display_order(
        self,
        request: PendingScheduleDisplayOrderRequest[TPendingSpec],
    ) -> int:
        display_order = request.state.display_order_by_game_id.get(request.spec.game_id)
        if display_order is not None:
            return display_order
        return len(request.state.display_order_by_game_id)

    def merge_restored_spec(
        self,
        request: PendingScheduleRestoreRequest[TPendingSpec],
    ) -> None:
        state = request.state
        known_game_ids = {existing.game_id for existing in state.schedule}
        if request.spec.game_id not in known_game_ids:
            is_inserted = False
            for idx, existing in enumerate(state.schedule):
                current_order = state.display_order_by_game_id.get(existing.game_id, idx)
                if current_order > request.display_order:
                    state.schedule.insert(idx, request.spec)
                    is_inserted = True
                    break
            if not is_inserted:
                state.schedule.append(request.spec)
        state.display_order_by_game_id[request.spec.game_id] = request.display_order


__all__ = [
    "PendingScheduleService",
]
