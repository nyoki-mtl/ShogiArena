"""Typed mutable state for SPSA runner."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from shogiarena._core.contexts.spsa.domain.spsa_models import ParamEntry
from shogiarena._core.contexts.spsa.ports.spsa_store_port import (
    SpsaAnalysisPort,
    SpsaGameListingPort,
    SpsaStorePort,
    SpsaSummaryServicePort,
    SpsaUpdateQueryPort,
)
from shogiarena._core.shared.kernel.json_types import JsonObject
from shogiarena._core.shared.kernel.service_ports import DatabaseServicePort


@dataclass
class SpsaRunnerState:
    """Typed container for SPSA runner mutable state.

    Mirrors the ``TournamentRunnerState`` pattern established in Phase C,
    making the runner's mutable dependencies explicit and typed.
    """

    # -- Domain inputs (populated during prepare_domain) -------------------
    params: list[ParamEntry] | None = None
    sfens: list[str] | None = None
    update_items: list[int] | None = None

    # -- Execution control -------------------------------------------------
    completion_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    session_uuid: str = field(default_factory=lambda: uuid.uuid4().hex)
    session_started_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))

    # -- Service references (initialized during runner lifecycle) ----------
    db_service: DatabaseServicePort | None = None
    spsa_store: SpsaStorePort | None = None
    spsa_summary_service: SpsaSummaryServicePort | None = None
    spsa_update_query_service: SpsaUpdateQueryPort | None = None
    spsa_game_listing_service: SpsaGameListingPort | None = None
    spsa_analysis_service: SpsaAnalysisPort | None = None

    # -- Engine metadata cache ---------------------------------------------
    engine_metadata_cache: list[JsonObject] | None = None
    engine_metadata_runtime_sig: str | None = None

    # -- Sealed run-artifact contract --------------------------------------
    sealed_schedule_hash: str | None = None
    sealed_resume_hash: str | None = None


__all__ = ["SpsaRunnerState"]
