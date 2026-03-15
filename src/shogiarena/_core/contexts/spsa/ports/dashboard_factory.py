"""Port contract for SPSA dashboard service creation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from shogiarena._core.contexts.spsa.ports.spsa_store_port import (
    SpsaAnalysisPort,
    SpsaGameListingPort,
    SpsaStorePort,
    SpsaSummaryServicePort,
    SpsaUpdateQueryPort,
)


@dataclass(frozen=True, slots=True)
class DashboardSpsaServices:
    """Typed bundle of SPSA dashboard-facing runtime services."""

    store: SpsaStorePort
    summary_service: SpsaSummaryServicePort
    update_query_service: SpsaUpdateQueryPort
    game_listing_service: SpsaGameListingPort
    analysis_service: SpsaAnalysisPort


@runtime_checkable
class DashboardSpsaServicesFactory(Protocol):
    """Factory object that creates SPSA dashboard service instances."""

    def create_services(
        self,
        *,
        run_dir: Path | None,
        db_path: Path,
        store: SpsaStorePort | None = ...,
        summary_service: SpsaSummaryServicePort | None = ...,
        update_query_service: SpsaUpdateQueryPort | None = ...,
        game_listing_service: SpsaGameListingPort | None = ...,
        analysis_service: SpsaAnalysisPort | None = ...,
    ) -> DashboardSpsaServices: ...


_dashboard_service_factory: DashboardSpsaServicesFactory | None = None


def configure_dashboard_service_factory(factory: DashboardSpsaServicesFactory) -> None:
    """Register the dashboard service factory from the composition root."""

    global _dashboard_service_factory
    _dashboard_service_factory = factory


def load_dashboard_service_factory() -> DashboardSpsaServicesFactory:
    """Load the configured dashboard service factory."""

    if _dashboard_service_factory is None:
        raise RuntimeError("dashboard service factory is not configured")
    return _dashboard_service_factory


__all__ = [
    "DashboardSpsaServices",
    "DashboardSpsaServicesFactory",
    "configure_dashboard_service_factory",
    "load_dashboard_service_factory",
]
