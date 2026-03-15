"""Shared remote option resolution for orchestrator remote execution paths."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from shogiarena._core.contexts.game_session.adapters.orchestration.config_builders import build_usi_options
from shogiarena._core.shared.kernel.json_types import JsonObject


@dataclass(frozen=True)
class RemotePairOptionContext:
    """Resolved per-side engine spec and merged option maps for remote execution."""

    black_spec: Any | None
    white_spec: Any | None
    black_options: JsonObject
    white_options: JsonObject


def _build_engine_options(extra_options: JsonObject | None, spec: Any | None) -> JsonObject:
    if spec is None:
        return {}
    resolved = build_usi_options(extra_options, spec)
    return resolved or {}


def build_remote_pair_option_context(
    *,
    engine_configs: Mapping[str, object],
    extra_options: JsonObject | None,
    black_engine_name: str,
    white_engine_name: str,
) -> RemotePairOptionContext:
    """Resolve remote per-side engine specs and merged USI option maps."""

    black_spec = engine_configs.get(black_engine_name)
    white_spec = engine_configs.get(white_engine_name)
    return RemotePairOptionContext(
        black_spec=black_spec,
        white_spec=white_spec,
        black_options=_build_engine_options(extra_options, black_spec),
        white_options=_build_engine_options(extra_options, white_spec),
    )


__all__ = ["build_remote_pair_option_context"]
