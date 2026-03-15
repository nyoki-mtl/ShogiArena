"""SPSA update event/index recording service."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from shogiarena._core.shared.kernel.json_types import JsonObject


@dataclass(frozen=True)
class SpsaUpdateRecordingRequest:
    """Input DTO for SPSA update event/index recording."""

    update_idx: int
    params: Mapping[str, float]
    s_plus: float
    s_minus: float
    step: float
    gradients: Mapping[str, float]
    deltas: Mapping[str, float]
    delta_norm: float
    batch_size: int
    total_games: int
    perturbations: JsonObject
    c_k: float
    a_k: float
    iteration_k: int


AppendSpsaEventPort = Callable[[JsonObject], None]
PersistUpdateIndexPort = Callable[[SpsaUpdateRecordingRequest, int], None]


class SpsaUpdateRecordingService:
    """Record SPSA update event and persist update index with shared payload."""

    def record(
        self,
        *,
        request: SpsaUpdateRecordingRequest,
        append_spsa_event: AppendSpsaEventPort,
        persist_update_index: PersistUpdateIndexPort,
    ) -> None:
        timestamp = int(time.time() * 1000)
        append_spsa_event(
            {
                "event": "update",
                "update_idx": int(request.update_idx),
                "params": dict(request.params),
                "s_plus": float(request.s_plus),
                "s_minus": float(request.s_minus),
                "step": float(request.step),
                "gradients": dict(request.gradients),
                "deltas": dict(request.deltas),
                "delta_norm": float(request.delta_norm),
                "batch_size": int(request.batch_size),
                "total_games": int(request.total_games),
                "perturbations": request.perturbations,
                "c_k": float(request.c_k),
                "a_k": float(request.a_k),
                "timestamp": timestamp,
            }
        )
        persist_update_index(request, timestamp)


__all__ = ["SpsaUpdateRecordingRequest", "SpsaUpdateRecordingService"]
