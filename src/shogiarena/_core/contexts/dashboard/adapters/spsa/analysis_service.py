"""SPSA analysis computation service for dashboard."""

from __future__ import annotations

from collections.abc import Mapping

from shogiarena._core.contexts.dashboard.adapters.spsa.analysis_payload_builders import (
    build_convergence_analysis_payload,
    build_correlation_analysis_payload,
    normalize_updates_for_analysis,
)
from shogiarena._core.contexts.dashboard.ports.spsa_payloads import (
    ConvergenceAnalysis,
    CorrelationAnalysis,
    UpdateEntry,
)
from shogiarena._core.contexts.dashboard.ports.spsa_service_ports import DashboardSpsaStorePort
from shogiarena._core.contexts.spsa.application.dashboard.analysis import (
    compute_convergence_analysis as run_convergence_analysis,
)
from shogiarena._core.contexts.spsa.application.dashboard.analysis import (
    compute_correlation_analysis as run_correlation_analysis,
)


class SpsaAnalysisService:
    """Compute correlation and convergence analyses for SPSA updates."""

    def __init__(self, *, store: DashboardSpsaStorePort) -> None:
        self._store = store

    def _load_initial_params_override(self) -> Mapping[str, float] | None:
        try:
            meta_data = self._store.load_meta_data()
        except OSError:
            return None
        return meta_data.effective_experiment_initial_params if meta_data is not None else None

    def compute_correlation_analysis(self, updates: list[UpdateEntry]) -> CorrelationAnalysis:
        analysis = run_correlation_analysis(
            normalize_updates_for_analysis(updates),
            initial_params_override=self._load_initial_params_override(),
        )
        return build_correlation_analysis_payload(analysis)

    def compute_convergence_analysis(self, updates: list[UpdateEntry]) -> ConvergenceAnalysis:
        analysis = run_convergence_analysis(normalize_updates_for_analysis(updates))
        return build_convergence_analysis_payload(analysis)
