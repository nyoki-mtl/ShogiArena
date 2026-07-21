"""Application-layer update aggregation from SPSA event streams."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence

from shogiarena._core.contexts.spsa.application.dashboard.event_updates_state_models import (
    _build_empty_wdl_counts,
    _UpdateState,
    _WdlCounts,
)
from shogiarena._core.shared.kernel.json_coercion import to_json_object_or_empty
from shogiarena._core.shared.kernel.json_types import JsonObject, JsonValue
from shogiarena._core.shared.kernel.scalar_coercion.api import (
    coerce_bool,
    coerce_float,
    coerce_int,
    coerce_non_negative_int,
    timestamp_to_iso,
)
from shogiarena._core.shared.kernel.variant_tokens import format_variant_token

SnapshotPersistFn = Callable[[Mapping[str, JsonValue], Mapping[str, JsonValue]], None]


def _format_variant_label(update_idx: int) -> str:
    return format_variant_token(update_idx)


def _is_ltc_event(event: Mapping[str, JsonValue]) -> bool:
    if coerce_bool(event.get("is_ltc")):
        return True
    family = event.get("family")
    if isinstance(family, str):
        return family.strip().lower() == "ltc"
    return False


def _counter_value(value: JsonValue | None) -> int:
    parsed = coerce_int(value)
    return parsed if parsed is not None else 0


def _increment_counter(value: JsonValue | None) -> int:
    return _counter_value(value) + 1


def _ensure_ltc_entry(state: _UpdateState) -> JsonObject:
    if isinstance(state.ltc_regression, Mapping):
        state.has_ltc_regression = True
        return to_json_object_or_empty(state.ltc_regression)
    ltc_entry: JsonObject = {
        "status": "pending",
        "tuned_wins": 0,
        "baseline_wins": 0,
        "draws": 0,
        "total_games": 0,
        "total_pairs": None,
        "winrate": None,
        "elo": None,
        "is_accepted": None,
        "baseline_update_idx": None,
        "baseline_variant_token": None,
        "tuned_variant_token": None,
        "started_at": None,
        "completed_at": None,
    }
    state.ltc_regression = ltc_entry
    state.has_ltc_regression = True
    return ltc_entry


def _register_game(state: _UpdateState, game_id: str) -> JsonObject:
    record = state.games_meta.get(game_id)
    if record is None:
        record = {"game_id": game_id}
        state.games_meta[game_id] = record
        state.games_order.append(game_id)
    return record


def _state_to_payload(state: _UpdateState) -> JsonObject:
    entry: JsonObject = {
        "update_idx": state.update_idx,
        "timestamp": state.timestamp,
        "s_plus": state.s_plus,
        "s_minus": state.s_minus,
        "step": state.step,
        "delta_norm": state.delta_norm,
        "params": state.params,
        "gradients": state.gradients,
        "deltas": state.deltas,
        "perturbations": state.perturbations,
        "is_pending": state.is_pending,
        "c_k": state.c_k,
        "a_k": state.a_k,
        "wins": state.wins,
        "losses": state.losses,
        "draws": state.draws,
        "phase_wdl": {
            name: _WdlCounts(
                wins=bucket.get("wins", 0),
                losses=bucket.get("losses", 0),
                draws=bucket.get("draws", 0),
            )
            for name, bucket in state.phase_wdl.items()
        },
        "variant_id": _format_variant_label(state.update_idx),
        "started_at": timestamp_to_iso(state.start_time),
        "ended_at": timestamp_to_iso(state.end_time),
        "games": [state.games_meta[gid] for gid in state.games_order if gid in state.games_meta],
        "ltc_regression": state.ltc_regression,
        "has_ltc_regression": state.has_ltc_regression and state.ltc_regression is not None,
        "btd_elo": coerce_float(state.ltc_regression.get("elo")) if isinstance(state.ltc_regression, Mapping) else None,
    }
    if state.has_ltc_rejected:
        entry["is_ltc_rejected"] = state.is_ltc_rejected
    if state.ltc_reverted_to is not None:
        entry["ltc_reverted_to"] = state.ltc_reverted_to
    return entry


def collect_updates_from_events(
    events: Sequence[Mapping[str, JsonValue]],
    *,
    now_ts: int | None = None,
    persist_best_params_snapshot: SnapshotPersistFn | None = None,
) -> list[JsonObject]:
    """Aggregate update records from SPSA events."""

    now_value = now_ts if isinstance(now_ts, int) else coerce_int(time.time() * 1000.0) or 0
    updates: dict[int, _UpdateState] = {}

    def get_state(idx: int) -> _UpdateState:
        state = updates.get(idx)
        if state is None:
            state = _UpdateState(update_idx=idx, timestamp=now_value)
            updates[idx] = state
        return state

    for raw_event in events:
        event = to_json_object_or_empty(raw_event)
        idx = coerce_int(event.get("update_idx"))
        if idx is None:
            continue

        state = get_state(idx)
        event_type = event.get("event")
        event_ts = coerce_int(event.get("ts"))
        if event_ts is None:
            event_ts = state.timestamp

        if event_type == "update":
            state.timestamp = event_ts
            if state.start_time is None:
                state.start_time = event_ts
            state.end_time = max(state.end_time, event_ts) if isinstance(state.end_time, int) else event_ts

            params = event.get("params")
            if isinstance(params, Mapping):
                state.params = to_json_object_or_empty(params)
            gradients = event.get("gradients")
            if isinstance(gradients, Mapping):
                state.gradients = to_json_object_or_empty(gradients)
            deltas = event.get("deltas")
            if isinstance(deltas, Mapping):
                state.deltas = to_json_object_or_empty(deltas)
            perturbations = event.get("perturbations")
            if isinstance(perturbations, Mapping):
                state.perturbations = to_json_object_or_empty(perturbations)

            if (value := coerce_float(event.get("c_k"))) is not None:
                state.c_k = value
            if (value := coerce_float(event.get("a_k"))) is not None:
                state.a_k = value
            if (value := coerce_float(event.get("s_plus"))) is not None:
                state.s_plus = value
            if (value := coerce_float(event.get("s_minus"))) is not None:
                state.s_minus = value
            if (value := coerce_float(event.get("step"))) is not None:
                state.step = value
            if (value := coerce_float(event.get("delta_norm"))) is not None:
                state.delta_norm = value

            if "is_ltc_rejected" in event:
                state.has_ltc_rejected = True
                state.is_ltc_rejected = coerce_bool(event.get("is_ltc_rejected"))
            reverted_idx = coerce_non_negative_int(event.get("ltc_reverted_to"))
            if reverted_idx is not None:
                state.ltc_reverted_to = reverted_idx
            state.is_pending = False
            continue

        if event_type == "update_pending":
            state.timestamp = event_ts
            state.start_time = min(state.start_time, event_ts) if isinstance(state.start_time, int) else event_ts

            params = event.get("params")
            if isinstance(params, Mapping):
                state.params = to_json_object_or_empty(params)
            perturbations = event.get("perturbations")
            if isinstance(perturbations, Mapping):
                state.perturbations = to_json_object_or_empty(perturbations)
            if (value := coerce_float(event.get("c_k"))) is not None:
                state.c_k = value
            if (value := coerce_float(event.get("a_k"))) is not None:
                state.a_k = value
            state.is_pending = True
            continue

        if event_type == "update_perturbation":
            perturbations = event.get("perturbations")
            if isinstance(perturbations, Mapping):
                state.perturbations = to_json_object_or_empty(perturbations)
            if (value := coerce_float(event.get("c_k"))) is not None:
                state.c_k = value
            if (value := coerce_float(event.get("a_k"))) is not None:
                state.a_k = value
            if state.is_pending is False:
                state.is_pending = True
            continue

        if event_type == "ltc_regression_start":
            ltc_entry = _ensure_ltc_entry(state)
            ltc_entry["status"] = "running"
            ltc_entry["total_pairs"] = event.get("total_pairs")
            ltc_entry["tuned_wins"] = 0
            ltc_entry["baseline_wins"] = 0
            ltc_entry["draws"] = 0
            ltc_entry["total_games"] = 0
            if event_ts is not None:
                ltc_entry["started_at"] = event_ts
            continue

        if event_type == "ltc_regression_result":
            ltc_entry = _ensure_ltc_entry(state)
            status_value = event.get("status")
            if isinstance(status_value, str):
                ltc_entry["status"] = status_value.strip()
            if (value := coerce_float(event.get("winrate"))) is not None:
                ltc_entry["winrate"] = value
            if (value := coerce_float(event.get("elo"))) is not None:
                ltc_entry["elo"] = value
            tuned_wins = event.get("tuned_wins")
            baseline_wins = event.get("baseline_wins")
            draws = event.get("draws")
            total_games = event.get("total_games")
            ltc_entry["tuned_wins"] = coerce_int(tuned_wins) or 0
            ltc_entry["baseline_wins"] = coerce_int(baseline_wins) or 0
            ltc_entry["draws"] = coerce_int(draws) or 0
            if (coerced_total := coerce_int(total_games)) is not None:
                ltc_entry["total_games"] = coerced_total
            else:
                ltc_entry["total_games"] = (
                    _counter_value(ltc_entry.get("tuned_wins"))
                    + _counter_value(ltc_entry.get("baseline_wins"))
                    + _counter_value(ltc_entry.get("draws"))
                )
            if "is_accepted" in event:
                ltc_entry["is_accepted"] = coerce_bool(event.get("is_accepted"))
            baseline_idx = coerce_non_negative_int(event.get("baseline_update_idx"))
            if baseline_idx is not None:
                ltc_entry["baseline_update_idx"] = baseline_idx
            if (value := event.get("baseline_variant_token")) is not None:
                ltc_entry["baseline_variant_token"] = value
            if (value := event.get("tuned_variant_token")) is not None:
                ltc_entry["tuned_variant_token"] = value
            if event_ts is not None:
                ltc_entry["completed_at"] = event_ts
            if coerce_bool(ltc_entry.get("is_accepted")) and persist_best_params_snapshot is not None:
                persist_best_params_snapshot(
                    {
                        "update_idx": idx,
                        "params": state.params,
                        "variant_id": _format_variant_label(idx),
                    },
                    ltc_entry,
                )
            continue

        if event_type == "game_result":
            if _is_ltc_event(event):
                ltc_entry = _ensure_ltc_entry(state)
                if event_ts is not None and ltc_entry.get("started_at") is None:
                    ltc_entry["started_at"] = event_ts
                winner = coerce_int(event.get("winner"))
                if winner == 1:
                    ltc_entry["tuned_wins"] = _increment_counter(ltc_entry.get("tuned_wins"))
                elif winner == 0:
                    ltc_entry["baseline_wins"] = _increment_counter(ltc_entry.get("baseline_wins"))
                else:
                    ltc_entry["draws"] = _increment_counter(ltc_entry.get("draws"))
                ltc_entry["total_games"] = _increment_counter(ltc_entry.get("total_games"))
                continue

            winner = coerce_int(event.get("winner"))
            if winner == 1:
                state.wins += 1
            elif winner == 0:
                state.losses += 1
            else:
                state.draws += 1

            phase_raw = event.get("phase")
            phase_key = phase_raw.strip().lower() if isinstance(phase_raw, str) else None
            if not phase_key:
                phase_key = "unknown"
            bucket = state.phase_wdl.setdefault(phase_key, _build_empty_wdl_counts())
            if winner == 1:
                bucket["wins"] = bucket.get("wins", 0) + 1
            elif winner == 0:
                bucket["losses"] = bucket.get("losses", 0) + 1
            else:
                bucket["draws"] = bucket.get("draws", 0) + 1

            game_id = str(event.get("game_id") or "").strip()
            if game_id:
                record = _register_game(state, game_id)
                if (value := event.get("black_player")) is not None:
                    record["black_player"] = value
                if (value := event.get("white_player")) is not None:
                    record["white_player"] = value
                record["game_result"] = event.get("game_result")
                record["num_moves"] = event.get("num_moves")
                record["status"] = "completed"
                if (value := event.get("end_time")) is not None:
                    record["end_time"] = value
            continue

        if event_type == "game_scheduled":
            if _is_ltc_event(event):
                continue
            game_id = str(event.get("game_id") or "").strip()
            if not game_id:
                continue
            record = _register_game(state, game_id)
            if (value := event.get("black_player")) is not None:
                record["black_player"] = value
            if (value := event.get("white_player")) is not None:
                record["white_player"] = value
            if (value := event.get("variant_token")) is not None:
                record["variant_id"] = value
            if (value := event.get("phase")) is not None:
                record["phase"] = value
            if (value := event.get("assigned_instance")) is not None:
                record["assigned_instance"] = value
            status = str(event.get("status") or "pending").strip().lower()
            record["status"] = status or "pending"
            if (value := event.get("start_time")) is not None:
                record["start_time"] = value

    enriched = [_state_to_payload(state) for state in updates.values()]
    enriched.sort(key=lambda item: coerce_int(item.get("update_idx")) or 0, reverse=True)
    return enriched


__all__ = ["collect_updates_from_events"]
