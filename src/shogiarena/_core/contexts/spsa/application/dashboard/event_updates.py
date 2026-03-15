"""Application-layer update-detail aggregation from SPSA event streams."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from shogiarena._core.contexts.spsa.application.dashboard.event_updates_collection import (
    _build_empty_wdl_counts,
    _counter_value,
    _format_variant_label,
    _increment_counter,
    _is_ltc_event,
    _WdlCounts,
)
from shogiarena._core.contexts.spsa.application.dashboard.event_updates_state_models import (
    _build_empty_phase_wdl_by_side,
)
from shogiarena._core.shared.kernel.json_coercion import to_json_object_or_empty
from shogiarena._core.shared.kernel.json_types import JsonObject, JsonValue
from shogiarena._core.shared.kernel.scalar_coercion.api import (
    coerce_bool,
    coerce_float,
    coerce_int,
    coerce_non_negative_int,
    coerce_optional_text,
)


def build_update_detail_state(
    events: Sequence[Mapping[str, JsonValue]],
    *,
    idx: int,
) -> JsonObject:
    """Aggregate event-derived detail state for a single update index."""

    if not events:
        raise ValueError("no events")

    params: JsonObject | None = None
    variant_id: str | None = None
    wins = losses = draws = 0
    game_ids: list[str] = []
    games_meta: dict[str, JsonObject] = {}
    games_order: list[str] = []
    ltc_game_ids: list[str] = []
    ltc_games_meta: dict[str, JsonObject] = {}
    ltc_games_order: list[str] = []
    ltc_event_count = 0
    ltc_game_event_count = 0
    gradients: JsonObject | None = None
    deltas: JsonObject | None = None
    s_plus: float | None = None
    s_minus: float | None = None
    step: float | None = None
    perturbations: JsonObject | None = None
    gain_c_k: float | None = None
    gain_a_k: float | None = None
    is_pending = True
    phase_wdl: dict[str, _WdlCounts] = _build_empty_phase_wdl_by_side()
    ltc_regression: JsonObject | None = None

    def ensure_ltc_detail() -> JsonObject:
        nonlocal ltc_regression
        if isinstance(ltc_regression, Mapping):
            ltc_regression = to_json_object_or_empty(ltc_regression)
            return ltc_regression
        ltc_regression = {
            "status": "pending",
            "tuned_wins": 0,
            "baseline_wins": 0,
            "draws": 0,
            "total_games": 0,
            "total_pairs": None,
            "winrate": None,
            "elo": None,
            "pairs_played": None,
            "is_accepted": None,
            "baseline_update_idx": None,
            "baseline_variant_token": None,
            "tuned_variant_token": None,
            "started_at": None,
            "completed_at": None,
            "fail_reasons": [],
            "sprt": None,
            "sprt_decision": None,
        }
        return ltc_regression

    def register_game_record(game_id: str) -> JsonObject:
        entry = games_meta.get(game_id)
        if entry is None:
            entry = {"game_id": game_id}
            games_meta[game_id] = entry
            games_order.append(game_id)
        return entry

    def register_ltc_game_record(game_id: str) -> JsonObject:
        entry = ltc_games_meta.get(game_id)
        if entry is None:
            entry = {"game_id": game_id}
            ltc_games_meta[game_id] = entry
            ltc_games_order.append(game_id)
        return entry

    for raw_event in events:
        event = to_json_object_or_empty(raw_event)
        if coerce_int(event.get("update_idx")) != idx:
            continue
        event_type = event.get("event")
        if not isinstance(event_type, str):
            continue

        if event_type == "update_pending":
            params_val = event.get("params")
            if isinstance(params_val, Mapping):
                params = to_json_object_or_empty(params_val)
                variant_id = _format_variant_label(idx)
            perturb_val = event.get("perturbations")
            if isinstance(perturb_val, Mapping):
                perturbations = to_json_object_or_empty(perturb_val)
            if (val := coerce_float(event.get("c_k"))) is not None:
                gain_c_k = val
            if (val := coerce_float(event.get("a_k"))) is not None:
                gain_a_k = val
            continue

        if event_type == "update":
            params_val = event.get("params")
            if isinstance(params_val, Mapping):
                params = to_json_object_or_empty(params_val)
                variant_id = _format_variant_label(idx)
            gradients_val = event.get("gradients")
            if isinstance(gradients_val, Mapping):
                gradients = to_json_object_or_empty(gradients_val)
            deltas_val = event.get("deltas")
            if isinstance(deltas_val, Mapping):
                deltas = to_json_object_or_empty(deltas_val)
            if (val := coerce_float(event.get("s_plus"))) is not None:
                s_plus = val
            if (val := coerce_float(event.get("s_minus"))) is not None:
                s_minus = val
            if (val := coerce_float(event.get("step"))) is not None:
                step = val
            perturb_val = event.get("perturbations")
            if isinstance(perturb_val, Mapping):
                perturbations = to_json_object_or_empty(perturb_val)
            if (val := coerce_float(event.get("c_k"))) is not None:
                gain_c_k = val
            if (val := coerce_float(event.get("a_k"))) is not None:
                gain_a_k = val
            is_pending = False
            continue

        if event_type == "update_perturbation":
            perturb_val = event.get("perturbations")
            if isinstance(perturb_val, Mapping):
                perturbations = to_json_object_or_empty(perturb_val)
            if (val := coerce_float(event.get("c_k"))) is not None:
                gain_c_k = val
            if (val := coerce_float(event.get("a_k"))) is not None:
                gain_a_k = val
            continue

        if event_type == "ltc_regression_start":
            ltc_event_count += 1
            ltc_entry = ensure_ltc_detail()
            ltc_entry["status"] = "running"
            ltc_entry["total_pairs"] = coerce_int(event.get("total_pairs"))
            ltc_entry["started_at"] = event.get("ts")
            ltc_entry["tuned_wins"] = 0
            ltc_entry["baseline_wins"] = 0
            ltc_entry["draws"] = 0
            ltc_entry["total_games"] = 0
            ltc_entry["pairs_played"] = 0
            ltc_entry["sprt"] = None
            ltc_entry["sprt_decision"] = None
            continue

        if event_type == "ltc_regression_result":
            ltc_event_count += 1
            ltc_entry = ensure_ltc_detail()
            status_val = event.get("status")
            if isinstance(status_val, str):
                ltc_entry["status"] = status_val.strip()
            if (winrate_val := coerce_float(event.get("winrate"))) is not None:
                ltc_entry["winrate"] = winrate_val
            if (elo_val := coerce_float(event.get("elo"))) is not None:
                ltc_entry["elo"] = elo_val
            tuned_wins_val = coerce_int(event.get("tuned_wins"))
            baseline_wins_val = coerce_int(event.get("baseline_wins"))
            draws_val = coerce_int(event.get("draws"))
            total_games_val = coerce_int(event.get("total_games"))
            ltc_entry["tuned_wins"] = tuned_wins_val if tuned_wins_val is not None else 0
            ltc_entry["baseline_wins"] = baseline_wins_val if baseline_wins_val is not None else 0
            ltc_entry["draws"] = draws_val if draws_val is not None else 0
            if total_games_val is not None:
                ltc_entry["total_games"] = total_games_val
            else:
                ltc_entry["total_games"] = (
                    _counter_value(ltc_entry.get("tuned_wins"))
                    + _counter_value(ltc_entry.get("baseline_wins"))
                    + _counter_value(ltc_entry.get("draws"))
                )
            if (val := coerce_int(event.get("pairs_played"))) is not None:
                ltc_entry["pairs_played"] = val
            if "is_accepted" in event:
                ltc_entry["is_accepted"] = coerce_bool(event.get("is_accepted"))
            baseline_idx_val = coerce_non_negative_int(event.get("baseline_update_idx"))
            if baseline_idx_val is not None:
                ltc_entry["baseline_update_idx"] = baseline_idx_val
            if (val := event.get("baseline_variant_token")) is not None:
                ltc_entry["baseline_variant_token"] = val
            if (val := event.get("tuned_variant_token")) is not None:
                ltc_entry["tuned_variant_token"] = val
            fail_reasons_val = event.get("fail_reasons")
            if isinstance(fail_reasons_val, Sequence) and not isinstance(fail_reasons_val, str | bytes):
                fail_reasons: list[str] = []
                for reason in fail_reasons_val:
                    normalized = coerce_optional_text(reason)
                    if normalized is not None:
                        fail_reasons.append(normalized)
                ltc_entry["fail_reasons"] = fail_reasons
            sprt_val = event.get("sprt")
            if isinstance(sprt_val, Mapping):
                ltc_entry["sprt"] = to_json_object_or_empty(sprt_val)
            sprt_decision_val = event.get("sprt_decision")
            if isinstance(sprt_decision_val, str):
                ltc_entry["sprt_decision"] = sprt_decision_val
            else:
                sprt_stored = ltc_entry.get("sprt")
                if isinstance(sprt_stored, Mapping):
                    sprt_obj = to_json_object_or_empty(sprt_stored)
                    decision = sprt_obj.get("decision")
                    if isinstance(decision, str):
                        ltc_entry["sprt_decision"] = decision
            ltc_entry["completed_at"] = event.get("ts")
            continue

        if event_type == "game_result":
            if _is_ltc_event(event):
                ltc_event_count += 1
                ltc_game_event_count += 1
                ltc_entry = ensure_ltc_detail()
                if ltc_entry.get("started_at") is None:
                    ltc_entry["started_at"] = event.get("ts")
                winner = coerce_int(event.get("winner"))
                if winner == 1:
                    ltc_entry["tuned_wins"] = _increment_counter(ltc_entry.get("tuned_wins"))
                elif winner == 0:
                    ltc_entry["baseline_wins"] = _increment_counter(ltc_entry.get("baseline_wins"))
                else:
                    ltc_entry["draws"] = _increment_counter(ltc_entry.get("draws"))
                ltc_entry["total_games"] = _increment_counter(ltc_entry.get("total_games"))
                ltc_gid = str(event.get("game_id") or "").strip()
                if ltc_gid:
                    ltc_game_ids.append(ltc_gid)
                    record = register_ltc_game_record(ltc_gid)
                    if (val := event.get("black_player")) is not None:
                        record["black_player"] = val
                    if (val := event.get("white_player")) is not None:
                        record["white_player"] = val
                    record["game_result"] = event.get("game_result")
                    record["num_moves"] = event.get("num_moves")
                    record["status"] = "completed"
                    if (val := event.get("variant_token")) is not None:
                        record["variant_id"] = val
                    if (val := event.get("tuned_variant_token")) is not None:
                        record["variant_token"] = val
                    if (val := event.get("baseline_variant_token")) is not None:
                        record["baseline_variant_token"] = val
                    if (val := event.get("phase")) is not None:
                        record["phase"] = val
                    if (val := event.get("start_time")) is not None:
                        record["start_time"] = val
                    if (val := event.get("end_time")) is not None:
                        record["end_time"] = val
                    if (val := event.get("assigned_instance")) is not None:
                        record["assigned_instance"] = val
                    if (val := coerce_int(event.get("round"))) is not None:
                        record["round"] = val
                continue

            gid = str(event.get("game_id") or "").strip()
            if gid:
                game_ids.append(gid)
                record = register_game_record(gid)
                if (val := event.get("black_player")) is not None:
                    record["black_player"] = val
                if (val := event.get("white_player")) is not None:
                    record["white_player"] = val
                record["game_result"] = event.get("game_result")
                record["num_moves"] = event.get("num_moves")
                record["status"] = "completed"
                if (val := event.get("end_time")) is not None:
                    record["end_time"] = val

            winner = coerce_int(event.get("winner"))
            if winner == 1:
                wins += 1
            elif winner == 0:
                losses += 1
            else:
                draws += 1

            phase_raw = event.get("phase")
            phase_key = phase_raw.strip().lower() if isinstance(phase_raw, str) else None
            if not phase_key:
                phase_key = "unknown"
            bucket = phase_wdl.setdefault(phase_key, _build_empty_wdl_counts())
            if winner == 1:
                bucket["wins"] = bucket.get("wins", 0) + 1
            elif winner == 0:
                bucket["losses"] = bucket.get("losses", 0) + 1
            else:
                bucket["draws"] = bucket.get("draws", 0) + 1
            continue

        if event_type == "game_scheduled":
            if _is_ltc_event(event):
                ltc_event_count += 1
                ltc_game_event_count += 1
                ltc_gid = str(event.get("game_id") or "").strip()
                if not ltc_gid:
                    continue
                record = register_ltc_game_record(ltc_gid)
                if (val := event.get("black_player")) is not None:
                    record["black_player"] = val
                if (val := event.get("white_player")) is not None:
                    record["white_player"] = val
                if (val := event.get("variant_token")) is not None:
                    record["variant_id"] = val
                if (val := event.get("tuned_variant_token")) is not None:
                    record["variant_token"] = val
                if (val := event.get("baseline_variant_token")) is not None:
                    record["baseline_variant_token"] = val
                if (val := event.get("phase")) is not None:
                    record["phase"] = val
                if (val := event.get("assigned_instance")) is not None:
                    record["assigned_instance"] = val
                status_value = str(event.get("status") or "pending").strip().lower()
                record["status"] = status_value or "pending"
                if (val := event.get("start_time")) is not None:
                    record["start_time"] = val
                record.setdefault("game_result", None)
                record.setdefault("num_moves", None)
                continue

            gid = str(event.get("game_id") or "").strip()
            if not gid:
                continue
            record = register_game_record(gid)
            if (val := event.get("black_player")) is not None:
                record["black_player"] = val
            if (val := event.get("white_player")) is not None:
                record["white_player"] = val
            if (val := event.get("variant_token")) is not None:
                record["variant_id"] = val
            if (val := event.get("phase")) is not None:
                record["phase"] = val
            if (val := event.get("assigned_instance")) is not None:
                record["assigned_instance"] = val
            status_value = str(event.get("status") or "pending").strip().lower()
            record["status"] = status_value or "pending"
            if (val := event.get("start_time")) is not None:
                record["start_time"] = val
            record.setdefault("game_result", None)
            record.setdefault("num_moves", None)

    return {
        "params": params,
        "variant_id": variant_id,
        "wins": wins,
        "losses": losses,
        "draws": draws,
        "game_ids": game_ids,
        "games_meta": games_meta,
        "games_order": games_order,
        "ltc_game_ids": ltc_game_ids,
        "ltc_games_meta": ltc_games_meta,
        "ltc_games_order": ltc_games_order,
        "ltc_event_count": ltc_event_count,
        "ltc_game_event_count": ltc_game_event_count,
        "gradients": gradients,
        "deltas": deltas,
        "s_plus": s_plus,
        "s_minus": s_minus,
        "step": step,
        "perturbations": perturbations,
        "c_k": gain_c_k,
        "a_k": gain_a_k,
        "is_pending": is_pending,
        "phase_wdl": phase_wdl,
        "ltc_regression": ltc_regression,
    }


__all__ = ["build_update_detail_state"]
