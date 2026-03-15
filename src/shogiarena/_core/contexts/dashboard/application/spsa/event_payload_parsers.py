"""Payload parsers for concrete SPSA event kinds."""

from __future__ import annotations

from collections.abc import Mapping

from shogiarena._core.shared.kernel.json_coercion import coerce_json_object_or_none
from shogiarena._core.shared.kernel.json_types import JsonObject, JsonValue
from shogiarena._core.shared.kernel.scalar_coercion.api import (
    coerce_float,
    coerce_float_dict,
    coerce_int,
    coerce_nested_float_dict,
    coerce_str,
    coerce_timestamp_ms,
)


def _set_optional_str(result: JsonObject, raw: Mapping[str, JsonValue], key: str) -> None:
    if (v := coerce_str(raw.get(key))) is not None:
        result[key] = v


def _set_optional_int(result: JsonObject, raw: Mapping[str, JsonValue], key: str) -> None:
    if (v := coerce_int(raw.get(key))) is not None:
        result[key] = v


def _set_optional_float(result: JsonObject, raw: Mapping[str, JsonValue], key: str) -> None:
    if (v := coerce_float(raw.get(key))) is not None:
        result[key] = v


def _set_optional_bool(result: JsonObject, raw: Mapping[str, JsonValue], key: str) -> None:
    value = raw.get(key)
    if isinstance(value, bool):
        result[key] = value


def parse_game_scheduled_payload(raw: Mapping[str, JsonValue], base: JsonObject) -> JsonObject:
    result: JsonObject = dict(base)
    for key in (
        "game_id",
        "variant_token",
        "variant_label",
        "tuned_variant_token",
        "baseline_variant_token",
        "phase",
        "black_player",
        "white_player",
        "status",
        "assigned_instance",
        "start_time",
    ):
        _set_optional_str(result, raw, key)
    _set_optional_bool(result, raw, "is_tuned_as_black")
    _set_optional_int(result, raw, "worker_idx")
    if "assigned_instance" not in result and "assigned_instance" in raw:
        result["assigned_instance"] = None
    if "start_time" not in result and "start_time" in raw:
        result["start_time"] = None
    return result


def parse_game_result_payload(raw: Mapping[str, JsonValue], base: JsonObject) -> JsonObject:
    result: JsonObject = dict(base)
    _set_optional_int(result, raw, "winner")
    _set_optional_bool(result, raw, "is_tuned_as_black")
    for key in (
        "game_id",
        "phase",
        "tuned_variant",
        "baseline_variant",
        "variant_token",
        "variant_label",
        "black_player",
        "white_player",
        "initial_sfen",
        "time_control_black",
        "time_control_white",
        "end_time",
    ):
        _set_optional_str(result, raw, key)
    _set_optional_int(result, raw, "num_moves")
    _set_optional_str(result, raw, "game_result")
    return result


def parse_update_pending_payload(raw: Mapping[str, JsonValue], base: JsonObject) -> JsonObject:
    result: JsonObject = dict(base)
    normalized_params = coerce_json_object_or_none(raw.get("params"))
    if normalized_params is not None:
        result["params"] = coerce_float_dict(normalized_params)
    if (v := coerce_timestamp_ms(raw.get("timestamp"))) is not None:
        result["timestamp"] = v
    normalized_perturbations = coerce_json_object_or_none(raw.get("perturbations"))
    if normalized_perturbations is not None:
        result["perturbations"] = coerce_nested_float_dict(normalized_perturbations)
    _set_optional_bool(result, raw, "is_pending")
    _set_optional_float(result, raw, "c_k")
    _set_optional_float(result, raw, "a_k")
    return result


def parse_update_payload(raw: Mapping[str, JsonValue], base: JsonObject) -> JsonObject:
    result: JsonObject = dict(base)
    normalized_params = coerce_json_object_or_none(raw.get("params"))
    if normalized_params is not None:
        result["params"] = coerce_float_dict(normalized_params)
    if (v := coerce_timestamp_ms(raw.get("timestamp"))) is not None:
        result["timestamp"] = v
    for fkey in ("s_plus", "s_minus", "step", "delta_norm", "c_k", "a_k"):
        _set_optional_float(result, raw, fkey)
    normalized_gradients = coerce_json_object_or_none(raw.get("gradients"))
    if normalized_gradients is not None:
        result["gradients"] = coerce_float_dict(normalized_gradients)
    normalized_deltas = coerce_json_object_or_none(raw.get("deltas"))
    if normalized_deltas is not None:
        result["deltas"] = coerce_float_dict(normalized_deltas)
    for ikey in ("batch_size", "total_games", "ltc_reverted_to"):
        _set_optional_int(result, raw, ikey)
    normalized_perturbations = coerce_json_object_or_none(raw.get("perturbations"))
    if normalized_perturbations is not None:
        result["perturbations"] = coerce_nested_float_dict(normalized_perturbations)
    _set_optional_bool(result, raw, "is_ltc_rejected")
    return result


def parse_update_perturbation_payload(raw: Mapping[str, JsonValue], base: JsonObject) -> JsonObject:
    result: JsonObject = dict(base)
    normalized_perturbations = coerce_json_object_or_none(raw.get("perturbations"))
    if normalized_perturbations is not None:
        result["perturbations"] = coerce_nested_float_dict(normalized_perturbations)
    _set_optional_float(result, raw, "c_k")
    _set_optional_float(result, raw, "a_k")
    return result


def parse_ltc_regression_start_payload(raw: Mapping[str, JsonValue], base: JsonObject) -> JsonObject:
    result: JsonObject = dict(base)
    _set_optional_int(result, raw, "total_pairs")
    _set_optional_str(result, raw, "tuned_variant_token")
    _set_optional_int(result, raw, "baseline_update_idx")
    _set_optional_str(result, raw, "baseline_variant_token")
    return result


def parse_ltc_regression_result_payload(raw: Mapping[str, JsonValue], base: JsonObject) -> JsonObject:
    result: JsonObject = dict(base)
    _set_optional_str(result, raw, "status")
    winrate = raw.get("winrate")
    if winrate is not None:
        result["winrate"] = coerce_float(winrate)
    elo = raw.get("elo")
    if elo is not None:
        result["elo"] = coerce_float(elo)
    for ikey in ("tuned_wins", "baseline_wins", "draws", "total_games", "pairs_played", "baseline_update_idx"):
        _set_optional_int(result, raw, ikey)
    _set_optional_bool(result, raw, "is_accepted")
    _set_optional_str(result, raw, "tuned_variant_token")
    _set_optional_str(result, raw, "baseline_variant_token")
    _set_optional_str(result, raw, "sprt_decision")
    raw_fail = raw.get("fail_reasons")
    if isinstance(raw_fail, list):
        fail_reasons: list[str] = []
        for reason in raw_fail:
            normalized_reason = coerce_str(reason)
            if normalized_reason is not None:
                fail_reasons.append(normalized_reason)
        result["fail_reasons"] = fail_reasons
    normalized_sprt = coerce_json_object_or_none(raw.get("sprt"))
    if normalized_sprt is not None:
        result["sprt"] = normalized_sprt
    elif raw.get("sprt") is None:
        result["sprt"] = None
    return result
