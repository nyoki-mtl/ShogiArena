"""Dashboard payload helpers for SPSA runner."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from shogiarena._core.contexts.game_session.adapters.engine.metadata_collector import (
    compute_engine_time_control_specs,
    engine_instance_defaults,
)
from shogiarena._core.contexts.game_session.adapters.orchestration.config_engine import EngineConfig
from shogiarena._core.contexts.game_session.adapters.orchestration.config_spsa_models import SpsaRunConfig
from shogiarena._core.contexts.game_session.application.progress.hub import DashboardServerPort
from shogiarena._core.contexts.spsa.adapters.fixed_option_preflight import (
    load_fixed_option_preflight_status,
)
from shogiarena._core.contexts.spsa.domain.spsa_models import ParamEntry
from shogiarena._core.shared.kernel.atomic_json import write_json_atomic
from shogiarena._core.shared.kernel.json_types import JsonObject


def spsa_engine_configs(config: SpsaRunConfig) -> tuple[EngineConfig, ...]:
    return tuple(config.baseline) + tuple(config.tuned)


def _build_seed_summary_payload(
    *,
    run_dir: Path,
    engines_list: list[str],
    engine_time_controls: dict[str, str],
    default_time_control: str | None,
    engine_instances: dict[str, str | None],
    engine_metadata: list[JsonObject],
    rules_payload: JsonObject,
    spsa_algorithm_config: JsonObject,
    preflight_status: JsonObject,
) -> JsonObject:
    seed_summary: JsonObject = {
        "tournament_type": "spsa",
        "mode": "spsa",
        "flip_policy": None,
        "num_engines": len(engines_list),
        "run_dir": str(run_dir),
        "leaderboard": [],
        "engines": engines_list,
        "engines_meta": engine_metadata,
        "engine_time_controls": engine_time_controls,
        "default_time_control": default_time_control,
        "engine_instances": engine_instances,
        "engine_stats": {name: {"wins": 0, "losses": 0, "draws": 0, "games": 0} for name in engines_list},
        "pair_results": {},
        "timestamp": datetime.now().isoformat(),
        "games": {
            "completed": 0,
            "total": 0,
            "cancelled": 0,
        },
        "btd": {
            "ratings": {name: {"elo": 0.0, "se": 0.0} for name in engines_list},
            "anchor": engines_list[0] if engines_list else None,
            "gamma_elo": 0.0,
            "gamma_elo_se": 0.0,
            "draw_eq": 0.5,
            "draw_eq_se": 0.0,
            "rating_cov": {name: {} for name in engines_list},
        },
        "preflight_status": preflight_status,
    }

    if rules_payload:
        seed_summary["rules"] = rules_payload
        seed_summary["initial_positions"] = rules_payload.get("initial_positions")
        seed_summary["repetition_occurrences_to_draw"] = rules_payload.get("repetition_occurrences_to_draw")
        init_pos = rules_payload.get("initial_positions")
        init_pos_flip = init_pos.get("flip_policy") if isinstance(init_pos, dict) else None
        seed_summary["flip_policy"] = rules_payload.get("flip_policy") or init_pos_flip or seed_summary["flip_policy"]
        seed_summary["tournament_config"] = {"rules": rules_payload}
        sprt_config = rules_payload.get("sprt")
        if sprt_config:
            seed_summary.setdefault("sprt", sprt_config)

    seed_summary["spsa_config"] = dict(spsa_algorithm_config)
    return seed_summary


def _build_spsa_meta_payload(
    *,
    config: SpsaRunConfig,
    num_workers: int,
    session_uuid: str,
    session_started_at_iso: str,
    params: list[ParamEntry] | None,
    experiment_initial_params: dict[str, float] | None,
    prior_experiment_initial_params: dict[str, float] | None,
    prior_sessions: list[JsonObject],
    spsa_algorithm_config: JsonObject,
    engine_time_controls: dict[str, str],
    default_time_control: str | None,
    engines_list: list[str],
    engine_instances: dict[str, str | None],
    engine_metadata: list[JsonObject],
    preflight_status: JsonObject,
) -> JsonObject:
    session_start_params: dict[str, float] = {}
    if params is not None:
        for p in params:
            session_start_params[p.name] = float(p.value)
    requested_initial_params = dict(experiment_initial_params or session_start_params)
    if prior_experiment_initial_params is not None and prior_experiment_initial_params != requested_initial_params:
        raise ValueError("SPSA experiment_initial_params conflict with the existing run projection")
    initial_params_map = dict(prior_experiment_initial_params or requested_initial_params)
    sessions = [
        session
        for session in prior_sessions
        if isinstance(session.get("session_uuid"), str) and session.get("session_uuid") != session_uuid
    ]
    sessions.append(
        {
            "session_uuid": session_uuid,
            "session_started_at": session_started_at_iso,
            "session_start_params": session_start_params,
        }
    )

    meta: JsonObject = {
        "type": "spsa",
        "experiment_name": config.experiment_name,
        "space_path": str(config.space_path),
        "start_sfens_path": str(config.start_sfens_path),
        **dict(spsa_algorithm_config),
        "num_workers": int(num_workers),
        "initial_params": initial_params_map,
        "experiment_initial_params": initial_params_map,
        "session_start_params": session_start_params,
        "sessions": sessions,
        "session_uuid": session_uuid,
        "session_started_at": session_started_at_iso,
        "preflight_status": preflight_status,
    }

    meta["engine_time_controls"] = engine_time_controls
    meta["default_time_control"] = default_time_control
    meta["engines"] = engines_list
    meta["engine_instances"] = engine_instances
    meta["engine_stats"] = {name: {"wins": 0, "losses": 0, "draws": 0, "games": 0} for name in engines_list}
    meta["engines_meta"] = engine_metadata
    return meta


def seed_spsa_initial_summary(
    *,
    run_dir: Path,
    config: SpsaRunConfig,
    num_workers: int,
    params: list[ParamEntry] | None,
    experiment_initial_params: dict[str, float] | None,
    session_uuid: str,
    session_started_at_iso: str,
    api_server: DashboardServerPort | None,
    engine_metadata: list[JsonObject],
    rules_payload: JsonObject,
    spsa_algorithm_config: JsonObject,
) -> None:
    engines_list: list[str] = []
    if config.baseline:
        engines_list.append(str(config.baseline[0].name or "baseline"))
    if config.tuned:
        engines_list.append(str(config.tuned[0].name or "tuned"))

    engines = spsa_engine_configs(config)
    engine_time_controls, default_time_control = compute_engine_time_control_specs(config.rules, engines)
    engine_instances = engine_instance_defaults(engines)
    preflight_status = load_fixed_option_preflight_status(run_dir)

    seed_summary = _build_seed_summary_payload(
        run_dir=run_dir,
        engines_list=engines_list,
        engine_time_controls=engine_time_controls,
        default_time_control=default_time_control,
        engine_instances=engine_instances,
        engine_metadata=engine_metadata,
        rules_payload=rules_payload,
        spsa_algorithm_config=spsa_algorithm_config,
        preflight_status=preflight_status,
    )

    if api_server is not None:
        api_server.broadcast_summary_update(seed_summary, source="spsa")

    spsa_dir = run_dir / "spsa"
    spsa_dir.mkdir(parents=True, exist_ok=True)
    meta_path = spsa_dir / "meta.json"
    prior_sessions: list[JsonObject] = []
    prior_experiment_initial_params: dict[str, float] | None = None
    if meta_path.is_file():
        try:
            prior_meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Existing SPSA session metadata is unreadable: {meta_path}") from exc
        if not isinstance(prior_meta, dict):
            raise RuntimeError(f"Existing SPSA session metadata is not a JSON object: {meta_path}")
        if isinstance(prior_meta, dict) and isinstance(prior_meta.get("sessions"), list):
            prior_sessions = [entry for entry in prior_meta["sessions"] if isinstance(entry, dict)]
        if isinstance(prior_meta, dict):
            raw_initial = prior_meta.get("experiment_initial_params")
            if isinstance(raw_initial, dict):
                prior_experiment_initial_params = {
                    str(name): float(value) for name, value in raw_initial.items() if isinstance(value, int | float)
                }
    meta = _build_spsa_meta_payload(
        config=config,
        num_workers=num_workers,
        session_uuid=session_uuid,
        session_started_at_iso=session_started_at_iso,
        params=params,
        experiment_initial_params=experiment_initial_params,
        prior_experiment_initial_params=prior_experiment_initial_params,
        prior_sessions=prior_sessions,
        spsa_algorithm_config=spsa_algorithm_config,
        engine_time_controls=engine_time_controls,
        default_time_control=default_time_control,
        engines_list=engines_list,
        engine_instances=engine_instances,
        engine_metadata=engine_metadata,
        preflight_status=preflight_status,
    )
    write_json_atomic(meta_path, meta)


__all__ = [
    "seed_spsa_initial_summary",
    "spsa_engine_configs",
]
