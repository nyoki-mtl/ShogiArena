"""Dashboard payload helpers for SPSA runner."""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

import rshogi.record

from shogiarena._core.contexts.game_session.adapters.engine.metadata_collector import (
    compute_engine_time_control_specs,
    engine_instance_defaults,
)
from shogiarena._core.contexts.game_session.adapters.orchestration.config_engine import EngineConfig
from shogiarena._core.contexts.game_session.adapters.orchestration.config_spsa_models import SpsaRunConfig
from shogiarena._core.contexts.game_session.application.progress.hub import DashboardServerPort
from shogiarena._core.contexts.spsa.adapters.runtime.tokens import phase_symbol, variant_token
from shogiarena._core.contexts.spsa.domain.spsa_models import ParamEntry, SpsaGamePayload
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
) -> JsonObject:
    seed_summary: JsonObject = {
        "tournamentType": "spsa",
        "mode": "spsa",
        "flipPolicy": None,
        "numEngines": len(engines_list),
        "runDir": str(run_dir),
        "leaderboard": [],
        "engines": engines_list,
        "enginesMeta": engine_metadata,
        "engineTimeControls": engine_time_controls,
        "defaultTimeControl": default_time_control,
        "engineInstances": engine_instances,
        "engineStats": {name: {"wins": 0, "losses": 0, "draws": 0, "games": 0} for name in engines_list},
        "pairResults": {},
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
    }

    if rules_payload:
        seed_summary["rules"] = rules_payload
        seed_summary["initialPositions"] = rules_payload.get("initial_positions")
        seed_summary["repetitionOccurrencesToDraw"] = rules_payload.get("repetition_occurrences_to_draw")
        init_pos = rules_payload.get("initial_positions")
        init_pos_flip = init_pos.get("flip_policy") if isinstance(init_pos, dict) else None
        seed_summary["flipPolicy"] = rules_payload.get("flip_policy") or init_pos_flip or seed_summary["flipPolicy"]
        seed_summary["tournamentConfig"] = {"rules": rules_payload}
        sprt_config = rules_payload.get("sprt")
        if sprt_config:
            seed_summary.setdefault("sprt", sprt_config)

    seed_summary["spsaConfig"] = dict(spsa_algorithm_config)
    return seed_summary


def _build_spsa_meta_payload(
    *,
    config: SpsaRunConfig,
    num_workers: int,
    session_uuid: str,
    session_started_at_iso: str,
    params: list[ParamEntry] | None,
    spsa_algorithm_config: JsonObject,
    engine_time_controls: dict[str, str],
    default_time_control: str | None,
    engines_list: list[str],
    engine_instances: dict[str, str | None],
    engine_metadata: list[JsonObject],
) -> JsonObject:
    initial_params_map: dict[str, float] = {}
    if params is not None:
        for p in params:
            initial_params_map[p.name] = float(p.value)

    meta: JsonObject = {
        "type": "spsa",
        "experiment_name": config.experiment_name,
        "space_path": str(config.space_path),
        "start_sfens_path": str(config.start_sfens_path),
        **dict(spsa_algorithm_config),
        "num_workers": int(num_workers),
        "initial_params": initial_params_map,
        "session_uuid": session_uuid,
        "session_started_at": session_started_at_iso,
    }

    meta["engine_time_controls"] = engine_time_controls
    meta["default_time_control"] = default_time_control
    meta["engines"] = engines_list
    meta["engine_instances"] = engine_instances
    meta["engine_stats"] = {name: {"wins": 0, "losses": 0, "draws": 0, "games": 0} for name in engines_list}
    meta["enginesMeta"] = engine_metadata
    return meta


def seed_spsa_initial_summary(
    *,
    run_dir: Path,
    config: SpsaRunConfig,
    num_workers: int,
    params: list[ParamEntry] | None,
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

    seed_summary = _build_seed_summary_payload(
        run_dir=run_dir,
        engines_list=engines_list,
        engine_time_controls=engine_time_controls,
        default_time_control=default_time_control,
        engine_instances=engine_instances,
        engine_metadata=engine_metadata,
        rules_payload=rules_payload,
        spsa_algorithm_config=spsa_algorithm_config,
    )

    if api_server is not None:
        api_server.broadcast_summary_update(seed_summary, source="spsa")

    spsa_dir = run_dir / "spsa"
    spsa_dir.mkdir(parents=True, exist_ok=True)
    meta = _build_spsa_meta_payload(
        config=config,
        num_workers=num_workers,
        session_uuid=session_uuid,
        session_started_at_iso=session_started_at_iso,
        params=params,
        spsa_algorithm_config=spsa_algorithm_config,
        engine_time_controls=engine_time_controls,
        default_time_control=default_time_control,
        engines_list=engines_list,
        engine_instances=engine_instances,
        engine_metadata=engine_metadata,
    )
    (spsa_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def append_spsa_event_record(
    *,
    run_dir: Path | None,
    payload: SpsaGamePayload,
    game_id: str,
    game_info: rshogi.record.GameRecord,
    session_uuid: str,
) -> None:
    if run_dir is None:
        return

    spsa_dir = run_dir / "spsa"
    spsa_dir.mkdir(parents=True, exist_ok=True)
    tuned_vid = variant_token(payload.update_idx)
    current_vid = variant_token(0)
    resolved_game_id = game_info.game_name or game_id
    black_tc = game_info.black_time_control
    white_tc = game_info.white_time_control
    tc_black = black_tc.to_spec() if black_tc is not None else None
    tc_white = white_tc.to_spec() if white_tc is not None else None
    end_time = game_info.metadata.end_date
    result = game_info.result
    variant_base = variant_token(payload.update_idx)
    variant_suffix = phase_symbol(payload.phase)
    variant_label = variant_base + variant_suffix if variant_suffix else variant_base

    event_record = {
        "event": "game_result",
        "update_idx": int(payload.update_idx),
        "winner": int(payload.winner_code),
        "tuned_as_black": bool(payload.is_tuned_as_black),
        "phase": payload.phase,
        "tuned_variant": tuned_vid,
        "baseline_variant": current_vid,
        "game_id": resolved_game_id or game_id,
        "variant_token": variant_base,
        "variant_label": variant_label,
        "black_player": game_info.metadata.black_player,
        "white_player": game_info.metadata.white_player,
        "initial_sfen": game_info.init_position_sfen,
        "num_moves": len(game_info.moves),
        "time_control_black": tc_black,
        "time_control_white": tc_white,
        "end_time": end_time,
        "game_result": result.name if result is not None else None,
        "ts": int(time.time() * 1000),
        "session_uuid": session_uuid,
        "family": payload.event_family,
        "is_ltc": payload.event_family == "ltc",
    }
    with open(spsa_dir / "events.jsonl", "a", encoding="utf-8") as handle:
        handle.write(json.dumps(event_record, ensure_ascii=False) + "\n")


__all__ = [
    "append_spsa_event_record",
    "seed_spsa_initial_summary",
    "spsa_engine_configs",
]
