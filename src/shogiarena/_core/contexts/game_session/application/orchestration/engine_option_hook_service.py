"""SPSA engine option hook helpers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from shogiarena._core.shared.kernel.json_types import JsonObject


class SpsaEngineOptionHookPort(Protocol):
    """Minimal engine contract for SPSA option hook application."""

    async def apply_engine_options(self, options: JsonObject) -> None: ...


@dataclass(frozen=True)
class SpsaEngineOptionHookRequest:
    """Input DTO for SPSA option hook execution."""

    tuned_pool_key: str
    baseline_pool_key: str
    tuned_options: JsonObject
    baseline_options: JsonObject
    tuned_label: str
    baseline_label: str


async def apply_engine_option_hooks(
    *,
    engines_by_key: Mapping[str, SpsaEngineOptionHookPort],
    request: SpsaEngineOptionHookRequest,
) -> dict[str, str]:
    """SPSA エンジンオプションを適用し、pool_key -> display_name の dict を返す。"""

    tuned_engine = engines_by_key.get(request.tuned_pool_key)
    baseline_engine = engines_by_key.get(request.baseline_pool_key)
    names: dict[str, str] = {}
    if tuned_engine is not None:
        await tuned_engine.apply_engine_options(request.tuned_options)
        names[request.tuned_pool_key] = request.tuned_label
    if baseline_engine is not None:
        if request.baseline_options:
            await baseline_engine.apply_engine_options(request.baseline_options)
        names[request.baseline_pool_key] = request.baseline_label
    return names


__all__ = [
    "SpsaEngineOptionHookPort",
    "SpsaEngineOptionHookRequest",
    "apply_engine_option_hooks",
]
