"""Service resolution helpers for the SPSA dashboard API."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from shogiarena._core.contexts.dashboard.ports.interface_dependencies import (
    DashboardGameQueryPort,
    DashboardSpsaSupportPort,
    load_dashboard_interface_dependencies,
)
from shogiarena._core.contexts.dashboard.ports.spsa_service_ports import (
    DashboardSpsaAnalysisPort,
    DashboardSpsaGameListingPort,
    DashboardSpsaStorePort,
    DashboardSpsaSummaryServicePort,
    DashboardSpsaUpdateQueryPort,
)
from shogiarena._core.contexts.spsa.ports.dashboard_factory import (
    DashboardSpsaServicesFactory,
    load_dashboard_service_factory,
)
from shogiarena._core.contexts.spsa.ports.spsa_store_port import (
    SpsaAnalysisPort,
    SpsaGameListingPort,
    SpsaStorePort,
    SpsaSummaryServicePort,
    SpsaUpdateQueryPort,
)


def _coerce_factory_store(store: DashboardSpsaStorePort | None) -> SpsaStorePort | None:
    if store is None:
        return None
    if isinstance(store, SpsaStorePort):
        return store
    raise TypeError("store must satisfy SpsaStorePort")


def _coerce_factory_summary_service(
    summary_service: DashboardSpsaSummaryServicePort | None,
) -> SpsaSummaryServicePort | None:
    if summary_service is None:
        return None
    if isinstance(summary_service, SpsaSummaryServicePort):
        return summary_service
    raise TypeError("summary_service must satisfy SpsaSummaryServicePort")


def _coerce_factory_update_query_service(
    service: DashboardSpsaUpdateQueryPort | None,
) -> SpsaUpdateQueryPort | None:
    if service is None:
        return None
    if isinstance(service, SpsaUpdateQueryPort):
        return service
    raise TypeError("update_query_service must satisfy SpsaUpdateQueryPort")


def _coerce_factory_game_listing_service(
    service: DashboardSpsaGameListingPort | None,
) -> SpsaGameListingPort | None:
    if service is None:
        return None
    if isinstance(service, SpsaGameListingPort):
        return service
    raise TypeError("game_listing_service must satisfy SpsaGameListingPort")


def _coerce_factory_analysis_service(
    service: DashboardSpsaAnalysisPort | None,
) -> SpsaAnalysisPort | None:
    if service is None:
        return None
    if isinstance(service, SpsaAnalysisPort):
        return service
    raise TypeError("analysis_service must satisfy SpsaAnalysisPort")


def _require_dashboard_store(store: object) -> DashboardSpsaStorePort:
    if isinstance(store, DashboardSpsaStorePort):
        return store
    raise TypeError("store must satisfy DashboardSpsaStorePort")


def _require_dashboard_summary_service(summary_service: object | None) -> DashboardSpsaSummaryServicePort:
    if isinstance(summary_service, DashboardSpsaSummaryServicePort):
        return summary_service
    raise TypeError("summary_service must satisfy DashboardSpsaSummaryServicePort")


def _require_dashboard_update_query_service(service: object | None) -> DashboardSpsaUpdateQueryPort:
    if isinstance(service, DashboardSpsaUpdateQueryPort):
        return service
    raise TypeError("update_query_service must satisfy DashboardSpsaUpdateQueryPort")


def _require_dashboard_game_listing_service(service: object | None) -> DashboardSpsaGameListingPort:
    if isinstance(service, DashboardSpsaGameListingPort):
        return service
    raise TypeError("game_listing_service must satisfy DashboardSpsaGameListingPort")


def _require_dashboard_analysis_service(service: object | None) -> DashboardSpsaAnalysisPort:
    if isinstance(service, DashboardSpsaAnalysisPort):
        return service
    raise TypeError("analysis_service must satisfy DashboardSpsaAnalysisPort")


@dataclass(frozen=True, slots=True)
class ResolvedDashboardSpsaServices:
    """Fully-resolved SPSA dashboard runtime services."""

    store: DashboardSpsaStorePort
    summary_service: DashboardSpsaSummaryServicePort
    update_query_service: DashboardSpsaUpdateQueryPort
    game_listing_service: DashboardSpsaGameListingPort
    analysis_service: DashboardSpsaAnalysisPort


@dataclass(frozen=True, slots=True)
class ResolvedDashboardInterfaceDependencies:
    """Resolved dashboard adapters required by the SPSA API."""

    spsa_support: DashboardSpsaSupportPort
    game_query: DashboardGameQueryPort


def resolve_dashboard_spsa_services(
    *,
    db_path: Path,
    run_dir: Path,
    dashboard_service_factory: DashboardSpsaServicesFactory | None = None,
    store: DashboardSpsaStorePort | None = None,
    summary_service: DashboardSpsaSummaryServicePort | None = None,
    update_query_service: DashboardSpsaUpdateQueryPort | None = None,
    game_listing_service: DashboardSpsaGameListingPort | None = None,
    analysis_service: DashboardSpsaAnalysisPort | None = None,
) -> ResolvedDashboardSpsaServices:
    resolved_factory = dashboard_service_factory
    should_create_missing_services = (
        store is None
        or summary_service is None
        or update_query_service is None
        or game_listing_service is None
        or analysis_service is None
    )
    if resolved_factory is None and should_create_missing_services:
        resolved_factory = load_dashboard_service_factory()

    if should_create_missing_services:
        assert resolved_factory is not None
        services = resolved_factory.create_services(
            run_dir=run_dir,
            db_path=db_path,
            store=_coerce_factory_store(store),
            summary_service=_coerce_factory_summary_service(summary_service),
            update_query_service=_coerce_factory_update_query_service(update_query_service),
            game_listing_service=_coerce_factory_game_listing_service(game_listing_service),
            analysis_service=_coerce_factory_analysis_service(analysis_service),
        )
        store = _require_dashboard_store(services.store)
        summary_service = _require_dashboard_summary_service(services.summary_service)
        update_query_service = _require_dashboard_update_query_service(services.update_query_service)
        game_listing_service = _require_dashboard_game_listing_service(services.game_listing_service)
        analysis_service = _require_dashboard_analysis_service(services.analysis_service)

    if (
        store is None
        or summary_service is None
        or update_query_service is None
        or game_listing_service is None
        or analysis_service is None
    ):
        raise RuntimeError("Failed to initialize SPSA services")

    return ResolvedDashboardSpsaServices(
        store=store,
        summary_service=summary_service,
        update_query_service=update_query_service,
        game_listing_service=game_listing_service,
        analysis_service=analysis_service,
    )


def resolve_dashboard_interface_dependencies(
    *,
    spsa_support: DashboardSpsaSupportPort | None = None,
    game_query: DashboardGameQueryPort | None = None,
) -> ResolvedDashboardInterfaceDependencies:
    interface_dependencies = (
        load_dashboard_interface_dependencies() if spsa_support is None or game_query is None else None
    )
    resolved_spsa_support = spsa_support or (
        interface_dependencies.spsa_support if interface_dependencies is not None else None
    )
    resolved_game_query = game_query or (
        interface_dependencies.game_query if interface_dependencies is not None else None
    )
    if resolved_spsa_support is None or resolved_game_query is None:
        raise RuntimeError("Failed to initialize dashboard interface dependencies")

    return ResolvedDashboardInterfaceDependencies(
        spsa_support=resolved_spsa_support,
        game_query=resolved_game_query,
    )


__all__ = [
    "ResolvedDashboardInterfaceDependencies",
    "ResolvedDashboardSpsaServices",
    "resolve_dashboard_interface_dependencies",
    "resolve_dashboard_spsa_services",
]
