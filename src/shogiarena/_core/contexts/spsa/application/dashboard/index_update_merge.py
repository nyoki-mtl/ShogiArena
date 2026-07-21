"""Merge SPSA index updates with event-derived enrichments."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

from shogiarena._core.shared.kernel.json_types import JsonValue
from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_bool
from shogiarena._core.shared.kernel.serialization import json_serialize


def merge_index_updates(
    *,
    index_updates: Sequence[Mapping[str, object]],
    event_updates: Sequence[Mapping[str, object]],
    format_variant_label: Callable[[JsonValue | None], str],
    parse_ltc_regression_detail: Callable[[JsonValue | None], object | None],
    normalize_non_negative_idx: Callable[[JsonValue | None], int | None],
) -> list[dict[str, object]]:
    """Merge index updates with event updates using SPSA dashboard rules."""

    entries: list[dict[str, object]] = [dict(entry) for entry in index_updates]
    if not entries:
        return []

    event_enriched: dict[int, dict[str, object]] = {}
    for entry in event_updates:
        idx = entry.get("update_idx")
        if isinstance(idx, int):
            event_enriched[idx] = dict(entry)

    existing_idx: dict[int, dict[str, object]] = {}

    for entry in entries:
        idx = entry.get("update_idx")
        if isinstance(idx, int):
            existing_idx[idx] = entry
        entry.setdefault("has_ltc_regression", False)
        entry.setdefault("is_ltc_rejected", False)
        entry["variant_id"] = format_variant_label(json_serialize(idx))
        entry.setdefault("is_pending", False)
        perturbations = entry.get("perturbations")
        if not isinstance(perturbations, Mapping):
            entry["perturbations"] = {"plus": {}, "minus": {}}
        enriched = event_enriched.get(idx) if isinstance(idx, int) else None
        if enriched is None:
            continue

        for key in ("wins", "losses", "draws"):
            if key not in entry or entry.get(key) in (None, 0):
                entry[key] = enriched.get(key)
        if "timestamp" not in entry or entry.get("timestamp") is None:
            entry["timestamp"] = enriched.get("timestamp")
        if not entry.get("started_at"):
            entry["started_at"] = enriched.get("started_at")
        if not entry.get("ended_at"):
            entry["ended_at"] = enriched.get("ended_at")
        if "phase_wdl" not in entry or not entry.get("phase_wdl"):
            entry["phase_wdl"] = enriched.get("phase_wdl")
        if not entry.get("perturbations"):
            entry["perturbations"] = enriched.get("perturbations", {"plus": {}, "minus": {}})
        if entry.get("c_k") is None and enriched.get("c_k") is not None:
            entry["c_k"] = enriched.get("c_k")
        if enriched.get("is_pending") is True:
            entry["is_pending"] = True
        ltc_info = enriched.get("ltc_regression")
        if isinstance(ltc_info, Mapping) and ltc_info:
            parsed_ltc = parse_ltc_regression_detail(json_serialize(ltc_info))
            if parsed_ltc is not None:
                entry["ltc_regression"] = parsed_ltc
        if enriched.get("has_ltc_regression"):
            entry["has_ltc_regression"] = True
        if "is_ltc_rejected" in enriched:
            entry["is_ltc_rejected"] = coerce_bool(enriched.get("is_ltc_rejected"))
        reverted_raw = enriched.get("ltc_reverted_to") if "ltc_reverted_to" in enriched else None
        reverted_idx = normalize_non_negative_idx(json_serialize(reverted_raw))
        if reverted_idx is not None:
            entry["ltc_reverted_to"] = reverted_idx

    for enriched in event_updates:
        idx = enriched.get("update_idx")
        if not isinstance(idx, int) or idx in existing_idx:
            continue
        enriched_copy: dict[str, object] = dict(enriched)
        enriched_copy.setdefault("is_pending", True)
        if not isinstance(enriched_copy.get("perturbations"), Mapping):
            enriched_copy["perturbations"] = {"plus": {}, "minus": {}}
        if "is_ltc_rejected" in enriched_copy:
            enriched_copy["is_ltc_rejected"] = coerce_bool(enriched_copy.get("is_ltc_rejected"))
        else:
            enriched_copy.setdefault("is_ltc_rejected", False)
        reverted_idx = normalize_non_negative_idx(json_serialize(enriched_copy.get("ltc_reverted_to")))
        if reverted_idx is not None:
            enriched_copy["ltc_reverted_to"] = reverted_idx
        else:
            enriched_copy.pop("ltc_reverted_to", None)
        enriched_copy["variant_id"] = format_variant_label(json_serialize(idx))
        enriched_copy.pop("start_time", None)
        enriched_copy.pop("end_time", None)
        if "has_ltc_regression" not in enriched_copy:
            enriched_copy["has_ltc_regression"] = enriched_copy.get("ltc_regression") is not None
        entries.append(enriched_copy)

    return entries


__all__ = ["merge_index_updates"]
