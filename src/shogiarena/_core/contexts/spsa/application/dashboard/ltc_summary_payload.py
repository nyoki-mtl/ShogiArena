"""Build LTC summary payload from SPSA metadata and results."""

from __future__ import annotations

from collections.abc import Mapping

from shogiarena._core.contexts.spsa.application.dashboard.ltc_regression_entry_parsing import round_optional_int
from shogiarena._core.shared.kernel.json_coercion import is_str_object_mapping, to_json_object
from shogiarena._core.shared.kernel.json_types import JsonObject
from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_bool, coerce_float


def build_ltc_summary_payload(
    *,
    config_meta: Mapping[str, object] | None,
    index_meta: Mapping[str, object] | None,
    latest_result: Mapping[str, object] | None,
    history_size: int,
) -> JsonObject:
    """Build LTC summary payload for dashboard API responses."""

    config_meta_dict = to_json_object(config_meta) if config_meta is not None else None
    index_meta_dict: JsonObject = to_json_object(index_meta) if index_meta is not None else {}
    latest: JsonObject | None = to_json_object(latest_result) if latest_result is not None else None

    enabled = (
        coerce_bool(config_meta_dict.get("is_enabled")) if config_meta_dict is not None else len(index_meta_dict) > 0
    )

    status_raw = index_meta_dict.get("status") if index_meta_dict else None
    if status_raw is None and latest:
        status_raw = latest.get("status")
    if isinstance(status_raw, str) and status_raw:
        status = status_raw
    else:
        status = "pending" if enabled else "disabled"

    last_update_idx = round_optional_int(index_meta_dict.get("last_update_idx")) if index_meta_dict else None
    if last_update_idx is None and latest:
        last_update_idx = round_optional_int(latest.get("update_idx"))

    winrate = coerce_float(index_meta_dict.get("winrate")) if index_meta_dict else None
    if winrate is None and latest is not None:
        winrate = coerce_float(latest.get("winrate"))

    elo = coerce_float(index_meta_dict.get("elo")) if index_meta_dict else None
    best_estimate: JsonObject | None = None
    if latest is not None:
        elo = coerce_float(latest.get("best_elo_mean")) if latest.get("best_elo_mean") is not None else elo
        source_raw = latest.get("best_estimate_source")
        best_estimate = {
            "mean": coerce_float(latest.get("best_elo_mean")),
            "variance": coerce_float(latest.get("best_elo_variance")),
            "sigma": coerce_float(latest.get("best_elo_sigma")),
            "lower": coerce_float(latest.get("best_elo_lower_1sigma")),
            "upper": coerce_float(latest.get("best_elo_upper_1sigma")),
            "source": source_raw if isinstance(source_raw, str) else None,
            "composition_depth": round_optional_int(latest.get("best_composition_depth")),
            "prob_positive": coerce_float(latest.get("best_positive_probability")),
        }

    pairs_played = round_optional_int(index_meta_dict.get("pairs_played")) if index_meta_dict else None
    if pairs_played is None and latest is not None:
        pairs_played = round_optional_int(latest.get("pairs_played"))

    sprt_meta = index_meta_dict.get("sprt") if index_meta_dict else None
    sprt = to_json_object(sprt_meta) if is_str_object_mapping(sprt_meta) else None
    if sprt is None and latest is not None:
        sprt_latest = latest.get("sprt")
        sprt = to_json_object(sprt_latest) if is_str_object_mapping(sprt_latest) else None

    sprt_decision_raw = index_meta_dict.get("sprt_decision") if index_meta_dict else None
    sprt_decision = sprt_decision_raw if isinstance(sprt_decision_raw, str) else None
    if sprt_decision is None and sprt is not None:
        decision_val = sprt.get("decision")
        if isinstance(decision_val, str):
            sprt_decision = decision_val

    return {
        "is_enabled": enabled,
        "status": status,
        "config": config_meta_dict,
        "last_update_idx": last_update_idx,
        "winrate": winrate,
        "elo": elo,
        "pairs_played": pairs_played,
        "sprt": sprt,
        "sprt_decision": sprt_decision,
        "latest": latest,
        "history_size": history_size,
        "best_estimate": best_estimate,
    }


__all__ = ["build_ltc_summary_payload"]
