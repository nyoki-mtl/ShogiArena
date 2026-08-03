"""SPSA ダッシュボードバックエンドで使用される TypedDict 定義。

各サービスが返す dict を型付けし、API 契約を明示化する。
``total=False`` で定義されたフィールドはオプションであり、
イベントソースや集計段階によって存在しない場合がある。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypedDict

from shogiarena._core.contexts.dashboard.application.live.view_payloads import LiveViewSnapshot
from shogiarena._core.shared.kernel.json_types import JsonObject, JsonValue
from shogiarena._core.shared.kernel.wdl_counts import WdlCounts

# ---------------------------------------------------------------------------
# Analysis status types
# ---------------------------------------------------------------------------

AnalysisStatus = Literal["ready", "warming", "error"]


@dataclass(frozen=True, slots=True)
class SpsaRevisionState:
    """Revision feed が 1 回のポーリングで観測する状態。

    ``revision`` は ``event_revisions`` に基づく **durable ledger revision** で、
    provenance evidence として `operational_status.ledger.revision` と同じ値を指す。
    このテーブルは variant quarantine / LTC 判定 / terminal でしか増えないため、
    通常の update commit や対局結果では変化しない。

    ``data_generation`` は projector が保持する **投影データの版**で、
    update / pair / game observation / LTC 結果のいずれかが変化するたびに進む。
    dashboard が描画する内容の変化はこちらで検出する。
    """

    run_id: str
    revision: int
    data_generation: int
    is_terminal: bool


class _AnalysisSnapshotBase(TypedDict, total=False):
    """warming/error 時に共通で含まれるメタフィールド。"""

    reason: str
    retry_after_ms: int
    updated_at: int


class CorrelationAnalysisSnapshot(_AnalysisSnapshotBase, total=False):
    """相関分析スナップショット。``status`` は必須。"""

    status: AnalysisStatus
    correlations: dict[str, float]
    parameter_evolution: dict[str, list[float]]
    gradient_evolution: dict[str, list[float]]
    step_evolution: list[float]
    parameter_names: list[str]
    num_updates: int
    parameter_timeline: dict[str, list[ParameterTimelineEntry]]
    message: str


class ConvergenceAnalysisSnapshot(_AnalysisSnapshotBase, total=False):
    """収束分析スナップショット。``status`` は必須。"""

    status: AnalysisStatus
    convergence_metrics: ConvergenceMetrics
    prediction: ConvergencePrediction
    delta_norm_history: list[float]
    delta_mean_vector_norm_history: list[float | None]
    delta_mean_vector_window: int
    recent_delta_norms: list[float]
    recent_step_sizes: list[float]
    required_delta_norms: int
    available_delta_norms: int
    available_updates: int
    pending_updates: int
    total_updates_observed: int
    num_updates_analyzed: int
    mobility_series: MobilitySeriesPayload
    convergence_probability_history: list[float]
    convergence_confidence_history: list[float]
    convergence_history_indices: list[int]
    message: str
    ltc_results: dict[str, JsonValue]


# ---------------------------------------------------------------------------
# Foundation types
# ---------------------------------------------------------------------------


class SpsaSummaryGames(TypedDict):
    """SPSA サマリ内の対局進捗。"""

    completed: int
    total: int


# ---------------------------------------------------------------------------
# Game-related types
# ---------------------------------------------------------------------------


class _GameBriefEntryRequired(TypedDict):
    """対局概要エントリの必須フィールド。"""

    game_id: str


class GameBriefEntry(_GameBriefEntryRequired, total=False):
    """対局一覧に表示される対局概要。"""

    black_player: str | None
    white_player: str | None
    game_result: str | None
    num_moves: int | None
    variant_id: str | None
    phase: str | None
    status: str | None
    assigned_instance: str | None
    round: int | None
    start_time: str | None
    end_time: str | None


class GameListEntry(TypedDict, total=False):
    """``list_games`` が返す対局リストエントリ。"""

    game_id: str
    black_player: str | None
    white_player: str | None
    game_result: str | None
    total_plies: int | None
    end_time: str | None
    initial_sfen: str | None
    time_control_black: str | None
    time_control_white: str | None
    variant_id: str | None
    phase: str | None
    timestamp: int


# ---------------------------------------------------------------------------
# LTC types
# ---------------------------------------------------------------------------


class LtcRegressionDetail(TypedDict, total=False):
    """LTC (Long Time Control) 回帰テストの詳細情報。"""

    status: str
    tuned_wins: int
    baseline_wins: int
    draws: int
    total_games: int
    total_pairs: int | None
    winrate: float | None
    elo: float | None
    pairs_played: int | None
    is_accepted: bool | None
    baseline_update_idx: int | None
    baseline_variant_token: str | None
    tuned_variant_token: str | None
    started_at: int | str | None
    completed_at: int | str | None
    fail_reasons: list[str]
    sprt: JsonObject | None
    sprt_decision: str | None


# ---------------------------------------------------------------------------
# Update types
# ---------------------------------------------------------------------------


class UpdateEntry(TypedDict, total=False):
    """SPSA パラメータ更新エントリ。

    ``collect_updates_from_events`` および ``load_index_updates`` が返す構造。
    """

    update_idx: int
    variant_id: str
    timestamp: int
    started_at: str | None
    ended_at: str | None
    params: dict[str, float]
    gradients: dict[str, float]
    deltas: dict[str, float]
    perturbations: dict[str, dict[str, float]]
    s_plus: float | None
    s_minus: float | None
    step: float | None
    delta_norm: float | None
    c_k: float | None
    a_k: float | None
    is_pending: bool
    wins: int
    losses: int
    draws: int
    phase_wdl: dict[str, WdlCounts]
    games: list[GameBriefEntry]
    games_count: int
    ltc_regression: LtcRegressionDetail | None
    has_ltc_regression: bool
    is_ltc_rejected: bool
    ltc_reverted_to: int
    btd_elo: float | None


class UpdateDetailResponse(TypedDict, total=False):
    """``build_update_detail`` が返す更新詳細レスポンス。"""

    update_idx: int
    run_id: str | None
    ledger_state: str | None
    session_uuids: list[str]
    engines: dict[str, str | None]
    wdl: WdlCounts
    variant_id: str | None
    params: dict[str, float] | None
    gradients: dict[str, float] | None
    deltas: dict[str, float] | None
    s_plus: float | None
    s_minus: float | None
    step: float | None
    perturbations: dict[str, dict[str, float]] | None
    c_k: float | None
    a_k: float | None
    is_pending: bool
    games: list[GameBriefEntry]
    games_count: int
    ltc_games: list[GameBriefEntry]
    ltc_games_count: int
    phase_wdl: dict[str, WdlCounts]
    ltc_regression: LtcRegressionDetail | None
    has_ltc_regression: bool
    payload: JsonObject


# ---------------------------------------------------------------------------
# Progress / Timeline types
# ---------------------------------------------------------------------------


class ProgressSnapshot(TypedDict):
    """``compute_progress_snapshot`` が返す進捗スナップショット。"""

    completed: int
    total: int | None
    percent: float | None


class ParameterTimelineEntry(TypedDict):
    """パラメータタイムラインの1エントリ（相関分析で使用）。"""

    update_idx: int
    actual: float
    baseline: float
    is_pending: bool
    is_ltc_invalidated: bool
    ltc_decision: str | None


class MobilitySeriesPayload(TypedDict):
    """収束解析のモビリティ系列。"""

    gain_ak: list[float]
    variant_indices: list[int]


class CorrelationAnalysis(TypedDict, total=False):
    """``compute_correlation_analysis`` が返す相関分析結果。"""

    correlations: dict[str, float]
    parameter_evolution: dict[str, list[float]]
    gradient_evolution: dict[str, list[float]]
    step_evolution: list[float]
    parameter_names: list[str]
    num_updates: int
    parameter_timeline: dict[str, list[ParameterTimelineEntry]]
    message: str


class ConvergenceMetrics(TypedDict, total=False):
    """収束メトリクス。"""

    recent_avg_delta_norm: float
    overall_avg_delta_norm: float
    recent_std_delta_norm: float
    trend_slope: float
    is_converging: bool
    convergence_confidence: float
    recent_mean_vector_delta_norm: float
    recent_avg_mean_vector_delta_norm: float


class ConvergencePrediction(TypedDict, total=False):
    """収束予測。"""

    remaining_updates_estimate: float
    convergence_probability: float


class ConvergenceAnalysis(TypedDict, total=False):
    """``compute_convergence_analysis`` が返す収束分析結果。"""

    convergence_metrics: ConvergenceMetrics
    prediction: ConvergencePrediction
    delta_norm_history: list[float]
    delta_mean_vector_norm_history: list[float | None]
    delta_mean_vector_window: int
    recent_delta_norms: list[float]
    recent_step_sizes: list[float]
    required_delta_norms: int
    available_delta_norms: int
    available_updates: int
    pending_updates: int
    total_updates_observed: int
    num_updates_analyzed: int
    mobility_series: MobilitySeriesPayload
    convergence_probability_history: list[float]
    convergence_confidence_history: list[float]
    convergence_history_indices: list[int]
    message: str
    ltc_results: dict[str, JsonValue]


class SpsaSummaryPayload(TypedDict, total=False):
    """``compute_summary`` が返す SPSA サマリペイロード。"""

    mode: Literal["spsa"]
    experiment_name: str | None
    wins: int
    losses: int
    draws: int
    elo: float | None
    btd_elo: float | None
    btd_se: float | None
    btd_los: float | None
    games_total: int
    draw_rate: float | None
    tuned_black_wins: int
    tuned_black_losses: int
    tuned_white_wins: int
    tuned_white_losses: int
    games: SpsaSummaryGames
    last_update_idx: int | None
    step_mean_20: float | None
    step_std_20: float | None
    recent_steps: list[float]
    delta_norm_last: float | None
    eta_seconds: int | None
    winrate: float
    progress: float
    recent_step: float
    recent_delta_norm: float
    engine_time_controls: dict[str, str]
    default_time_control: str | None
    engines: list[str]
    engines_meta: list[JsonObject]
    engine_instances: dict[str, str | None]
    engine_stats: dict[str, dict[str, int | float]]
    spsa_config: JsonObject | None
    current_session_uuid: str | None
    resume_boundaries: list[JsonObject]
    operational_status: JsonObject
    live_view: LiveViewSnapshot


# ---------------------------------------------------------------------------
# Params types
# ---------------------------------------------------------------------------


class ParamEntry(TypedDict):
    """パラメータ定義エントリ。"""

    name: str
    type: str
    v: float
    min: float
    max: float
    step: float
    delta: float
    comment: str
    is_not_used: bool


class ParamsPayload(TypedDict, total=False):
    """``build_params_payload`` が返すパラメータペイロード。"""

    params: list[ParamEntry]
    variant_id: str | None
    num_params: int
    num_used: int
    num_clamped: int
    clamped_ratio: float | None
    initial_params: dict[str, float] | None
    diffs: dict[str, float] | None
    initial_variant_id: str | None


class LtcSummary(TypedDict, total=False):
    """``compute_ltc_summary`` が返す LTC サマリ。"""

    is_enabled: bool
    status: str
    config: JsonObject | None
    last_update_idx: int | None
    winrate: float | None
    elo: float | None
    pairs_played: int | None
    sprt: JsonObject | None
    sprt_decision: str | None
    latest: JsonObject | None
    history_size: int
    best_estimate: LtcBestEstimate | None


class LtcBestEstimate(TypedDict, total=False):
    """LTC 最良推定の統計情報。"""

    variant: int
    theta: float
    mean: float | None
    variance: float | None
    sigma: float | None
    lower: float | None
    upper: float | None
    source: str
    prob_positive: float | None
