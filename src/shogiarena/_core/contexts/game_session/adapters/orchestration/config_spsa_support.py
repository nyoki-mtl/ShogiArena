"""Helper functions used by SPSA configuration parser."""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from omegaconf import DictConfig, OmegaConf

from shogiarena._core.contexts.game_session.application.engine.config_normalizer import (
    coerce_optional_positive_int,
    load_overlays,
    normalize_check_templates,
    normalize_overlays,
    parse_isready_sync_strategy,
)
from shogiarena._core.contexts.game_session.application.engine.option_coercion import (
    coerce_engine_option_map,
)
from shogiarena._core.shared.kernel.json_coercion import coerce_json_object_serialized
from shogiarena._core.shared.kernel.json_types import JsonObject, JsonValue
from shogiarena._core.shared.kernel.paths import resolve_path_like
from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_bool, coerce_float, coerce_int, coerce_str
from shogiarena._core.shared.kernel.time_control import TimeControlLimits

from .config_core import RulesConfig, parse_time_control_raw
from .config_engine import EngineConfig
from .config_spsa_models import _EngineCommonKwargs

logger = logging.getLogger(__name__)


def _get_spsa_int(node: Mapping[str, JsonValue], name: str, default: int) -> int:
    raw = node.get(name)
    if raw is None:
        return default
    parsed = coerce_int(raw)
    if parsed is None:
        raise TypeError(f"spsa.{name} must be an integer")
    return parsed


def _get_spsa_optional_int(node: Mapping[str, JsonValue], name: str) -> int | None:
    raw = node.get(name)
    if raw is None:
        return None
    parsed = coerce_int(raw)
    if parsed is None:
        raise TypeError(f"spsa.{name} must be an integer when provided")
    return parsed


def _get_spsa_float(node: Mapping[str, JsonValue], name: str, default: float) -> float:
    raw = node.get(name)
    if raw is None:
        return default
    parsed = coerce_float(raw)
    if parsed is None:
        raise TypeError(f"spsa.{name} must be a finite number")
    return parsed


def _extract_engine_common_kwargs(x: Mapping[str, JsonValue], overlays: list[Path]) -> _EngineCommonKwargs:
    """artifact / engine_path 両ブランチで共通する EngineConfig kwargs を抽出する。"""
    return {
        "mate_default_ply_limit": coerce_optional_positive_int(
            x.get("mate_default_ply_limit"),
            field_name="engines[0].mate_default_ply_limit",
        ),
        "mate_default_node_limit": coerce_optional_positive_int(
            x.get("mate_default_node_limit"),
            field_name="engines[0].mate_default_node_limit",
        ),
        "is_mate_default_infinite": coerce_bool(x.get("mate_default_infinite", False)),
        "should_mate_wait_for_bestmove": coerce_bool(x.get("mate_wait_for_bestmove", False)),
        "isready_sync_strategy": parse_isready_sync_strategy(x.get("isready_sync_strategy")),
        "isready_lock_key": coerce_str(x.get("isready_lock_key")),
        "isready_lock_template": coerce_str(x.get("isready_lock_template")),
        "isready_lock_check_key": coerce_str(x.get("isready_lock_check_key")),
        "isready_lock_check_template": coerce_str(x.get("isready_lock_check_template")),
        "isready_lock_check_templates": normalize_check_templates(x.get("isready_lock_check_templates")),
        "should_skip_isready_lock_if_exists": coerce_bool(x.get("isready_lock_skip_if_exists", False)),
        "time_control": parse_time_control_raw(x.get("time_control")),
        "options_overlays": overlays,
        "instance_id": coerce_str(x.get("instance_id")),
    }


def _map_engine(x: Mapping[str, JsonValue]) -> EngineConfig:
    _warn_unknown_keys(
        x,
        allowed={
            "artifact",
            "engine_path",
            "build_options",
            "name",
            "options",
            "options_overlays",
            "mate_default_ply_limit",
            "mate_default_node_limit",
            "mate_default_infinite",
            "mate_wait_for_bestmove",
            "isready_sync_strategy",
            "isready_lock_key",
            "isready_lock_template",
            "isready_lock_check_key",
            "isready_lock_check_template",
            "isready_lock_check_templates",
            "isready_lock_skip_if_exists",
            "time_control",
            "instance_id",
        },
        label="engines[0]",
    )
    # Exactly one of artifact or engine_path
    has_art = coerce_str(x.get("artifact")) is not None
    engine_path_key = x.get("engine_path")
    has_cfg = coerce_str(engine_path_key) is not None
    if has_art == has_cfg:
        raise ValueError("Engine must specify exactly one of 'artifact' or 'engine_path'")

    name = x.get("name")
    overlays = normalize_overlays(x.get("options_overlays"))
    common = _extract_engine_common_kwargs(x, overlays)

    if has_art:
        art = str(x["artifact"]).strip()
        bo = coerce_engine_option_map(x.get("build_options"), field_name="engines[0].build_options")

        # Build merged options: overlay -> options_overlays -> inline options(dict)
        merged: JsonObject = {}
        merged.update(load_overlays(overlays))
        inline_opts = x.get("options")
        if isinstance(inline_opts, Mapping):
            merged.update(coerce_json_object_serialized(inline_opts, field_name="engines[0].options"))

        # Name default: <repo>_<commit>-<overlay>
        if not name:
            overlay_label = "nooverlay"
            if overlays:
                overlay_label = overlays[0].stem
            m = re.match(r"^([A-Za-z0-9._-]+)/([A-Fa-f0-9]{6,40})$", art)
            if m:
                repo = m.group(1)
                commit = m.group(2)
                name = f"{repo}_{commit[:8]}-{overlay_label}"
            else:
                name = f"artifact-{overlay_label}"

        return EngineConfig(
            name=str(name),
            artifact=art,
            build_options=bo,
            options=coerce_engine_option_map(merged if merged else {}, field_name="engines[0].options"),
            **common,
        )

    # engine_path-based
    eng_cfg = Path(resolve_path_like(str(engine_path_key)))
    if not eng_cfg.exists():
        raise FileNotFoundError(f"Engine config file not found: {eng_cfg}")

    opts: JsonObject = {}
    if overlays:
        opts.update(load_overlays(overlays))

    inline_opts = x.get("options")
    if isinstance(inline_opts, Mapping):
        opts.update(coerce_json_object_serialized(inline_opts, field_name="engines[0].options"))

    # Resolve placeholders in string values
    for k, v in list(opts.items()):
        if isinstance(v, str):
            opts[k] = resolve_path_like(v)

    # Name default
    if not name:
        name = eng_cfg.stem

    return EngineConfig(
        name=str(name),
        engine_path=eng_cfg,
        options=coerce_engine_option_map(opts, field_name="engines[0].options"),
        **common,
    )


def _build_rules_config(raw_rules: Mapping[str, JsonValue] | None) -> RulesConfig:
    """Normalize a rules mapping into a RulesConfig."""
    if raw_rules is None:
        return RulesConfig()
    rr_any = (
        dict(raw_rules) if not isinstance(raw_rules, DictConfig) else OmegaConf.to_container(raw_rules, resolve=True)
    )
    if not isinstance(rr_any, dict):
        raise TypeError("rules must be a mapping")
    tc = rr_any.get("time_control")
    if isinstance(tc, dict):
        rr_any["time_control"] = TimeControlLimits(**tc)
    return RulesConfig.model_validate(rr_any)


def _warn_unknown_keys(section: Mapping[str, JsonValue], allowed: set[str], *, label: str) -> None:
    """Emit a warning for unknown keys in a config section (non-fatal)."""
    extras = sorted(k for k in section.keys() if k not in allowed)
    if extras:
        logger.warning("Unknown keys in %s: %s", label, ", ".join(extras))


def _parse_int_rounding(raw: JsonValue | None) -> Literal["none", "stochastic"]:
    normalized = (coerce_str(raw) or "none").lower()
    if normalized == "none":
        return "none"
    if normalized == "stochastic":
        return "stochastic"
    raise ValueError("spsa.int_rounding must be 'none' or 'stochastic'")


def _parse_update_mode(raw: JsonValue | None) -> Literal["immediate", "barrier"]:
    normalized = (coerce_str(raw) or "immediate").lower()
    if normalized == "immediate":
        return "immediate"
    if normalized == "barrier":
        return "barrier"
    raise ValueError("spsa.update_mode must be 'immediate' or 'barrier'")
