"""Top-level SPSA config parser and YAML loader."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from omegaconf import DictConfig, OmegaConf

from shogiarena._core.platform.settings import project_dirs
from shogiarena._core.shared.kernel.json_coercion import coerce_json_object_serialized
from shogiarena._core.shared.kernel.json_types import JsonValue
from shogiarena._core.shared.kernel.paths import resolve_path_like
from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_bool, coerce_float, coerce_int, coerce_str

from .config_core import (
    _coerce_instance_sources_input,
    parse_time_control_raw,
)
from .config_engine import DashboardConfig, SystemConfig
from .config_spsa_models import (
    EarlyStopConfig,
    LtcRegressionConfig,
    SpsaAlgorithmAConfig,
    SpsaAlgorithmBlock,
    SpsaRunConfig,
    SpsaVariantApplyConfig,
    SpsaVariantsConfig,
    _DashboardPayload,
)
from .config_spsa_support import (
    _build_rules_config,
    _coerce_spsa_integral_int,
    _get_spsa_float,
    _get_spsa_int,
    _map_engine,
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

    _reject_unknown_keys(
        spsa_node_map,
        allowed={
            "space",
            "run_seed",
            "num_updates",
            "pairs_per_update",
            "algorithm",
            "variants",
            "inflight_factor",
            "snap_float_to_step",
            "int_ck_floor",
            "early_stop",
            "num_parallel",
            "ltc_regression",
            "derived_json_min_interval_s",
        },
        label="spsa",
    )

    # Required params
    raw_space_path = coerce_str(spsa_node_map.get("space"))
    if raw_space_path is None:
        raise ValueError("spsa.space is required")
    num_updates_val = _coerce_spsa_integral_int(spsa_node_map.get("num_updates"))
    if num_updates_val is None or num_updates_val <= 0:
        raise ValueError("spsa.num_updates must be a positive integer")
    pairs_per_update = _get_spsa_int(spsa_node_map, "pairs_per_update", 1)
    if pairs_per_update <= 0:
        raise ValueError("spsa.pairs_per_update must be a positive integer")

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

    # Dashboard and workers
    raw_dash = config_payload.get("dashboard")
    dash = coerce_json_object_serialized(raw_dash, field_name="dashboard") if isinstance(raw_dash, Mapping) else None
    dashboard_payload: _DashboardPayload = {}
    num_workers = 4
    if isinstance(dash, Mapping):
        _warn_unknown_keys(dash, allowed={"enabled", "api_port", "api_host"}, label="dashboard")
        dashboard_payload["is_enabled"] = coerce_bool(dash.get("enabled", True))
        port = coerce_int(dash.get("api_port"))
        if port is not None:
            dashboard_payload["api_port"] = port
        api_host = dash.get("api_host")
        if isinstance(api_host, str) and api_host.strip():
            dashboard_payload["api_host"] = api_host.strip()
    raw_np = spsa_node_map.get("num_parallel")
    parsed_np = _coerce_spsa_integral_int(raw_np)
    if raw_np is not None:
        if parsed_np is None:
            raise ValueError("spsa.num_parallel must be a positive integer")
        if parsed_np < 1:
            raise ValueError("spsa.num_parallel must be a positive integer")
        num_workers = parsed_np

    system = SystemConfig()
    system_raw = config_payload.get("system")
    if isinstance(system_raw, Mapping):
        allowed_keys = {
            "resource_poll_interval",
            "resource_poll_max_interval",
            "engine_handshake_timeout",
            "path_preflight",
            "resource_capacity_preflight",
            "instance_scheduling",
            "extras",
        }
        raw_system = dict(system_raw)
        payload = {k: raw_system[k] for k in raw_system.keys() if k in allowed_keys}
        extras = {k: raw_system[k] for k in raw_system.keys() if k not in allowed_keys}
        if extras:
            payload["extras"] = extras
        system = SystemConfig(**payload)

    rules_obj = _build_rules_config(rules_node)

    algorithm = _parse_algorithm_block(spsa_node_map.get("algorithm"))
    variants = _parse_variants_block(spsa_node_map.get("variants"))

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

    inflight_factor = _get_spsa_int(spsa_node_map, "inflight_factor", 4)
    if inflight_factor < 1:
        raise ValueError("spsa.inflight_factor must be a positive integer")
    int_ck_floor = _get_spsa_float(spsa_node_map, "int_ck_floor", 0.5)
    raw_derived_json_interval = spsa_node_map.get("derived_json_min_interval_s")
    derived_json_min_interval_s = (
        None
        if raw_derived_json_interval is None
        else _parse_float_field(
            raw_derived_json_interval,
            field="spsa.derived_json_min_interval_s",
            default=0.0,
        )
    )
    space_path = Path(
        resolve_path_like(
            str(raw_space_path),
            output_dir=project_dirs.output_dir,
            engine_dir=project_dirs.engine_dir,
        )
    )
    if not space_path.is_absolute():
        space_path = p.parent / space_path

    return SpsaRunConfig(
        start_sfens_path=start_sfens_path,
        space_path=str(space_path.resolve()),
        baseline=baseline,
        tuned=tuned,
        rules=rules_obj,
        num_updates=num_updates_val,
        pairs_per_update=pairs_per_update,
        algorithm=algorithm,
        variants=variants,
        scale=1.0,
        experiment_name=exp_name,
        run_seed=coerce_str(spsa_node_map.get("run_seed")),
        inflight_factor=inflight_factor,
        update_batch_size=pairs_per_update,
        derived_json_min_interval_s=derived_json_min_interval_s,
        is_snap_float_to_step=coerce_bool(spsa_node_map.get("snap_float_to_step", False)),
        int_ck_floor=int_ck_floor,
        early_stop=early_stop,
        dashboard=DashboardConfig(**dashboard_payload) if dashboard_payload else DashboardConfig(),
        system=system,
        num_workers=num_workers,
        instances=resolved_instances,
        ltc_regression=ltc_config,
    )


def _parse_algorithm_block(raw: object) -> SpsaAlgorithmBlock:
    if raw is None:
        return SpsaAlgorithmBlock()
    if not isinstance(raw, Mapping):
        raise TypeError("spsa.algorithm must be a mapping")
    payload = coerce_json_object_serialized(raw, field_name="spsa.algorithm")
    _reject_unknown_keys(payload, allowed={"name", "alpha", "gamma", "A"}, label="spsa.algorithm")
    a_payload: object = payload.get("A", {})
    if isinstance(a_payload, int | float):
        a_config = SpsaAlgorithmAConfig(mode="absolute", value=float(a_payload))
    elif isinstance(a_payload, Mapping):
        a_config = SpsaAlgorithmAConfig.model_validate(dict(a_payload))
    else:
        raise TypeError("spsa.algorithm.A must be a number or mapping")
    return SpsaAlgorithmBlock(
        name=_parse_algorithm_name(payload.get("name")),
        alpha=_parse_float_field(payload.get("alpha"), field="spsa.algorithm.alpha", default=0.602),
        gamma=_parse_float_field(payload.get("gamma"), field="spsa.algorithm.gamma", default=0.101),
        A=a_config,
    )


def _parse_variants_block(raw: object) -> SpsaVariantsConfig:
    if raw is None:
        return SpsaVariantsConfig()
    if not isinstance(raw, Mapping):
        raise TypeError("spsa.variants must be a mapping")
    payload = coerce_json_object_serialized(raw, field_name="spsa.variants")
    _reject_unknown_keys(
        payload,
        allowed={"pairing", "crn", "integer_rounding", "apply"},
        label="spsa.variants",
    )
    apply_raw = payload.get("apply")
    apply_config = SpsaVariantApplyConfig()
    if isinstance(apply_raw, Mapping):
        apply_config = SpsaVariantApplyConfig.model_validate(dict(apply_raw))
    elif apply_raw is not None:
        raise TypeError("spsa.variants.apply must be a mapping")
    return SpsaVariantsConfig(
        pairing=_parse_pairing(payload.get("pairing")),
        is_crn_enabled=_parse_bool_field(payload.get("crn"), field="spsa.variants.crn", default=True),
        integer_rounding=_parse_integer_rounding(payload.get("integer_rounding")),
        apply=apply_config,
    )


def _parse_float_field(value: JsonValue | None, *, field: str, default: float) -> float:
    parsed = default if value is None else coerce_float(value)
    if parsed is None:
        raise ValueError(f"{field} must be a finite number")
    return float(parsed)


def _parse_bool_field(value: JsonValue | None, *, field: str, default: bool) -> bool:
    parsed = default if value is None else coerce_bool(value)
    if parsed is None:
        raise ValueError(f"{field} must be a boolean")
    return bool(parsed)


def _parse_algorithm_name(value: JsonValue | None) -> Literal["classic"]:
    parsed = coerce_str(value) or "classic"
    if parsed != "classic":
        raise ValueError("spsa.algorithm.name must be 'classic'")
    return "classic"


def _parse_pairing(value: JsonValue | None) -> Literal["plus_minus"]:
    parsed = coerce_str(value) or "plus_minus"
    if parsed != "plus_minus":
        raise ValueError("spsa.variants.pairing must be 'plus_minus'")
    return "plus_minus"


def _parse_integer_rounding(value: JsonValue | None) -> Literal["none", "stochastic"]:
    parsed = coerce_str(value) or "stochastic"
    if parsed == "none":
        return "none"
    if parsed == "stochastic":
        return "stochastic"
    raise ValueError("spsa.variants.integer_rounding must be 'none' or 'stochastic'")


def _reject_unknown_keys(section: Mapping[str, JsonValue], allowed: set[str], *, label: str) -> None:
    extras = sorted(key for key in section if key not in allowed)
    if extras:
        raise ValueError(f"Unknown keys in {label}: {', '.join(extras)}")
