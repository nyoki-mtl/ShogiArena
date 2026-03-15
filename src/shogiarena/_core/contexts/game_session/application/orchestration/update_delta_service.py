"""SPSA parameter delta application service."""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Generic, Protocol, TypeVar


class SpsaParamDeltaPort(Protocol):
    """Minimal parameter contract for SPSA delta application."""

    name: str
    value: float
    delta: float
    is_not_used: bool


ParamEntryT = TypeVar("ParamEntryT", bound=SpsaParamDeltaPort)


@dataclass(frozen=True)
class SpsaUpdateDeltaRequest(Generic[ParamEntryT]):
    """Input DTO for one SPSA parameter delta application."""

    params: list[ParamEntryT]
    perturbations: Sequence[float]
    step: float
    step_factor: float
    c_k: float
    mobility_factor: float
    early_stop_delta_norm_threshold: float | None = None


@dataclass(frozen=True)
class SpsaUpdateDeltaResponse:
    """Output DTO for SPSA delta application result."""

    gradients: dict[str, float]
    deltas: dict[str, float]
    delta_norm: float
    changed_params: int
    should_stop: bool


class SpsaUpdateDeltaService:
    """Apply SPSA update deltas and compute telemetry metrics."""

    def apply(
        self,
        *,
        request: SpsaUpdateDeltaRequest[ParamEntryT],
        quantize_value: Callable[[ParamEntryT, float], float],
    ) -> SpsaUpdateDeltaResponse:
        gradients: dict[str, float] = {}
        deltas: dict[str, float] = {}
        delta_norm_sq = 0.0

        for index, param in enumerate(request.params):
            if param.is_not_used:
                continue
            c_i = request.perturbations[index] if index < len(request.perturbations) else 0.0
            if c_i != 0.0 and request.c_k != 0.0:
                gradients[param.name] = float(request.step / (2.0 * request.c_k * c_i))
            else:
                gradients[param.name] = 0.0

            delta_theta = request.mobility_factor * float(param.delta) * request.step_factor * c_i
            new_value = param.value + delta_theta
            quantized = quantize_value(param, new_value)
            delta_value = float(quantized - param.value)
            deltas[param.name] = delta_value
            delta_norm_sq += delta_value * delta_value
            param.value = quantized

        delta_norm = math.sqrt(delta_norm_sq)
        changed_params = sum(1 for delta_value in deltas.values() if abs(delta_value) > 1e-10)
        should_stop = (
            request.early_stop_delta_norm_threshold is not None and delta_norm < request.early_stop_delta_norm_threshold
        )
        return SpsaUpdateDeltaResponse(
            gradients=gradients,
            deltas=deltas,
            delta_norm=delta_norm,
            changed_params=changed_params,
            should_stop=should_stop,
        )


__all__ = ["SpsaUpdateDeltaRequest", "SpsaUpdateDeltaService"]
