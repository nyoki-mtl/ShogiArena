"""Top-level SPSA config parser and YAML loader."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from omegaconf import DictConfig, OmegaConf

from shogiarena._core.platform.settings import project_dirs
from shogiarena._core.shared.kernel.json_coercion import coerce_json_object_serialized
from shogiarena._core.shared.kernel.json_types import JsonValue
from shogiarena._core.shared.kernel.paths import resolve_path_like
from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_bool, coerce_int, coerce_str

from .config_core import (
    _coerce_instance_sources_input,
    parse_time_control_raw,
)
from .config_engine import DashboardConfig, SystemConfig
from .config_spsa_models import (
    EarlyStopConfig,
    LtcRegressionConfig,
    SpsaRunConfig,
    _DashboardPayload,
)
from .config_spsa_support import (
    _build_rules_config,
    _get_spsa_float,
    _get_spsa_int,
    _get_spsa_optional_int,
    _map_engine,
    _parse_int_rounding,
    _parse_update_mode,
    _warn_unknown_keys,
)
from .config_tournament import TournamentRunConfig


def parse_spsa_config_mapping(
    config_data: Mapping[str, object] | DictConfig,
    *,
    source_path: Path | None = None,
) -> SpsaRunConfig:
    """Load an SPSA run configuration from already-parsed mapping data."""
    if not isinstance(config_data, Mapping):
        raise TypeError("SPSA config payload must be a mapping")

    p = source_path or Path.cwd()
    if isinstance(config_data, DictConfig):
        raw_data = OmegaConf.to_container(config_data, resolve=True)
        if not isinstance(raw_data, Mapping):
            raise TypeError("SPSA config payload must be a mapping")
        config_payload = coerce_json_object_serialized(raw_data, field_name="root")
    else:
        config_payload = coerce_json_object_serialized(config_data, field_name="root")

    instances_entry = config_payload.get("instances")
    resolved_instances: tuple[Path, ...] | None = None
    if instances_entry is not None:
        parsed_instances_entry = _coerce_instance_sources_input(instances_entry, field_name="instances")
        resolved_instances = TournamentRunConfig._resolve_instance_sources(
            parsed_instances_entry,
            base_dir=p.parent,
        )
        if not resolved_instances:
            resolved_instances = None
    parent_name = p.parent.name
    explicit_exp = config_payload.get("experiment_name")
    if explicit_exp:
        exp_name = str(explicit_exp)
    else:
        exp_name = p.stem if parent_name in {"spsa"} else parent_name

    # Strict spsa block
    spsa_node = config_payload.get("spsa")
    if not isinstance(spsa_node, Mapping):
        raise ValueError("Missing required 'spsa' block")
    spsa_node_map = coerce_json_object_serialized(spsa_node, field_name="spsa")

    _warn_unknown_keys(
        spsa_node_map,
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
    raw_params_path = coerce_str(spsa_node_map.get("parameters_path"))
    if raw_params_path is None:
        raise ValueError("spsa.parameters_path is required")
    num_updates_val = coerce_int(spsa_node_map.get("num_updates"))
    if num_updates_val is None or num_updates_val <= 0:
        raise ValueError("spsa.num_updates must be a positive integer")

    # Initial positions (file only) under rules
    raw_rules_node = config_payload.get("rules")
    if not isinstance(raw_rules_node, Mapping):
        raise ValueError("rules block is required for SPSA (to specify initial_positions)")
    rules_node = coerce_json_object_serialized(raw_rules_node, field_name="rules")
    raw_initial_positions = rules_node.get("initial_positions")
    if not isinstance(raw_initial_positions, Mapping):
        raise ValueError("rules.initial_positions is required")
    ip_map = coerce_json_object_serialized(raw_initial_positions, field_name="rules.initial_positions")
    ip_type = coerce_str(ip_map.get("type"))
    if ip_type is None:
        ip_type = ""
    ip_type = ip_type.strip().lower()
    if ip_type != "file":
        raise ValueError("rules.initial_positions.type must be 'file'")
    src = coerce_str(ip_map.get("source"))
    if src is None:
        raise ValueError("rules.initial_positions.source is required")
    start_sfens_path = TournamentRunConfig._resolve_initial_source(src, base_dir=p.parent)

    # SPSAはpair_both固定
    fp = ip_map.get("flip_policy")
    if fp is not None and str(fp) != "pair_both":
        raise ValueError("SPSA requires rules.initial_positions.flip_policy to be 'pair_both'")

    # Engines: exactly one
    raw_engines_node = config_payload.get("engines")
    engines_py: list[JsonValue] | None = None
    if raw_engines_node is None:
        engines_py = None
    elif isinstance(raw_engines_node, list):
        engines_py = raw_engines_node
    else:
        engines_py = None
    if not isinstance(engines_py, list) or len(engines_py) != 1:
        raise ValueError("'engines' must be a list with exactly one engine entry for SPSA")
    engine_entry = engines_py[0]
    if not isinstance(engine_entry, Mapping):
        raise TypeError("engines[0] must be a mapping")
    engine_spec = _map_engine(coerce_json_object_serialized(engine_entry, field_name="engines[0]"))
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
    raw_dash = config_payload.get("dashboard")
    dash = coerce_json_object_serialized(raw_dash, field_name="dashboard") if isinstance(raw_dash, Mapping) else None
    dashboard_payload: _DashboardPayload = {}
    num_workers = 4
    if isinstance(dash, Mapping):
        _warn_unknown_keys(dash, allowed={"enabled", "api_port"}, label="dashboard")
        dashboard_payload["is_enabled"] = coerce_bool(dash.get("enabled", True))
        port = coerce_int(dash.get("api_port"))
        if port is not None:
            dashboard_payload["api_port"] = port
    parsed_np = coerce_int(spsa_node_map.get("num_parallel"))
    if parsed_np is not None:
        num_workers = parsed_np

    system = SystemConfig()
    system_raw = config_payload.get("system")
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

    rules_obj = _build_rules_config(rules_node)

    int_rounding = _parse_int_rounding(spsa_node_map.get("int_rounding"))
    update_mode = _parse_update_mode(spsa_node_map.get("update_mode"))

    has_a_key = "A" in spsa_node_map
    if has_a_key:
        raw_a = spsa_node_map.get("A")
        if raw_a is None:
            a_value: float | None = None
        elif isinstance(raw_a, int | float):
            a_value = float(raw_a)
        else:
            raise TypeError("spsa.A must be a number or null")
    else:
        a_value = 0.0

    ltc_config: LtcRegressionConfig | None = None
    ltc_node = spsa_node_map.get("ltc_regression")
    if ltc_node is not None:
        if not isinstance(ltc_node, Mapping):
            raise TypeError("spsa.ltc_regression must be a mapping")
        ltc_dict = dict(ltc_node)
        # Parse time_control if present
        tc_raw = ltc_dict.get("time_control")
        if tc_raw is not None:
            ltc_dict["time_control"] = parse_time_control_raw(tc_raw)
        # Parse pass_criteria if present
        pc_raw = ltc_dict.get("pass_criteria")
        if isinstance(pc_raw, Mapping):
            ltc_dict["pass_criteria"] = {str(k): v for k, v in pc_raw.items()}
        ltc_config = LtcRegressionConfig.model_validate(ltc_dict)

    early_stop_raw = spsa_node_map.get("early_stop")
    if early_stop_raw is None:
        early_stop: EarlyStopConfig | None = None
    elif isinstance(early_stop_raw, Mapping):
        early_stop = EarlyStopConfig.model_validate({str(key): value for key, value in early_stop_raw.items()})
    else:
        raise TypeError("spsa.early_stop must be a mapping or null")

    mobility = _get_spsa_float(spsa_node_map, "mobility", 1.0)
    scale = _get_spsa_float(spsa_node_map, "scale", 1.0)
    inflight_factor = _get_spsa_int(spsa_node_map, "inflight_factor", 4)
    update_batch_size = _get_spsa_optional_int(spsa_node_map, "update_batch_size")
    a0 = _get_spsa_float(spsa_node_map, "a0", mobility)
    alpha = _get_spsa_float(spsa_node_map, "alpha", 0.0)
    gamma = _get_spsa_float(spsa_node_map, "gamma", 0.0)
    int_ck_floor = _get_spsa_float(spsa_node_map, "int_ck_floor", 0.5)

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
        mobility=mobility,
        scale=scale,
        experiment_name=exp_name,
        inflight_factor=inflight_factor,
        update_batch_size=update_batch_size,
        a0=a0,
        A=a_value,
        alpha=alpha,
        gamma=gamma,
        is_snap_float_to_step=coerce_bool(spsa_node_map.get("snap_float_to_step", False)),
        is_crn_enabled=coerce_bool(spsa_node_map.get("crn_enabled", True)),
        int_rounding=int_rounding,
        int_ck_floor=int_ck_floor,
        update_mode=update_mode,
        early_stop=early_stop,
        dashboard=DashboardConfig(**dashboard_payload) if dashboard_payload else DashboardConfig(),
        system=system,
        num_workers=num_workers,
        instances=resolved_instances,
        ltc_regression=ltc_config,
    )
