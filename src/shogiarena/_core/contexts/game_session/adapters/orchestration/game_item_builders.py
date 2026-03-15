"""Engine game item builders shared across orchestrators."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Generic, Literal, TypeVar

from shogiarena._core.contexts.game_session.adapters.orchestration.config_builders import (
    build_usi_options,
)
from shogiarena._core.contexts.instances.ports.orchestrator_primitives import make_role_pool_key
from shogiarena._core.shared.kernel.json_types import JsonObject
from shogiarena._core.shared.kernel.time_control import TimeControlLimits
from shogiarena._core.shared.kernel.time_control_resolution import build_time_control_limits

from .config_engine import EngineConfig

TEngineGameSpec = TypeVar("TEngineGameSpec")

TournamentEngineGameSpecFn = Callable[
    [str, Path, JsonObject | None, str | None, Literal["black", "white"]],
    TEngineGameSpec,
]
SpsaEngineGameSpecFn = Callable[[str, Path, JsonObject | None], TEngineGameSpec]


@dataclass(frozen=True)
class PreparedGameItems(Generic[TEngineGameSpec]):
    """Prepared per-side engine items and time-control limits."""

    black_item: TEngineGameSpec
    white_item: TEngineGameSpec
    black_limits: TimeControlLimits
    white_limits: TimeControlLimits


def _require_time_control_limits(
    *,
    base_time_control: TimeControlLimits | None,
    override_time_control: TimeControlLimits | None,
    missing_message: str,
) -> TimeControlLimits:
    limits = build_time_control_limits(base_time_control, override_time_control)
    if limits is None:
        raise RuntimeError(missing_message)
    return limits


def build_tournament_game_items(
    *,
    game_id: str,
    black_engine_name: str,
    white_engine_name: str,
    engine_configs: Mapping[str, EngineConfig],
    extra_options: JsonObject | None,
    base_time_control: TimeControlLimits | None,
    black_instance_override: str | None,
    white_instance_override: str | None,
    engine_game_spec_fn: TournamentEngineGameSpecFn[TEngineGameSpec],
) -> PreparedGameItems[TEngineGameSpec]:
    """Build tournament game items and resolved time-control limits."""

    black_config_spec = engine_configs.get(black_engine_name)
    white_config_spec = engine_configs.get(white_engine_name)
    if black_config_spec is None or white_config_spec is None:
        raise ValueError(f"Missing engine config for game {game_id}")

    black_extras = build_usi_options(extra_options, black_config_spec)
    white_extras = build_usi_options(extra_options, white_config_spec)

    black_limits = _require_time_control_limits(
        base_time_control=base_time_control,
        override_time_control=black_config_spec.time_control,
        missing_message=(
            "Missing required time_control for tournament engine. "
            "Provide rules.time_control or per-engine time_control."
        ),
    )
    white_limits = _require_time_control_limits(
        base_time_control=base_time_control,
        override_time_control=white_config_spec.time_control,
        missing_message=(
            "Missing required time_control for tournament engine. "
            "Provide rules.time_control or per-engine time_control."
        ),
    )

    black_path = black_config_spec.engine_path
    white_path = white_config_spec.engine_path
    if black_path is None:
        raise ValueError(f"Engine '{black_engine_name}' is missing a resolved engine_path")
    if white_path is None:
        raise ValueError(f"Engine '{white_engine_name}' is missing a resolved engine_path")

    black_item = engine_game_spec_fn(
        make_role_pool_key(black_engine_name, "black"),
        black_path,
        black_extras,
        black_instance_override,
        "black",
    )
    white_item = engine_game_spec_fn(
        make_role_pool_key(white_engine_name, "white"),
        white_path,
        white_extras,
        white_instance_override,
        "white",
    )
    return PreparedGameItems(
        black_item=black_item,
        white_item=white_item,
        black_limits=black_limits,
        white_limits=white_limits,
    )


def build_spsa_game_items(
    *,
    is_tuned_as_black: bool,
    base_spec: EngineConfig,
    tuned_spec: EngineConfig,
    baseline_name: str,
    tuned_name: str,
    baseline_config_path: Path,
    tuned_config_path: Path,
    extra_options: JsonObject | None,
    base_time_control: TimeControlLimits | None,
    time_control_override: TimeControlLimits | None,
    engine_game_spec_fn: SpsaEngineGameSpecFn[TEngineGameSpec],
) -> PreparedGameItems[TEngineGameSpec]:
    """Build SPSA game items and resolved time-control limits."""

    base_key = make_role_pool_key(baseline_name, "baseline")
    tuned_key = make_role_pool_key(tuned_name, "tuned")

    base_limits = _require_time_control_limits(
        base_time_control=base_time_control,
        override_time_control=base_spec.time_control,
        missing_message=(
            f"Missing required time_control for SPSA engine '{base_spec.name}'. "
            "Add a time_control block to the engine_config."
        ),
    )
    tuned_limits = _require_time_control_limits(
        base_time_control=base_time_control,
        override_time_control=tuned_spec.time_control,
        missing_message=(
            f"Missing required time_control for SPSA engine '{tuned_spec.name}'. "
            "Add a time_control block to the engine_config."
        ),
    )

    if is_tuned_as_black:
        black_item = engine_game_spec_fn(tuned_key, tuned_config_path, extra_options)
        white_item = engine_game_spec_fn(base_key, baseline_config_path, extra_options)
        black_limits = tuned_limits
        white_limits = base_limits
    else:
        black_item = engine_game_spec_fn(base_key, baseline_config_path, extra_options)
        white_item = engine_game_spec_fn(tuned_key, tuned_config_path, extra_options)
        black_limits = base_limits
        white_limits = tuned_limits

    if time_control_override is not None:
        black_limits = time_control_override
        white_limits = time_control_override

    return PreparedGameItems(
        black_item=black_item,
        white_item=white_item,
        black_limits=black_limits,
        white_limits=white_limits,
    )


__all__ = [
    "build_spsa_game_items",
    "build_tournament_game_items",
]
