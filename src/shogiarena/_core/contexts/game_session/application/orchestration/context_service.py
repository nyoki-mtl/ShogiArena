"""SPSA orchestration naming/context helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

SpsaPhase = Literal["plus", "minus", "ltc"]


@dataclass(frozen=True)
class SpsaOrchestrationContextRequest:
    """Input DTO for SPSA game label/context resolution."""

    tuned_token: str
    baseline_token: str
    is_tuned_as_black: bool
    tuned_engine_name: str
    baseline_engine_name: str
    phase: SpsaPhase
    phase_suffix: str


@dataclass(frozen=True)
class SpsaOrchestrationContextResponse:
    """Resolved SPSA naming/context values used during game orchestration."""

    tuned_label: str
    baseline_label: str
    black_engine_name: str
    white_engine_name: str
    black_player_label: str
    white_player_label: str
    variant_label: str


def build_orchestration_context(
    *,
    request: SpsaOrchestrationContextRequest,
) -> SpsaOrchestrationContextResponse:
    """SPSA オーケストレーション用のラベルとサイド名を解決する。"""

    tuned_label = f"{request.tuned_token}-tuned" if request.phase == "ltc" else f"{request.tuned_token}-{request.phase}"
    baseline_label = f"{request.baseline_token}-base"
    variant_label = (
        request.tuned_token if request.phase_suffix == "" else f"{request.tuned_token}{request.phase_suffix}"
    )
    if request.is_tuned_as_black:
        black_engine_name = request.tuned_engine_name
        white_engine_name = request.baseline_engine_name
        black_player_label = tuned_label
        white_player_label = baseline_label
    else:
        black_engine_name = request.baseline_engine_name
        white_engine_name = request.tuned_engine_name
        black_player_label = baseline_label
        white_player_label = tuned_label
    return SpsaOrchestrationContextResponse(
        tuned_label=tuned_label,
        baseline_label=baseline_label,
        black_engine_name=black_engine_name,
        white_engine_name=white_engine_name,
        black_player_label=black_player_label,
        white_player_label=white_player_label,
        variant_label=variant_label,
    )


__all__ = [
    "SpsaOrchestrationContextRequest",
    "SpsaOrchestrationContextResponse",
    "SpsaPhase",
    "build_orchestration_context",
]
