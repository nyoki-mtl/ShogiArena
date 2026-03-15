"""SPSA dashboard service factory for interfaces bootstrap wiring."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import cast

from shogiarena._core.contexts.dashboard.adapters.spsa.analysis_service import SpsaAnalysisService
from shogiarena._core.contexts.dashboard.adapters.spsa.game_listing_service import SpsaGameListingService
from shogiarena._core.contexts.dashboard.adapters.spsa.summary_service import SpsaSummaryService
from shogiarena._core.contexts.dashboard.adapters.spsa.update_query_service import SpsaUpdateQueryService
from shogiarena._core.contexts.dashboard.application.spsa.data_store import SpsaStore
from shogiarena._core.contexts.dashboard.ports.spsa_service_ports import (
    DashboardSpsaStorePort,
    DashboardSpsaUpdateQueryPort,
)
from shogiarena._core.contexts.spsa.ports.dashboard_factory import (
    DashboardSpsaServices,
)
from shogiarena._core.contexts.spsa.ports.spsa_store_port import (
    SpsaAnalysisPort,
    SpsaGameListingPort,
    SpsaStorePort,
    SpsaSummaryServicePort,
    SpsaUpdateQueryPort,
)


@dataclass(frozen=True, slots=True)
class SpsaDashboardServicesFactory:
    """Create the cohesive SPSA dashboard service bundle."""

    def create_services(
        self,
        *,
        run_dir: Path | None,
        db_path: Path,
        store: SpsaStorePort | None = None,
        summary_service: SpsaSummaryServicePort | None = None,
        update_query_service: SpsaUpdateQueryPort | None = None,
        game_listing_service: SpsaGameListingPort | None = None,
        analysis_service: SpsaAnalysisPort | None = None,
    ) -> DashboardSpsaServices:
        resolved_run_dir = run_dir if run_dir is not None else db_path.parent

        if store is None:
            store = cast(
                SpsaStorePort,
                SpsaStore(
                    run_dir=resolved_run_dir,
                ),
            )

        if summary_service is None:
            summary_service = cast(
                SpsaSummaryServicePort,
                SpsaSummaryService(store=cast(DashboardSpsaStorePort, store)),
            )

        if update_query_service is None:
            update_query_service = cast(
                SpsaUpdateQueryPort,
                SpsaUpdateQueryService(
                    store=cast(DashboardSpsaStorePort, store),
                    db_path=db_path,
                ),
            )

        if game_listing_service is None:
            game_listing_service = cast(
                SpsaGameListingPort,
                SpsaGameListingService(
                    db_path=db_path,
                    update_query_service=cast(DashboardSpsaUpdateQueryPort, update_query_service),
                ),
            )

        if analysis_service is None:
            analysis_service = cast(
                SpsaAnalysisPort,
                SpsaAnalysisService(store=cast(DashboardSpsaStorePort, store)),
            )

        assert store is not None
        assert summary_service is not None
        assert update_query_service is not None
        assert game_listing_service is not None
        assert analysis_service is not None

        return DashboardSpsaServices(
            store=store,
            summary_service=summary_service,
            update_query_service=update_query_service,
            game_listing_service=game_listing_service,
            analysis_service=analysis_service,
        )


__all__ = [
    "SpsaDashboardServicesFactory",
]
