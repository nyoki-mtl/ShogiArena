"""SPSA LTC (Long Time Control) regression service."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import cast

from shogiarena._core.contexts.dashboard.application.spsa.event_types import SpsaEvent
from shogiarena._core.contexts.dashboard.ports.spsa_payloads import LtcSummary
from shogiarena._core.contexts.dashboard.ports.spsa_service_ports import DashboardSpsaStorePort
from shogiarena._core.contexts.spsa.application.dashboard.ltc_regression import (
    enrich_ltc_results as run_enrich_ltc_results,
)
from shogiarena._core.contexts.spsa.application.dashboard.ltc_summary_projection import (
    build_ltc_summary_projection,
)
from shogiarena._core.shared.kernel.json_coercion import to_json_object
from shogiarena._core.shared.kernel.json_types import JsonObject, JsonValue


class SpsaLtcService:
    """Service for LTC regression summary and results."""

    def __init__(
        self,
        store: DashboardSpsaStorePort,
    ) -> None:
        self._store = store

    def compute_ltc_summary(
        self,
        *,
        enriched_results: list[JsonObject] | None = None,
    ) -> LtcSummary:
        """Compute LTC regression summary from config, results, and index metadata."""
        ltc_config = self._store.load_meta_data().ltc_regression
        config_meta_payload = to_json_object(ltc_config.model_dump(exclude_none=True)) if ltc_config else None

        if enriched_results is None:
            raw_results = self._store.load_ltc_results()
            enriched_results = self.enrich_ltc_results(raw_results)

        ltc_index = self._store.load_index_metadata().ltc_regression
        index_meta_payload = to_json_object(ltc_index.model_dump(exclude_none=True)) if ltc_index else None
        summary = build_ltc_summary_projection(
            config_meta_payload=config_meta_payload,
            index_meta_payload=index_meta_payload,
            enriched_results=enriched_results,
        )
        return cast(LtcSummary, summary)

    def enrich_ltc_results(self, results: Sequence[SpsaEvent | Mapping[str, JsonValue]]) -> list[JsonObject]:
        """Return LTC results enriched with cumulative best estimates."""

        normalized: list[JsonObject] = [to_json_object(result) for result in results]
        return run_enrich_ltc_results(normalized)
