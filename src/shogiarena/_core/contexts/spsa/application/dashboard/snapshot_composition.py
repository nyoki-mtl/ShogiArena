"""SPSA snapshot composition service.

REST/SSE で重複していたスナップショット組み立てロジックを
application 層に集約する。interfaces 層はこのサービスを呼ぶ thin adapter に縮退する。
"""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from shogiarena._core.shared.kernel.json_coercion import to_json_object
from shogiarena._core.shared.kernel.json_types import JsonObject, JsonValue
from shogiarena._core.shared.kernel.serialization import json_serialize


class _LtcServicePort(Protocol):
    """LTC service の最小契約。"""

    def compute_ltc_summary(self, *, enriched_results: list[JsonObject] | None = None) -> Mapping[str, object]: ...
    def enrich_ltc_results(self, results: Sequence[object]) -> list[JsonObject]: ...


class _StorePort(Protocol):
    """Store の最小契約。"""

    def load_ltc_results(self) -> Sequence[Mapping[str, object]]: ...


class _SummaryServicePort(Protocol):
    """Summary service の最小契約。"""

    def compute_summary(self) -> Mapping[str, object]: ...


class _LiveViewBuilderPort(Protocol):
    """Live view snapshot 生成の最小契約。"""

    def __call__(self, snapshot: Mapping[str, JsonValue] | None) -> Mapping[str, object]: ...


class SpsaSnapshotCompositionService:
    """SPSA スナップショット合成の一元的サービス。

    LTC 結果エンリッチメント、convergence + LTC 統合、
    summary + LTC フィールド関連付けを一箇所に集約する。
    """

    def __init__(
        self,
        *,
        store: Any,
        ltc_service: Any,
        summary_service: Any | None = None,
        live_view_builder: Any | None = None,
    ) -> None:
        self._store: _StorePort = store
        self._ltc_service: _LtcServicePort = ltc_service
        self._summary_service: _SummaryServicePort | None = summary_service
        self._live_view_builder: _LiveViewBuilderPort | None = live_view_builder

    # ── LTC 結果スナップショット ─────────────────────────────────────

    def compose_ltc_results_snapshot(self, *, limit: int = 200) -> JsonObject | None:
        """LTC 結果を enrich し、subset + summary を組み立てる。"""
        raw_results = self._store.load_ltc_results()
        enriched_results = self._ltc_service.enrich_ltc_results(raw_results)
        total = len(enriched_results)
        subset = enriched_results[-limit:] if limit < total else enriched_results
        subset_out = list(reversed(subset))
        summary = self._ltc_service.compute_ltc_summary(enriched_results=enriched_results)
        return to_json_object(
            {
                "total": total,
                "results": subset_out,
                "summary": summary,
            }
        )

    # ── Convergence + LTC 統合 ──────────────────────────────────────

    def compose_convergence_with_ltc(
        self,
        convergence_snapshot: Mapping[str, object],
        *,
        ltc_limit: int = 200,
    ) -> JsonObject:
        """Convergence スナップショットに LTC 結果を統合する。"""
        result: JsonObject = dict(convergence_snapshot)  # type: ignore[arg-type]
        if result.get("status") == "ready":
            ltc_snapshot = self.compose_ltc_results_snapshot(limit=ltc_limit)
            if ltc_snapshot is not None:
                result["ltc_results"] = ltc_snapshot
        return result

    # ── Summary + LTC フィールド関連付け ────────────────────────────

    def attach_ltc_summary_fields(
        self,
        snapshot: Mapping[str, JsonValue],
        *,
        ltc_summary: Mapping[str, object] | None = None,
    ) -> JsonObject:
        """スナップショットに LTC regression フィールドを関連付ける。"""
        enriched = to_json_object(snapshot)
        summary_payload: Mapping[str, object] | None = ltc_summary
        if summary_payload is None:
            summary_payload = self._ltc_service.compute_ltc_summary()

        if isinstance(summary_payload, Mapping):
            summary_dict: JsonObject = {str(k): json_serialize(v) for k, v in summary_payload.items()}
            enriched["ltc_regression"] = summary_dict
            ltc_elo = summary_dict.get("elo")
            enriched["elo"] = ltc_elo
            enriched["btd_elo"] = ltc_elo
        else:
            enriched.setdefault("ltc_regression", None)
            enriched.setdefault("elo", None)
            enriched.setdefault("btd_elo", None)

        enriched.setdefault("btd_se", None)
        enriched.setdefault("btd_los", None)
        return enriched

    # ── Summary payload 組み立て ────────────────────────────────────

    def build_summary_payload(self) -> JsonObject:
        """Summary + LTC + live view を統合した payload を返す。"""
        if self._summary_service is None:
            raise RuntimeError("summary_service is required for build_summary_payload")

        overall_start = time.perf_counter()
        ltc_start = time.perf_counter()
        ltc_summary = self._ltc_service.compute_ltc_summary()
        ltc_elapsed = (time.perf_counter() - ltc_start) * 1000.0
        if ltc_elapsed >= 50.0:
            print(f"[spsa:ltc_summary] duration_ms={ltc_elapsed:.1f}", flush=True)

        summary_start = time.perf_counter()
        summary_data = to_json_object(self._summary_service.compute_summary())
        summary_elapsed = (time.perf_counter() - summary_start) * 1000.0
        if summary_elapsed >= 50.0:
            games_snapshot = summary_data.get("games")
            completed = None
            total = None
            if isinstance(games_snapshot, Mapping):
                games_map: JsonObject = {}
                for key, value in games_snapshot.items():
                    games_map[str(key)] = json_serialize(value)
                completed = games_map.get("completed")
                total = games_map.get("total")
            print(
                f"[spsa:summary_service] duration_ms={summary_elapsed:.1f} completed={completed} total={total}",
                flush=True,
            )

        summary_data = self.attach_ltc_summary_fields(summary_data, ltc_summary=ltc_summary)
        summary_data.setdefault("mode", "spsa")
        summary_data.setdefault("summarySource", "spsa")
        if self._live_view_builder is not None:
            summary_data["liveView"] = json_serialize(self._live_view_builder(summary_data))
        total_elapsed = (time.perf_counter() - overall_start) * 1000.0
        if total_elapsed >= 200.0:
            print(f"[spsa:summary_total] duration_ms={total_elapsed:.1f}", flush=True)
        return summary_data


__all__ = ["SpsaSnapshotCompositionService"]
