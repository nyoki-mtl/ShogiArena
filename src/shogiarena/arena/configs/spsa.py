from __future__ import annotations

import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import yaml
from omegaconf import DictConfig, OmegaConf

from shogiarena.arena.configs.base import SprtConfig
from shogiarena.arena.configs.tournament import ArenaConfig, EngineSpec, RulesConfig
from shogiarena.arena.engines.time_control import TimeControlLimits
from shogiarena.utils.common import project_dirs
from shogiarena.utils.common.paths import resolve_path_like
from shogiarena.utils.common.run_paths import default_run_dir

logger = logging.getLogger(__name__)


@dataclass
class SpsaConfig:
    # Required inputs
    start_sfens_path: str
    parameters_path: str
    # Engines: exactly one entry required; baseline=tunedに同一を使用
    baseline: list[EngineSpec]
    tuned: list[EngineSpec]
    # Tournament-like rules (time_control, adjudication, etc.)
    rules: RulesConfig = field(default_factory=RulesConfig)
    # SPSA algorithm parameters
    num_updates: int = 0
    mobility: float = 1.0
    scale: float = 1.0
    # Paths and runtime
    experiment_name: str | None = None
    run_dir: str | None = None
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
    int_rounding: str = "none"
    int_ck_floor: float = 0.5
    update_mode: str = "immediate"
    early_stop: dict[str, Any] | None = None
    # Dashboard / workers
    dashboard_enabled: bool = True
    dashboard_api_port: int = 8080
    num_workers: int = 1
    ltc_regression: LtcRegressionConfig | None = None


@dataclass
class LtcPassCriteria:
    min_winrate: float | None = None
    max_elo_drop: float | None = None
    sprt: SprtConfig | None = None


@dataclass
class LtcRegressionConfig:
    enabled: bool = False
    every_n_updates: int = 0
    total_pairs: int = 0
    time_control: TimeControlLimits | None = None
    pass_criteria: LtcPassCriteria | None = None


def _get_spsa_value(node: Mapping[str, Any], name: str, default: Any) -> Any:
    """Helper to fetch a value from the spsa node with a default.

    Keeps semantics explicit and improves readability over terse helpers.
    """
    v = node.get(name)
    return v if v is not None else default


def _parse_ltc_pass_criteria(raw: Mapping[str, Any]) -> LtcPassCriteria:
    if not isinstance(raw, Mapping):
        raise TypeError("ltc_regression.pass_criteria must be a mapping")

    min_winrate = raw.get("min_winrate")
    if min_winrate is not None and not isinstance(min_winrate, int | float):
        raise TypeError("ltc_regression.pass_criteria.min_winrate must be numeric")

    max_elo_drop = raw.get("max_elo_drop")
    if max_elo_drop is not None and not isinstance(max_elo_drop, int | float):
        raise TypeError("ltc_regression.pass_criteria.max_elo_drop must be numeric")

    sprt_raw = raw.get("sprt")
    sprt: SprtConfig | None = None
    if sprt_raw is not None:
        if not isinstance(sprt_raw, Mapping):
            raise TypeError("ltc_regression.pass_criteria.sprt must be a mapping")
        sprt_dict = dict(sprt_raw)
        for key in ("elo0", "elo1", "alpha", "beta"):
            value = sprt_dict.get(key)
            if value is not None:
                if not isinstance(value, int | float):
                    raise TypeError(f"ltc_regression.pass_criteria.sprt.{key} must be numeric")
                sprt_dict[key] = float(value)
        sprt = SprtConfig(**sprt_dict)

    return LtcPassCriteria(
        min_winrate=float(min_winrate) if min_winrate is not None else None,
        max_elo_drop=float(max_elo_drop) if max_elo_drop is not None else None,
        sprt=sprt,
    )


def _parse_ltc_regression(node: Mapping[str, Any]) -> LtcRegressionConfig:
    if not isinstance(node, Mapping):
        raise TypeError("ltc_regression must be a mapping")

    enabled = bool(node.get("enabled", True))

    every_n_updates_raw = node.get("every_n_updates", 0)
    if every_n_updates_raw is None:
        every_n_updates = 0
    elif isinstance(every_n_updates_raw, int):
        every_n_updates = every_n_updates_raw
    else:
        raise TypeError("ltc_regression.every_n_updates must be an integer")

    total_pairs_raw = node.get("total_pairs", 0)
    if total_pairs_raw is None:
        total_pairs = 0
    elif isinstance(total_pairs_raw, int):
        total_pairs = total_pairs_raw
    else:
        raise TypeError("ltc_regression.total_pairs must be an integer")

    if enabled:
        if every_n_updates <= 0:
            raise ValueError("ltc_regression.every_n_updates must be positive when enabled")
        if total_pairs <= 0:
            raise ValueError("ltc_regression.total_pairs must be positive when enabled")

    tc_raw = node.get("time_control")
    time_control: TimeControlLimits | None = None
    if tc_raw is not None:
        tc_container = OmegaConf.to_container(tc_raw, resolve=True)
        if not isinstance(tc_container, Mapping):
            raise TypeError("ltc_regression.time_control must be a mapping")
        time_control = TimeControlLimits(**cast(dict[str, Any], tc_container))

    pass_criteria_raw = node.get("pass_criteria")
    pass_criteria = _parse_ltc_pass_criteria(pass_criteria_raw) if pass_criteria_raw is not None else None

    if "fail_action" in node and node.get("fail_action") is not None:
        raise ValueError("ltc_regression.fail_action is no longer supported; remove this field from the config")

    return LtcRegressionConfig(
        enabled=enabled,
        every_n_updates=every_n_updates,
        total_pairs=total_pairs,
        time_control=time_control,
        pass_criteria=pass_criteria,
    )


def _load_overlay_for_artifact(artifact: str) -> dict[str, Any]:
    return {}


def _normalize_overlays(raw: Any) -> list[Path]:
    if raw is None:
        return []
    if isinstance(raw, str):
        items = [raw]
    elif isinstance(raw, Sequence):
        items = list(raw)
    else:
        raise TypeError("options_overlays must be a string or list of strings")
    overlays: list[Path] = []
    for item in items:
        if not isinstance(item, str) or not item.strip():
            raise TypeError("options_overlays entries must be non-empty strings")
        candidate = Path(resolve_path_like(item))
        if not candidate.exists():
            raise FileNotFoundError(f"Options overlay file not found: {candidate}")
        overlays.append(candidate)
    return overlays


def _load_overlays(overlays: list[Path]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for overlay in overlays:
        raw = yaml.safe_load(overlay.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            raise TypeError("options_overlays YAML must be a mapping")
        opts = raw.get("options") if "options" in raw else raw
        if isinstance(opts, dict):
            merged.update(opts)
    return merged


def _map_engine(x: Mapping[str, Any]) -> EngineSpec:
    _warn_unknown_keys(
        x,
        allowed={
            "artifact",
            "engine_config",
            "build_options",
            "name",
            "options",
            "options_overlays",
            "time_control",
            "instance_id",
        },
        label="engines[0]",
    )
    # Exactly one of artifact or engine_config
    has_art = isinstance(x.get("artifact"), str) and str(x["artifact"]).strip() != ""
    has_cfg = isinstance(x.get("engine_config"), str) and str(x["engine_config"]).strip() != ""
    if has_art == has_cfg:
        raise ValueError("Engine must specify exactly one of 'artifact' or 'engine_config'")

    name = x.get("name")
    overlays = _normalize_overlays(x.get("options_overlays"))
    if has_art:
        art = str(x["artifact"]).strip()
        # Validate build_options.target_cpu
        bo_raw = x.get("build_options")
        if not isinstance(bo_raw, Mapping) or not str(bo_raw.get("target_cpu", "")).strip():
            raise ValueError("Artifact engines require build_options.target_cpu")
        bo = cast(dict[str, Any], dict(bo_raw))

        # Build merged options: overlay -> options_overlays -> inline options(dict)
        merged: dict[str, Any] = {}
        merged.update(_load_overlay_for_artifact(art))
        merged.update(_load_overlays(overlays))
        inline_opts = x.get("options")
        if isinstance(inline_opts, Mapping):
            merged.update(cast(dict[str, Any], inline_opts))

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

        # Do not resolve artifact here; EngineFactory will handle it
        return EngineSpec(
            name=str(name),
            artifact=art,
            build_options=bo,
            options=merged if merged else {},
            time_control=(
                TimeControlLimits(**cast(dict[str, Any], OmegaConf.to_container(x.get("time_control"), resolve=True)))
                if isinstance(x.get("time_control"), Mapping)
                else None
            ),
            options_overlays=overlays,
            instance_id=(str(x.get("instance_id")).strip() or None) if x.get("instance_id") is not None else None,
        )

    # engine_config-based
    eng_cfg = Path(resolve_path_like(str(x["engine_config"])))
    if not eng_cfg.exists():
        raise FileNotFoundError(f"Engine config file not found: {eng_cfg}")

    opts: dict[str, Any] = {}
    if overlays:
        opts.update(_load_overlays(overlays))

    inline_opts = x.get("options")
    if isinstance(inline_opts, Mapping):
        if not isinstance(opts, dict):
            opts = {}
        opts.update(cast(dict[str, Any], inline_opts))

    # Resolve placeholders in string values
    for k, v in list(opts.items()):
        if isinstance(v, str):
            opts[k] = resolve_path_like(v)

    # Name default
    if not name:
        name = eng_cfg.stem

    return EngineSpec(
        name=str(name),
        engine_config=eng_cfg,
        options=opts,
        time_control=(
            TimeControlLimits(**cast(dict[str, Any], OmegaConf.to_container(x.get("time_control"), resolve=True)))
            if isinstance(x.get("time_control"), Mapping)
            else None
        ),
        options_overlays=overlays,
        instance_id=(str(x.get("instance_id")).strip() or None) if x.get("instance_id") is not None else None,
    )


def _build_rules_config(raw_rules: Any) -> RulesConfig:
    """Normalize a rules mapping into a RulesConfig, resolving time_control into TimeControlLimits.

    Accepts either an OmegaConf node or a plain dict; returns a RulesConfig. Raises TypeError on invalid inputs.
    """
    rules_obj = RulesConfig()
    if raw_rules is None:
        return rules_obj
    rr_any = OmegaConf.to_container(raw_rules, resolve=True)
    if not isinstance(rr_any, dict):
        raise TypeError("rules must be a mapping")
    rr = cast(dict[str, Any], rr_any)
    tc = rr.get("time_control")
    if isinstance(tc, dict):
        rr["time_control"] = TimeControlLimits(**cast(dict[str, Any], tc))
    return RulesConfig(**rr)


def _warn_unknown_keys(section: Mapping[str, Any], allowed: set[str], *, label: str) -> None:
    """Emit a warning for unknown keys in a config section (non-fatal)."""
    extras = sorted(k for k in section.keys() if k not in allowed)
    if extras:
        logger.warning("Unknown keys in %s: %s", label, ", ".join(extras))


def load_config_yaml(path: str | Path) -> SpsaConfig:
    """Load an SPSA run configuration from YAML.

    Performs strict validation for the SPSA block and engines, resolves placeholder paths,
    and returns a normalized SpsaConfig with RulesConfig and TimeControlLimits objects.
    """
    config_data = cast(DictConfig, OmegaConf.load(str(path)))
    p = Path(path)
    instances_entry = config_data.get("instances") if isinstance(config_data, Mapping) else None
    resolved_instances: tuple[Path, ...] | None = None
    if instances_entry is not None:
        resolved_instances = ArenaConfig._resolve_instance_sources(instances_entry, base_dir=p.parent)
        if not resolved_instances:
            resolved_instances = None
    parent_name = p.parent.name
    explicit_exp = config_data.get("experiment_name") if isinstance(config_data, Mapping) else None
    if explicit_exp:
        exp_name = str(explicit_exp)
    else:
        exp_name = p.stem if parent_name in {"spsa"} else parent_name
        config_data["experiment_name"] = exp_name
    run_dir_path = Path(config_data.get("run_dir") or default_run_dir(p, project_dirs.output_dir / "spsa"))
    config_data["run_dir"] = str(run_dir_path)

    # Strict spsa block
    spsa_node = config_data.get("spsa")
    if not isinstance(spsa_node, Mapping):
        raise ValueError("Missing required 'spsa' block")

    # Warn on unknown keys in spsa block (non-fatal)
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
    raw_params_path = spsa_node.get("parameters_path")
    if not isinstance(raw_params_path, str) or not raw_params_path.strip():
        raise ValueError("spsa.parameters_path is required")
    raw_num_updates = spsa_node.get("num_updates")
    if not isinstance(raw_num_updates, int) or raw_num_updates <= 0:
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
    src = ip.get("source")
    if not isinstance(src, str) or not src.strip():
        raise ValueError("rules.initial_positions.source is required")
    start_sfens_path = ArenaConfig._resolve_initial_source(str(src), base_dir=Path(path).parent)

    # SPSAはpair_both固定。その他が指定されていたらエラー。
    fp = ip.get("flip_policy")
    if fp is not None and str(fp) != "pair_both":
        raise ValueError("SPSA requires rules.initial_positions.flip_policy to be 'pair_both'")

    # Engines: exactly one
    engines_node = config_data.get("engines")
    engines_py = OmegaConf.to_container(engines_node, resolve=True) if engines_node is not None else None
    if not isinstance(engines_py, list) or len(engines_py) != 1:
        raise ValueError("'engines' must be a list with exactly one engine entry for SPSA")
    engine_spec = _map_engine(cast(Mapping[str, Any], engines_py[0]))
    baseline = [engine_spec]
    tuned = [engine_spec]

    # Require tune_file only when using artifact-based engine. Propagate tune_tag into build_options.
    if baseline[0].artifact or tuned[0].artifact:
        # Require engine-level build_options.tune_file (no top-level fallback)
        tune_file: str | None = None
        if isinstance(engines_py, list) and len(engines_py) > 0 and isinstance(engines_py[0], Mapping):
            bo = cast(Mapping[str, Any] | None, engines_py[0].get("build_options"))
            if isinstance(bo, Mapping) and isinstance(bo.get("tune_file"), str):
                tune_file = cast(str, bo.get("tune_file"))
        if not tune_file:
            raise ValueError("SPSA requires engines[0].build_options.tune_file when using artifacts")
        tune_tag = Path(resolve_path_like(tune_file)).stem
        baseline[0].build_options["tune_tag"] = tune_tag
        tuned[0].build_options["tune_tag"] = tune_tag

    # Dashboard and workers
    dash = config_data.get("dashboard")
    dashboard_enabled = True
    dashboard_api_port = 8080
    num_workers = 4
    if isinstance(dash, Mapping):
        _warn_unknown_keys(dash, allowed={"enabled", "api_port"}, label="dashboard")
        if "enabled" in dash:
            dashboard_enabled = bool(dash.get("enabled"))
        if "api_port" in dash:
            _ap = dash.get("api_port")
            if _ap is not None and not isinstance(_ap, int):
                raise TypeError("dashboard.api_port must be an integer")
            if _ap is not None:
                dashboard_api_port = int(_ap)
    # Prefer spsa.num_parallel
    np = spsa_node.get("num_parallel")
    if np is not None:
        if not isinstance(np, int):
            raise TypeError("spsa.num_parallel must be an integer")
        num_workers = int(np)

    # Rules (optional)
    rules_obj = _build_rules_config(config_data.get("rules"))

    # OpenBench/SPSA knobs (strictly from spsa block; defaults applied here)
    int_rounding = str(_get_spsa_value(spsa_node, "int_rounding", "none"))
    if int_rounding not in ("none", "stochastic"):
        raise ValueError("spsa.int_rounding must be 'none' or 'stochastic'")
    update_mode = str(_get_spsa_value(spsa_node, "update_mode", "immediate"))
    if update_mode not in ("immediate", "barrier"):
        raise ValueError("spsa.update_mode must be 'immediate' or 'barrier'")

    has_a_key = isinstance(spsa_node, Mapping) and "A" in spsa_node
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
        ltc_config = _parse_ltc_regression(ltc_node)

    return SpsaConfig(
        start_sfens_path=start_sfens_path,
        parameters_path=resolve_path_like(
            str(raw_params_path),
            output_dir=project_dirs.output_dir,
            engine_dir=project_dirs.engine_dir,
        ),
        baseline=baseline,
        tuned=tuned,
        rules=rules_obj,
        num_updates=int(raw_num_updates),
        mobility=float(_get_spsa_value(spsa_node, "mobility", 1.0)),
        scale=float(_get_spsa_value(spsa_node, "scale", 1.0)),
        experiment_name=exp_name,
        run_dir=str(run_dir_path),
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
        snap_float_to_step=bool(_get_spsa_value(spsa_node, "snap_float_to_step", False)),
        crn_enabled=bool(_get_spsa_value(spsa_node, "crn_enabled", True)),
        int_rounding=int_rounding,
        int_ck_floor=float(_get_spsa_value(spsa_node, "int_ck_floor", 0.5)),
        update_mode=update_mode,
        early_stop=cast(dict[str, Any] | None, _get_spsa_value(spsa_node, "early_stop", None)),
        dashboard_enabled=dashboard_enabled,
        dashboard_api_port=dashboard_api_port,
        num_workers=num_workers,
        instances=resolved_instances,
        ltc_regression=ltc_config,
    )


__all__ = [
    "SpsaConfig",
    "LtcRegressionConfig",
    "LtcPassCriteria",
    "load_config_yaml",
]
