"""Build best-params snapshot payloads from LTC accept events."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from shogiarena._core.shared.kernel.json_types import JsonObject, JsonValue
from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_float, timestamp_to_iso
from shogiarena._core.shared.kernel.serialization import json_serialize


@dataclass(frozen=True, slots=True)
class BestParamsSnapshotPayload:
    """Write payload for best-params snapshot persistence."""

    variant_token: str
    update_idx: int
    params: dict[str, float]
    metadata: JsonObject


def build_best_params_snapshot_payload(
    *,
    entry: Mapping[str, JsonValue],
    ltc_entry: Mapping[str, JsonValue],
    format_variant_label: Callable[[JsonValue | None], str],
) -> BestParamsSnapshotPayload | None:
    """Derive persist-ready snapshot payload from update and LTC entries."""

    params = entry.get("params")
    if not isinstance(params, Mapping) or not params:
        return None
    update_idx = entry.get("update_idx")
    if not isinstance(update_idx, int):
        return None

    token_candidate = ltc_entry.get("tuned_variant_token") or entry.get("variant_id")
    if isinstance(token_candidate, str) and token_candidate.strip():
        variant_token = token_candidate.strip()
    else:
        variant_token = format_variant_label(update_idx)
    if not variant_token:
        return None

    sanitized_params: dict[str, float] = {}
    for key, value in params.items():
        numeric = coerce_float(json_serialize(value))
        if numeric is None:
            continue
        sanitized_params[str(key)] = numeric
    if not sanitized_params:
        return None

    completed_iso = timestamp_to_iso(ltc_entry.get("completed_at"))
    metadata: JsonObject = {
        "ltc": {
            "status": ltc_entry.get("status"),
            "winrate": ltc_entry.get("winrate"),
            "elo": ltc_entry.get("elo"),
            "baseline_update_idx": ltc_entry.get("baseline_update_idx"),
            "baseline_variant_token": ltc_entry.get("baseline_variant_token"),
            "tuned_variant_token": ltc_entry.get("tuned_variant_token"),
            "pairs_played": ltc_entry.get("total_games"),
            "completed_at": completed_iso,
        }
    }
    return BestParamsSnapshotPayload(
        variant_token=variant_token,
        update_idx=update_idx,
        params=sanitized_params,
        metadata=metadata,
    )


__all__ = ["build_best_params_snapshot_payload"]
