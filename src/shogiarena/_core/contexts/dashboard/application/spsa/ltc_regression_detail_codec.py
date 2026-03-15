"""Normalization helpers for LTC regression payloads."""

from __future__ import annotations

from collections.abc import Mapping

from shogiarena._core.contexts.dashboard.ports.spsa_payloads import LtcRegressionDetail
from shogiarena._core.shared.kernel.json_types import JsonObject, JsonValue
from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_bool, coerce_float, coerce_int
from shogiarena._core.shared.kernel.serialization import json_serialize


def parse_ltc_regression_detail(value: JsonValue | None) -> LtcRegressionDetail | None:
    """Convert loose LTC regression payload into typed detail contract."""
    if not isinstance(value, Mapping):
        return None

    raw: JsonObject = {}
    for key, item in value.items():
        if isinstance(key, str):
            raw[key] = json_serialize(item)
    detail: LtcRegressionDetail = {}

    status = raw.get("status")
    if isinstance(status, str):
        detail["status"] = status

    for int_key in (
        "tuned_wins",
        "baseline_wins",
        "draws",
        "total_games",
        "total_pairs",
        "pairs_played",
        "baseline_update_idx",
    ):
        normalized = coerce_int(raw.get(int_key))
        if normalized is not None:
            detail[int_key] = normalized

    for float_key in ("winrate", "elo"):
        normalized = coerce_float(raw.get(float_key))
        if normalized is not None:
            detail[float_key] = normalized
        elif float_key in raw and raw.get(float_key) is None:
            detail[float_key] = None

    for str_key in ("baseline_variant_token", "tuned_variant_token", "sprt_decision"):
        token = raw.get(str_key)
        if isinstance(token, str):
            detail[str_key] = token
        elif token is None and str_key in raw:
            detail[str_key] = None

    for time_key in ("started_at", "completed_at"):
        timestamp = raw.get(time_key)
        if isinstance(timestamp, str):
            detail[time_key] = timestamp
            continue
        normalized = coerce_int(timestamp)
        if normalized is not None:
            detail[time_key] = normalized
        elif timestamp is None and time_key in raw:
            detail[time_key] = None

    if "is_accepted" in raw:
        detail["is_accepted"] = coerce_bool(raw.get("is_accepted"))

    fail_reasons = raw.get("fail_reasons")
    if isinstance(fail_reasons, list):
        detail["fail_reasons"] = [reason for reason in fail_reasons if isinstance(reason, str)]

    sprt_payload = raw.get("sprt")
    if isinstance(sprt_payload, Mapping):
        normalized_sprt: JsonObject = {}
        for key, item in sprt_payload.items():
            if isinstance(key, str):
                normalized_sprt[key] = json_serialize(item)
        detail["sprt"] = normalized_sprt
    elif sprt_payload is None and "sprt" in raw:
        detail["sprt"] = None

    return detail if detail else None
