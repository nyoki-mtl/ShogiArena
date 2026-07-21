"""SPSA runtime persistence helpers for events, index, and LTC results."""

from __future__ import annotations

import json
import time
from datetime import UTC
from pathlib import Path

from shogiarena._core.shared.kernel.atomic_json import write_json_atomic
from shogiarena._core.shared.kernel.boundary_parsers.runner_state_payloads.parsers import (
    parse_spsa_index_boundary,
    parse_spsa_run_state_boundary,
)
from shogiarena._core.shared.kernel.json_types import JsonObject, JsonValue
from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_int
from shogiarena._core.shared.kernel.serialization import json_serialize


def _datetime_iso_now() -> str:
    from datetime import datetime

    return datetime.now(UTC).isoformat()


def _ensure_spsa_dir(run_dir: Path) -> Path:
    spsa_dir = run_dir / "spsa"
    spsa_dir.mkdir(parents=True, exist_ok=True)
    return spsa_dir


def append_event(run_dir: Path, session_uuid: str, payload: dict[str, JsonValue]) -> None:
    data: JsonObject = {str(key): value for key, value in payload.items()}
    data.setdefault("session_uuid", session_uuid)
    data.setdefault("ts", int(time.time() * 1000))

    events_path = _ensure_spsa_dir(run_dir) / "events.jsonl"
    with open(events_path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(data, ensure_ascii=False) + "\n")


def _load_spsa_index(index_path: Path) -> dict[str, JsonValue]:
    if not index_path.exists():
        return parse_spsa_index_boundary({}, path=str(index_path))

    with open(index_path, encoding="utf-8") as handle:
        raw = json.load(handle)
    if not isinstance(raw, dict):
        raise ValueError(f"{index_path} must contain a JSON object")
    return parse_spsa_index_boundary(raw, path=str(index_path))


def update_index_json(
    run_dir: Path,
    config: object,
    *,
    update_idx: int,
    params: dict[str, float],
    s_plus: float,
    s_minus: float,
    step: float,
    gradients: dict[str, float],
    deltas: dict[str, float],
    delta_norm: float,
    batch_size: int,
    total_games: int,
    timestamp: int,
    a_k: float,
    c_k: float,
    iteration_k: int,
    perturbations: JsonObject | None,
    extra_fields: dict[str, JsonValue] | None = None,
) -> None:
    spsa_dir = _ensure_spsa_dir(run_dir)
    index_path = spsa_dir / "index.json"

    update_data = _load_spsa_index(index_path)
    update_entry: JsonObject = {
        "update_idx": update_idx,
        "timestamp": timestamp,
        "params": params,
        "s_plus": s_plus,
        "s_minus": s_minus,
        "step": step,
        "gradients": gradients,
        "deltas": deltas,
        "delta_norm": delta_norm,
        "batch_size": batch_size,
        "total_games": total_games,
        "a_k": a_k,
        "c_k": c_k,
        "iteration_k": iteration_k,
        "perturbations": perturbations or {},
    }
    if extra_fields:
        update_entry.update({str(key): value for key, value in extra_fields.items()})

    updates_raw = update_data.get("updates")
    updates_list: list[JsonObject] = []
    if isinstance(updates_raw, list):
        for entry in updates_raw:
            if isinstance(entry, dict):
                normalized_entry = json_serialize(entry)
                if isinstance(normalized_entry, dict):
                    updates_list.append({str(key): value for key, value in normalized_entry.items()})

    for i, existing in enumerate(updates_list):
        if existing.get("update_idx") == update_idx:
            updates_list[i] = update_entry
            break
    else:
        updates_list.append(update_entry)

    update_data["updates"] = updates_list

    metadata_raw = update_data.get("metadata")
    metadata: JsonObject = {}
    if isinstance(metadata_raw, dict):
        normalized_metadata = json_serialize(metadata_raw)
        if isinstance(normalized_metadata, dict):
            metadata = {str(key): value for key, value in normalized_metadata.items()}
    update_data["metadata"] = metadata

    metadata["last_update_idx"] = max(coerce_int(metadata.get("last_update_idx")) or -1, update_idx)
    metadata["total_updates"] = len(updates_list)
    metadata["last_updated"] = timestamp
    config_map: JsonObject = {}
    normalized_config = json_serialize(config)
    if isinstance(normalized_config, dict):
        config_map = {str(key): value for key, value in normalized_config.items()}
    metadata["int_rounding_policy"] = config_map.get("int_rounding")
    metadata["crn_used"] = config_map.get("crn_enabled")
    metadata["update_mode"] = config_map.get("update_mode")

    write_json_atomic(index_path, update_data)

    state_path = run_dir / "state.json"
    if not state_path.exists():
        raise ValueError("state.json missing for SPSA run")
    with open(state_path, encoding="utf-8") as handle:
        state_raw = json.load(handle)
    if not isinstance(state_raw, dict):
        raise ValueError("state.json must contain a JSON object")
    parsed_state = parse_spsa_run_state_boundary(state_raw, path=str(state_path))

    completed_updates = max(coerce_int(parsed_state.get("completed_updates")) or 0, update_idx)
    total_updates = coerce_int(getattr(config, "num_updates", 0)) or 0
    next_state: JsonObject = {str(key): value for key, value in parsed_state.items()}
    next_state["updated_at"] = _datetime_iso_now()
    next_state["completed_updates"] = completed_updates
    next_state["total_updates"] = total_updates
    next_state["is_finished"] = total_updates > 0 and completed_updates >= total_updates

    write_json_atomic(state_path, next_state)


def record_ltc_result(
    *,
    run_dir: Path,
    session_uuid: str,
    results_path: Path | None,
    record: dict[str, JsonValue],
) -> None:
    if results_path is None:
        return

    out_record: JsonObject = {str(key): value for key, value in record.items()}
    out_record.setdefault("ts", int(time.time() * 1000))
    out_record.setdefault("session_uuid", session_uuid)

    results_path.parent.mkdir(parents=True, exist_ok=True)
    with open(results_path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(out_record, ensure_ascii=False) + "\n")

    index_path = _ensure_spsa_dir(run_dir) / "index.json"
    index_data = _load_spsa_index(index_path)

    metadata_raw = index_data.get("metadata")
    metadata: JsonObject = {}
    if isinstance(metadata_raw, dict):
        normalized_metadata = json_serialize(metadata_raw)
        if isinstance(normalized_metadata, dict):
            metadata = {str(key): value for key, value in normalized_metadata.items()}
    index_data["metadata"] = metadata

    metadata["ltc_regression"] = {
        "last_update_idx": out_record.get("update_idx"),
        "status": out_record.get("status"),
        "winrate": out_record.get("winrate"),
        "elo": out_record.get("elo"),
        "pairs_played": out_record.get("pairs_played"),
        "tuned_wins": out_record.get("tuned_wins"),
        "baseline_wins": out_record.get("baseline_wins"),
        "draws": out_record.get("draws"),
        "total_games": out_record.get("total_games"),
        "timestamp": out_record.get("ts"),
        "baseline_update_idx": out_record.get("baseline_update_idx"),
        "baseline_variant_token": out_record.get("baseline_variant_token"),
        "tuned_variant_token": out_record.get("tuned_variant_token"),
        "is_accepted": out_record.get("is_accepted"),
        "sprt": out_record.get("sprt"),
        "sprt_decision": out_record.get("sprt_decision"),
    }

    write_json_atomic(index_path, index_data)


__all__ = ["append_event", "record_ltc_result", "update_index_json"]
