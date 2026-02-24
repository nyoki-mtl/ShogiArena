"""SPSA-related backend modules for the arena dashboard.

This package contains all SPSA (Simultaneous Perturbation Stochastic Approximation)
functionality including API handlers, data services, and streaming endpoints.
"""

from __future__ import annotations

from .api import SPSAAPI
from .bradley_terry import (
    BradleyTerryEstimate,
    BradleyTerryMatch,
    estimate_bradley_terry,
    theta_to_elo,
)
from .data_service import SpsaDataService
from .detail_view import DetailViewConfig, apply_detail_view, parse_detail_view_config
from .event_service import SpsaEventService
from .ltc_service import SpsaLtcService
from .params_service import SpsaParamsService
from .store import SpsaStore
from .streams import SpsaStreams
from .summary_service import SpsaSummaryService
from .types import (
    ConvergenceAnalysis,
    ConvergenceMetrics,
    ConvergencePrediction,
    CorrelationAnalysis,
    GameBriefEntry,
    GameListEntry,
    LtcBestEstimate,
    LtcRegressionDetail,
    LtcSummary,
    MobilitySeriesPayload,
    ParamEntry,
    ParameterTimelineEntry,
    ParamsPayload,
    ProgressSnapshot,
    SpsaSummaryGames,
    SpsaSummaryPayload,
    UpdateDetailResponse,
    UpdateEntry,
    WdlCounts,
)
from .utils import (
    coerce_float,
    coerce_int,
    coerce_timestamp_ms,
    compute_variant_id_safe,
    extract_variant_from_game_id,
    format_variant_label,
    resolve_variant_id,
    timestamp_to_iso,
    variant_token,
)

__all__ = [
    # API
    "SPSAAPI",
    # Services
    "SpsaDataService",
    "SpsaEventService",
    "SpsaLtcService",
    "SpsaParamsService",
    "SpsaStore",
    "SpsaStreams",
    "SpsaSummaryService",
    # Bradley-Terry
    "BradleyTerryEstimate",
    "BradleyTerryMatch",
    "estimate_bradley_terry",
    "theta_to_elo",
    # Detail view
    "DetailViewConfig",
    "apply_detail_view",
    "parse_detail_view_config",
    # Types
    "ConvergenceAnalysis",
    "ConvergenceMetrics",
    "ConvergencePrediction",
    "CorrelationAnalysis",
    "GameBriefEntry",
    "GameListEntry",
    "LtcBestEstimate",
    "LtcRegressionDetail",
    "LtcSummary",
    "MobilitySeriesPayload",
    "ParamEntry",
    "ParameterTimelineEntry",
    "ParamsPayload",
    "ProgressSnapshot",
    "SpsaSummaryGames",
    "SpsaSummaryPayload",
    "UpdateDetailResponse",
    "UpdateEntry",
    "WdlCounts",
    # Utils
    "coerce_float",
    "coerce_int",
    "coerce_timestamp_ms",
    "compute_variant_id_safe",
    "extract_variant_from_game_id",
    "format_variant_label",
    "resolve_variant_id",
    "timestamp_to_iso",
    "variant_token",
]
