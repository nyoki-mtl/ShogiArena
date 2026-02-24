from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Literal, TypedDict, TypeVar, cast

import yaml
from omegaconf import DictConfig, OmegaConf
from pydantic import BaseModel, Field, model_validator
from typing_extensions import Self

from shogiarena.arena.configs.base import SprtConfig
from shogiarena.arena.configs.errors import ConfigError
from shogiarena.arena.configs.tournament import (
    DashboardConfig,
    EngineConfig,
    RulesConfig,
    SystemConfig,
    TournamentRunConfig,
)
from shogiarena.arena.engines.time_control import TimeControlLimits
from shogiarena.utils.common import project_dirs
from shogiarena.utils.common.paths import resolve_path_like
from shogiarena.utils.types.coerce import coerce_bool, coerce_int, coerce_str, coerce_str_list

logger = logging.getLogger(__name__)
T = TypeVar("T")


class _DashboardPayload(TypedDict, total=False):
    enabled: bool
    api_port: int


class LtcPassCriteria(BaseModel):
    """LTC test pass criteria."""

    min_winrate: float | None = None
    max_elo_drop: float | None = None
    sprt: SprtConfig | None = None

    @model_validator(mode="after")
    def _validate_pass_criteria(self) -> Self:
        if self.min_winrate is not None and not (0.0 <= self.min_winrate <= 1.0):
            raise ValueError("ltc_pass_criteria.min_winrate must be between 0.0 and 1.0")
        return self


class LtcRegressionConfig(BaseModel):
    """LTC regression test configuration."""

    enabled: bool = False
    every_n_updates: int = Field(default=0, ge=0)
    total_pairs: int = Field(default=0, ge=0)
    time_control: TimeControlLimits | None = None
    pass_criteria: LtcPassCriteria | None = None

    @model_validator(mode="after")
    def _validate_ltc_regression(self) -> Self:
        if self.enabled:
            if self.every_n_updates <= 0:
                raise ValueError("ltc_regression.every_n_updates must be positive when enabled")
            if self.total_pairs <= 0:
                raise ValueError("ltc_regression.total_pairs must be positive when enabled")
        return self


class SpsaRunConfig(BaseModel):
    """SPSA run configuration."""

    # Required inputs
    start_sfens_path: str
    parameters_path: str
    # Engines: exactly one entry required; baseline=tunedに同一を使用
    baseline: list[EngineConfig]
    tuned: list[EngineConfig]
    # Tournament-like rules (time_control, adjudication, etc.)
    rules: RulesConfig = Field(default_factory=RulesConfig)
    # SPSA algorithm parameters
    num_updates: int = Field(gt=0)
    mobility: float = 1.0
    scale: float = 1.0
    # Paths and runtime
    experiment_name: str | None = None
    instances: tuple[Path, ...] | None = None
    # Async orchestration
    inflight_factor: int = 4
    update_batch_size: int | None = None
    # Gain schedules
    a0: float = 1.0
    A: float | None = 0.0
    alpha: float = 0.0
    gamma: float = 0.0
    snap_float_to_step: bool = False
    # OpenBench alignment options
    crn_enabled: bool = True
    int_rounding: Literal["none", "stochastic"] = "none"
    int_ck_floor: float = 0.5
    update_mode: Literal["immediate", "barrier"] = "immediate"
    early_stop: dict[str, object] | None = None
    # Dashboard / workers
    dashboard: DashboardConfig = Field(default_factory=DashboardConfig)
    system: SystemConfig = Field(default_factory=SystemConfig)
    num_workers: int = 1
    ltc_regression: LtcRegressionConfig | None = None


def _get_spsa_value(node: Mapping[str, object], name: str, default: T) -> T:
    """Helper to fetch a value from the spsa node with a default."""
    v = node.get(name)
    return cast(T, v if v is not None else default)


def _load_overlay_for_artifact(artifact: str) -> dict[str, object]:
    return {}


def _normalize_overlays(raw: object) -> list[Path]:
    items = coerce_str_list(raw, field="options_overlays")
    overlays: list[Path] = []
    for item in items:
        candidate = Path(resolve_path_like(item))
        if not candidate.exists():
            raise FileNotFoundError(f"Options overlay file not found: {candidate}")
        overlays.append(candidate)
    return overlays


def _load_overlays(overlays: list[Path]) -> dict[str, object]:
    merged: dict[str, object] = {}
    for overlay in overlays:
        raw = yaml.safe_load(overlay.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, Mapping):
            raise TypeError("options_overlays YAML must be a mapping")
        opts = raw.get("options") if "options" in raw else raw
        if isinstance(opts, Mapping):
            merged.update(opts)
    return merged


def _normalize_check_templates(raw: object) -> tuple[str, ...]:
    """Normalize isready_lock_check_templates into a tuple of strings."""
    return tuple(coerce_str_list(raw, field="isready_lock_check_templates"))


def _parse_time_control_raw(raw: object) -> TimeControlLimits | None:
    """OmegaConf/dict 形式の time_control を ``TimeControlLimits`` に変換する。"""
    if raw is None or not isinstance(raw, Mapping):
        return None
    container = OmegaConf.to_container(raw, resolve=True)
    if not isinstance(container, dict):
        return None
    return TimeControlLimits.model_validate(container)


def _extract_engine_common_kwargs(x: Mapping[str, object], overlays: list[Path]) -> dict[str, object]:
    """artifact / engine_path 両ブランチで共通する EngineConfig kwargs を抽出する。"""
    instance_id_raw = x.get("instance_id")
    return {
        "mate_default_ply_limit": x.get("mate_default_ply_limit"),
        "mate_default_node_limit": x.get("mate_default_node_limit"),
        "mate_default_infinite": coerce_bool(x.get("mate_default_infinite", False)),
        "mate_wait_for_bestmove": coerce_bool(x.get("mate_wait_for_bestmove", False)),
        "isready_sync_strategy": x.get("isready_sync_strategy", "direct"),
        "isready_lock_key": x.get("isready_lock_key"),
        "isready_lock_template": x.get("isready_lock_template"),
        "isready_lock_check_key": x.get("isready_lock_check_key"),
        "isready_lock_check_template": x.get("isready_lock_check_template"),
        "isready_lock_check_templates": _normalize_check_templates(x.get("isready_lock_check_templates")),
        "isready_lock_skip_if_exists": coerce_bool(x.get("isready_lock_skip_if_exists", False)),
        "time_control": _parse_time_control_raw(x.get("time_control")),
        "options_overlays": overlays,
        "instance_id": (str(instance_id_raw).strip() or None) if instance_id_raw is not None else None,
    }


def _map_engine(x: Mapping[str, object]) -> EngineConfig:
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
    overlays = _normalize_overlays(x.get("options_overlays"))
    common = _extract_engine_common_kwargs(x, overlays)

    if has_art:
        art = str(x["artifact"]).strip()
        bo_raw = x.get("build_options")
        bo = dict(bo_raw) if isinstance(bo_raw, Mapping) else {}

        # Build merged options: overlay -> options_overlays -> inline options(dict)
        merged: dict[str, object] = {}
        merged.update(_load_overlay_for_artifact(art))
        merged.update(_load_overlays(overlays))
        inline_opts = x.get("options")
        if isinstance(inline_opts, Mapping):
            merged.update({str(k): v for k, v in inline_opts.items()})

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
            options=merged if merged else {},
            **common,  # type: ignore[arg-type]  # validators で型検証
        )

    # engine_path-based (engine_config is deprecated alias)
    eng_cfg = Path(resolve_path_like(str(engine_path_key)))
    if not eng_cfg.exists():
        raise FileNotFoundError(f"Engine config file not found: {eng_cfg}")

    opts: dict[str, object] = {}
    if overlays:
        opts.update(_load_overlays(overlays))

    inline_opts = x.get("options")
    if isinstance(inline_opts, Mapping):
        opts.update({str(k): v for k, v in inline_opts.items()})

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
        options=opts,
        **common,  # type: ignore[arg-type]  # validators で型検証
    )


def _build_rules_config(raw_rules: Mapping[str, object] | None) -> RulesConfig:
    """Normalize a rules mapping into a RulesConfig."""
    if raw_rules is None:
        return RulesConfig()
    rr_any = OmegaConf.to_container(raw_rules, resolve=True)
    if not isinstance(rr_any, dict):
        raise TypeError("rules must be a mapping")
    tc = rr_any.get("time_control")
    if isinstance(tc, dict):
        rr_any["time_control"] = TimeControlLimits(**tc)
    return RulesConfig.model_validate(rr_any)


def _warn_unknown_keys(section: Mapping[str, object], allowed: set[str], *, label: str) -> None:
    """Emit a warning for unknown keys in a config section (non-fatal)."""
    extras = sorted(k for k in section.keys() if k not in allowed)
    if extras:
        logger.warning("Unknown keys in %s: %s", label, ", ".join(extras))


def _load_config_yaml_impl(path: str | Path) -> SpsaRunConfig:
    """Load an SPSA run configuration from YAML."""
    config_data = cast(DictConfig, OmegaConf.load(str(path)))
    p = Path(path)
    instances_entry = config_data.get("instances")
    resolved_instances: tuple[Path, ...] | None = None
    if instances_entry is not None:
        resolved_instances = TournamentRunConfig._resolve_instance_sources(instances_entry, base_dir=p.parent)
        if not resolved_instances:
            resolved_instances = None
    parent_name = p.parent.name
    explicit_exp = config_data.get("experiment_name")
    if explicit_exp:
        exp_name = str(explicit_exp)
    else:
        exp_name = p.stem if parent_name in {"spsa"} else parent_name
        config_data["experiment_name"] = exp_name
    if config_data.get("run_dir"):
        raise ValueError("SpsaConfig.run_dir is deprecated; supply a RunStorage instead")

    # Strict spsa block
    spsa_node = config_data.get("spsa")
    if not isinstance(spsa_node, Mapping):
        raise ValueError("Missing required 'spsa' block")

    _warn_unknown_keys(
        spsa_node,
        allowed={
            "parameters_path",
            "num_updates",
            "mobility",
            "scale",
            "inflight_factor",
            "update_batch_size",
            "a0",
            "A",
            "alpha",
            "gamma",
            "snap_float_to_step",
            "crn_enabled",
            "int_rounding",
            "int_ck_floor",
            "update_mode",
            "early_stop",
            "num_parallel",
            "ltc_regression",
        },
        label="spsa",
    )

    # Required params
    raw_params_path = coerce_str(spsa_node.get("parameters_path"))
    if raw_params_path is None:
        raise ValueError("spsa.parameters_path is required")
    num_updates_val = coerce_int(spsa_node.get("num_updates"))
    if num_updates_val is None or num_updates_val <= 0:
        raise ValueError("spsa.num_updates must be a positive integer")

    # Initial positions (file only) under rules
    rules_node = config_data.get("rules")
    if not isinstance(rules_node, Mapping):
        raise ValueError("rules block is required for SPSA (to specify initial_positions)")
    ip = rules_node.get("initial_positions")
    if not isinstance(ip, Mapping):
        raise ValueError("rules.initial_positions is required")
    ip_type = str(ip.get("type", "")).strip().lower()
    if ip_type != "file":
        raise ValueError("rules.initial_positions.type must be 'file'")
    src = coerce_str(ip.get("source"))
    if src is None:
        raise ValueError("rules.initial_positions.source is required")
    start_sfens_path = TournamentRunConfig._resolve_initial_source(src, base_dir=Path(path).parent)

    # SPSAはpair_both固定
    fp = ip.get("flip_policy")
    if fp is not None and str(fp) != "pair_both":
        raise ValueError("SPSA requires rules.initial_positions.flip_policy to be 'pair_both'")

    # Engines: exactly one
    engines_node = config_data.get("engines")
    engines_py = OmegaConf.to_container(engines_node, resolve=True) if engines_node is not None else None
    if not isinstance(engines_py, list) or len(engines_py) != 1:
        raise ValueError("'engines' must be a list with exactly one engine entry for SPSA")
    engine_entry = engines_py[0]
    if not isinstance(engine_entry, Mapping):
        raise TypeError("engines[0] must be a mapping")
    engine_spec = _map_engine(engine_entry)
    baseline = [engine_spec]
    tuned = [engine_spec]

    # Require tune_file only when using artifact-based engine
    if baseline[0].artifact or tuned[0].artifact:
        bo = engine_entry.get("build_options")
        tune_file = coerce_str(bo.get("tune_file") if isinstance(bo, Mapping) else None)
        if tune_file is None:
            raise ValueError("SPSA requires engines[0].build_options.tune_file when using artifacts")
        tune_tag = Path(resolve_path_like(tune_file)).stem
        baseline[0].build_options["tune_tag"] = tune_tag
        tuned[0].build_options["tune_tag"] = tune_tag

    # Dashboard and workers
    dash = config_data.get("dashboard")
    dashboard_payload: _DashboardPayload = {}
    num_workers = 4
    if isinstance(dash, Mapping):
        _warn_unknown_keys(dash, allowed={"enabled", "api_port"}, label="dashboard")
        dashboard_payload["enabled"] = coerce_bool(dash.get("enabled", True))
        port = coerce_int(dash.get("api_port"))
        if port is not None:
            dashboard_payload["api_port"] = port
    parsed_np = coerce_int(spsa_node.get("num_parallel"))
    if parsed_np is not None:
        num_workers = parsed_np

    system = SystemConfig()
    system_raw = config_data.get("system")
    if isinstance(system_raw, Mapping):
        allowed_keys = {
            "resource_poll_interval",
            "resource_poll_max_interval",
            "engine_handshake_timeout",
            "extras",
        }
        raw_system = dict(system_raw)
        payload = {k: raw_system[k] for k in raw_system.keys() if k in allowed_keys}
        extras = {k: raw_system[k] for k in raw_system.keys() if k not in allowed_keys}
        if extras:
            payload["extras"] = extras
        system = SystemConfig(**payload)

    rules_obj = _build_rules_config(config_data.get("rules"))

    int_rounding = str(_get_spsa_value(spsa_node, "int_rounding", "none"))
    if int_rounding not in ("none", "stochastic"):
        raise ValueError("spsa.int_rounding must be 'none' or 'stochastic'")
    update_mode = str(_get_spsa_value(spsa_node, "update_mode", "immediate"))
    if update_mode not in ("immediate", "barrier"):
        raise ValueError("spsa.update_mode must be 'immediate' or 'barrier'")

    has_a_key = "A" in spsa_node
    if has_a_key:
        raw_a = spsa_node.get("A")
        if raw_a is None:
            a_value: float | None = None
        elif isinstance(raw_a, int | float):
            a_value = float(raw_a)
        else:
            raise TypeError("spsa.A must be a number or null")
    else:
        a_value = 0.0

    ltc_config: LtcRegressionConfig | None = None
    ltc_node = spsa_node.get("ltc_regression")
    if ltc_node is not None:
        if not isinstance(ltc_node, Mapping):
            raise TypeError("spsa.ltc_regression must be a mapping")
        ltc_dict = dict(ltc_node)
        # Parse time_control if present
        tc_raw = ltc_dict.get("time_control")
        if tc_raw is not None:
            ltc_dict["time_control"] = _parse_time_control_raw(tc_raw)
        # Parse pass_criteria if present
        pc_raw = ltc_dict.get("pass_criteria")
        if isinstance(pc_raw, Mapping):
            ltc_dict["pass_criteria"] = {str(k): v for k, v in pc_raw.items()}
        if "fail_action" in ltc_dict and ltc_dict.get("fail_action") is not None:
            raise ValueError("ltc_regression.fail_action is no longer supported; remove this field from the config")
        ltc_dict.pop("fail_action", None)
        ltc_config = LtcRegressionConfig.model_validate(ltc_dict)

    return SpsaRunConfig(
        start_sfens_path=start_sfens_path,
        parameters_path=resolve_path_like(
            str(raw_params_path),
            output_dir=project_dirs.output_dir,
            engine_dir=project_dirs.engine_dir,
        ),
        baseline=baseline,
        tuned=tuned,
        rules=rules_obj,
        num_updates=num_updates_val,
        mobility=float(_get_spsa_value(spsa_node, "mobility", 1.0)),
        scale=float(_get_spsa_value(spsa_node, "scale", 1.0)),
        experiment_name=exp_name,
        inflight_factor=int(_get_spsa_value(spsa_node, "inflight_factor", 4)),
        update_batch_size=(
            int(_get_spsa_value(spsa_node, "update_batch_size", 0))
            if _get_spsa_value(spsa_node, "update_batch_size", None) is not None
            else None
        ),
        a0=float(_get_spsa_value(spsa_node, "a0", _get_spsa_value(spsa_node, "mobility", 1.0))),
        A=a_value,
        alpha=float(_get_spsa_value(spsa_node, "alpha", 0.0)),
        gamma=float(_get_spsa_value(spsa_node, "gamma", 0.0)),
        snap_float_to_step=coerce_bool(_get_spsa_value(spsa_node, "snap_float_to_step", False)),
        crn_enabled=coerce_bool(_get_spsa_value(spsa_node, "crn_enabled", True)),
        int_rounding=cast(Literal["none", "stochastic"], int_rounding),
        int_ck_floor=float(_get_spsa_value(spsa_node, "int_ck_floor", 0.5)),
        update_mode=cast(Literal["immediate", "barrier"], update_mode),
        early_stop=_get_spsa_value(spsa_node, "early_stop", None),
        dashboard=DashboardConfig(**dashboard_payload) if dashboard_payload else DashboardConfig(),
        system=system,
        num_workers=num_workers,
        instances=resolved_instances,
        ltc_regression=ltc_config,
    )


def load_config_yaml(path: str | Path) -> SpsaRunConfig:
    """Load an SPSA run configuration from YAML with error normalization."""
    try:
        return _load_config_yaml_impl(path)
    except (TypeError, ValueError, OSError) as exc:
        raise ConfigError(f"Invalid SPSA config {path}: {exc}") from exc


__all__ = [
    "SpsaRunConfig",
    "LtcRegressionConfig",
    "LtcPassCriteria",
    "load_config_yaml",
]
