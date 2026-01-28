from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from shogiarena.arena.configs.spsa import SpsaConfig

logger = logging.getLogger(__name__)


def _ensure_spsa_dir(run_dir: Path) -> Path:
    spsa_dir = run_dir / "spsa"
    spsa_dir.mkdir(parents=True, exist_ok=True)
    return spsa_dir


def append_event(run_dir: Path, session_uuid: str, payload: dict[str, Any]) -> None:
    data = dict(payload)
    data.setdefault("session_uuid", session_uuid)
    data.setdefault("ts", int(time.time() * 1000))

    events_path = _ensure_spsa_dir(run_dir) / "events.jsonl"
    with open(events_path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(data, ensure_ascii=False) + "\n")


def update_index_json(
    run_dir: Path,
    config: SpsaConfig,
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
    perturbations: dict[str, Any] | None,
    extra_fields: dict[str, Any] | None = None,
) -> None:
    spsa_dir = _ensure_spsa_dir(run_dir)
    index_path = spsa_dir / "index.json"

    index_data: dict[str, Any] = {"updates": [], "metadata": {"last_update_idx": -1}}
    if index_path.exists():
        with open(index_path, encoding="utf-8") as f:
            loaded_data = json.load(f)
        if not isinstance(loaded_data, dict):
            raise ValueError("index.json must contain a JSON object")
        index_data = loaded_data

    update_entry = {
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
        update_entry.update(extra_fields)

    updates_raw = index_data.get("updates", [])
    updates_list: list[dict[str, Any]] = []
    if isinstance(updates_raw, list):
        updates_list = [u for u in updates_raw if isinstance(u, dict)]

    for i, existing in enumerate(updates_list):
        if existing.get("update_idx") == update_idx:
            updates_list[i] = update_entry
            break
    else:
        updates_list.append(update_entry)

    index_data["updates"] = updates_list
    metadata_raw = index_data.setdefault("metadata", {})
    metadata: dict[str, Any] = metadata_raw if isinstance(metadata_raw, dict) else {}
    index_data["metadata"] = metadata

    metadata["last_update_idx"] = max(metadata.get("last_update_idx", -1), update_idx)
    metadata["total_updates"] = len(updates_list)
    metadata["last_updated"] = timestamp
    metadata["int_rounding_policy"] = config.int_rounding
    metadata["crn_used"] = config.crn_enabled
    metadata["update_mode"] = config.update_mode

    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(index_data, f, ensure_ascii=False, indent=2)

    state_path = run_dir / "run_state.json"
    if not state_path.exists():
        raise ValueError("run_state.json missing for SPSA run")
    with open(state_path, encoding="utf-8") as f:
        state_raw = json.load(f)
    if not isinstance(state_raw, dict):
        raise ValueError("run_state.json must contain a JSON object")
    completed_updates = max(int(state_raw.get("completed_updates", 0)), update_idx + 1)
    total_updates = int(getattr(config, "num_updates", 0) or 0)
    state_raw["updated_at"] = datetime.now(timezone.utc).isoformat()
    state_raw["completed_updates"] = completed_updates
    state_raw["total_updates"] = total_updates
    state_raw["finished"] = bool(total_updates > 0 and completed_updates >= total_updates)
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(state_raw, f, ensure_ascii=False, indent=2)


def record_ltc_result(
    *,
    run_dir: Path,
    session_uuid: str,
    results_path: Path | None,
    record: dict[str, Any],
) -> None:
    if results_path is None:
        return

    out_record = dict(record)
    out_record.setdefault("ts", int(time.time() * 1000))
    out_record.setdefault("session_uuid", session_uuid)

    results_path.parent.mkdir(parents=True, exist_ok=True)
    with open(results_path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(out_record, ensure_ascii=False) + "\n")

    index_path = _ensure_spsa_dir(run_dir) / "index.json"
    index_data: dict[str, Any] = {"updates": [], "metadata": {}}
    if index_path.exists():
        try:
            with open(index_path, encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                index_data = loaded
        except json.JSONDecodeError:
            logger.warning("Failed to parse existing index.json for LTC update; regenerating metadata")

    metadata = index_data.setdefault("metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}
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
        "accepted": out_record.get("accepted"),
        "sprt": out_record.get("sprt"),
        "sprt_decision": out_record.get("sprt_decision"),
    }

    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(index_data, f, ensure_ascii=False, indent=2)
