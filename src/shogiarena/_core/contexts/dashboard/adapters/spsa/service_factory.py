"""SPSA dashboard service factory for interfaces bootstrap wiring."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import cast

from shogiarena._core.contexts.dashboard.adapters.spsa.analysis_service import SpsaAnalysisService
from shogiarena._core.contexts.dashboard.adapters.spsa.game_listing_service import SpsaGameListingService
from shogiarena._core.contexts.dashboard.adapters.spsa.run_projector import SpsaRunProjector
from shogiarena._core.contexts.dashboard.adapters.spsa.summary_service import SpsaSummaryService
from shogiarena._core.contexts.dashboard.adapters.spsa.update_query_service import SpsaUpdateQueryService
from shogiarena._core.contexts.dashboard.application.spsa.data_store import SpsaStore
from shogiarena._core.contexts.dashboard.ports.spsa_service_ports import (
    DashboardSpsaStorePort,
    DashboardSpsaUpdateQueryPort,
)
from shogiarena._core.contexts.spsa.adapters.ledger_store import open_spsa_ledger
from shogiarena._core.contexts.spsa.ports.dashboard_factory import (
    DashboardSpsaServices,
)
from shogiarena._core.contexts.spsa.ports.ledger_ports import SPSA_LEDGER_RELATIVE_PATH
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
        read_only: bool = False,
        store: SpsaStorePort | None = None,
        summary_service: SpsaSummaryServicePort | None = None,
        update_query_service: SpsaUpdateQueryPort | None = None,
        game_listing_service: SpsaGameListingPort | None = None,
        analysis_service: SpsaAnalysisPort | None = None,
    ) -> DashboardSpsaServices:
        resolved_run_dir = run_dir if run_dir is not None else db_path.parent
        is_spsa_archive = (resolved_run_dir / "spsa" / "meta.json").is_file() or (
            resolved_run_dir / SPSA_LEDGER_RELATIVE_PATH
        ).exists()
        if read_only and is_spsa_archive:
            with open_spsa_ledger(resolved_run_dir, read_only=True):
                pass

        if store is None:
            store = cast(
                SpsaStorePort,
                SpsaStore(
                    run_dir=resolved_run_dir,
                ),
            )

        projector = (
            SpsaRunProjector(
                run_dir=resolved_run_dir,
                db_path=db_path,
                immutable_db=read_only,
            )
            if update_query_service is None and (resolved_run_dir / SPSA_LEDGER_RELATIVE_PATH).is_file()
            else None
        )

        if summary_service is None:
            summary_service = cast(
                SpsaSummaryServicePort,
                SpsaSummaryService(
                    store=cast(DashboardSpsaStorePort, store),
                    ledger_summary_loader=projector.summary_aggregates if projector is not None else None,
                    operational_status_loader=projector.operational_status if projector is not None else None,
                ),
            )

        if update_query_service is None:
            update_query_service = cast(
                SpsaUpdateQueryPort,
                SpsaUpdateQueryService(
                    store=cast(DashboardSpsaStorePort, store),
                    db_path=db_path,
                    read_only=read_only,
                    ledger_update_loader=projector.load_update if projector is not None else None,
                    ledger_updates_loader=projector.load_updates if projector is not None else None,
                    ledger_events_loader=projector.load_events if projector is not None else None,
                    ledger_game_snapshot_loader=projector.game_snapshot if projector is not None else None,
                    ledger_game_snapshots_loader=projector.game_snapshots if projector is not None else None,
                    ledger_game_entries_loader=projector.game_entries if projector is not None else None,
                    ledger_ltc_results_loader=projector.load_ltc_results if projector is not None else None,
                    ledger_revision_loader=projector.revision_state if projector is not None else None,
                    game_batch_loader=projector.load_game_records if projector is not None else None,
                    close_callback=projector.close if projector is not None else None,
                ),
            )

        if game_listing_service is None:
            game_listing_service = cast(
                SpsaGameListingPort,
                SpsaGameListingService(
                    db_path=db_path,
                    update_query_service=cast(DashboardSpsaUpdateQueryPort, update_query_service),
                    read_only=read_only,
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
