from __future__ import annotations

import pytest

from shogiarena._core.contexts.spsa.application.dashboard.snapshot_composition import (
    SpsaSnapshotCompositionService,
)


class _StoreStub:
    def __init__(self, *, results: list[dict[str, object]] | None = None) -> None:
        self._results = results or []

    def load_ltc_results(self) -> list[dict[str, object]]:
        return list(self._results)


class _LtcServiceStub:
    def __init__(self, *, default_results: list[dict[str, object]] | None = None) -> None:
        self.summary_calls: list[list[dict[str, object]] | None] = []
        self._default_results = default_results

    def enrich_ltc_results(self, results: list[object]) -> list[dict[str, object]]:
        enriched: list[dict[str, object]] = []
        for idx, result in enumerate(results, start=1):
            item = dict(result) if isinstance(result, dict) else {"raw": result}
            item.setdefault("update_idx", idx)
            item.setdefault("elo", float(idx))
            enriched.append(item)
        return enriched

    def compute_ltc_summary(self, *, enriched_results: list[dict[str, object]] | None = None) -> dict[str, object]:
        effective_results = enriched_results
        if effective_results is None and self._default_results is not None:
            effective_results = self.enrich_ltc_results(self._default_results)
        self.summary_calls.append(effective_results)
        latest = effective_results[-1] if effective_results else None
        return {
            "status": "passed" if effective_results else "pending",
            "elo": 42.0 if effective_results else None,
            "latest": latest,
        }


class _SummaryServiceStub:
    def compute_summary(self) -> dict[str, object]:
        return {"games": {"completed": 3, "total": 5}}


def _build_live_view(snapshot: dict[str, object] | None) -> dict[str, object]:
    source = snapshot or {}
    return {"completed": source.get("games", {}).get("completed") if isinstance(source.get("games"), dict) else None}


def test_compose_ltc_results_snapshot_builds_subset_and_summary() -> None:
    service = SpsaSnapshotCompositionService(
        store=_StoreStub(results=[{"update_idx": 1}, {"update_idx": 2}, {"update_idx": 3}]),
        ltc_service=_LtcServiceStub(),
    )

    snapshot = service.compose_ltc_results_snapshot(limit=2)

    assert snapshot is not None
    assert snapshot["total"] == 3
    assert [entry["update_idx"] for entry in snapshot["results"]] == [3, 2]
    assert snapshot["summary"]["latest"]["update_idx"] == 3


def test_compose_convergence_with_ltc_only_attaches_for_ready_status() -> None:
    service = SpsaSnapshotCompositionService(
        store=_StoreStub(results=[{"update_idx": 1}]),
        ltc_service=_LtcServiceStub(),
    )

    ready = service.compose_convergence_with_ltc({"status": "ready", "updated_at": 1}, ltc_limit=1)
    assert "ltc_results" in ready

    warming = service.compose_convergence_with_ltc({"status": "warming", "updated_at": 1}, ltc_limit=1)
    assert "ltc_results" not in warming


def test_attach_ltc_summary_fields_sets_derived_elo_fields() -> None:
    service = SpsaSnapshotCompositionService(
        store=_StoreStub(),
        ltc_service=_LtcServiceStub(),
    )

    enriched = service.attach_ltc_summary_fields({"mode": "spsa"})

    assert enriched["ltc_regression"]["status"] == "pending"
    assert enriched["elo"] is None
    assert enriched["btd_elo"] is None
    assert "btd_se" in enriched
    assert "btd_los" in enriched


def test_build_summary_payload_attaches_ltc_and_live_view() -> None:
    service = SpsaSnapshotCompositionService(
        store=_StoreStub(results=[{"update_idx": 1, "elo": 12.0}]),
        ltc_service=_LtcServiceStub(default_results=[{"update_idx": 1, "elo": 12.0}]),
        summary_service=_SummaryServiceStub(),
        live_view_builder=_build_live_view,
    )

    payload = service.build_summary_payload()

    assert payload["mode"] == "spsa"
    assert payload["summarySource"] == "spsa"
    assert payload["ltc_regression"]["status"] == "passed"
    assert payload["elo"] == 42.0
    assert payload["btd_elo"] == 42.0
    assert payload["liveView"]["completed"] == 3


def test_build_summary_payload_requires_summary_service() -> None:
    service = SpsaSnapshotCompositionService(
        store=_StoreStub(),
        ltc_service=_LtcServiceStub(),
    )

    with pytest.raises(RuntimeError, match="summary_service is required"):
        service.build_summary_payload()
