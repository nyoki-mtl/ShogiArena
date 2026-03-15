"""Build LTC summary payloads from normalized dashboard inputs."""

from __future__ import annotations

from collections.abc import Sequence

from shogiarena._core.contexts.spsa.application.dashboard.ltc_summary_payload import (
    build_ltc_summary_payload,
)
from shogiarena._core.shared.kernel.json_coercion import to_json_object
from shogiarena._core.shared.kernel.json_types import JsonObject


def build_ltc_summary_projection(
    *,
    config_meta_payload: JsonObject | None,
    index_meta_payload: JsonObject | None,
    enriched_results: Sequence[JsonObject],
) -> JsonObject:
    """Compose LTC summary payload from already-normalized entries."""

    config_meta: JsonObject | None = to_json_object(config_meta_payload) if config_meta_payload else None
    index_meta: JsonObject = to_json_object(index_meta_payload) if index_meta_payload else {}
    latest = to_json_object(enriched_results[-1]) if enriched_results else None
    return build_ltc_summary_payload(
        config_meta=config_meta,
        index_meta=index_meta,
        latest_result=latest,
        history_size=len(enriched_results),
    )


__all__ = ["build_ltc_summary_projection"]
