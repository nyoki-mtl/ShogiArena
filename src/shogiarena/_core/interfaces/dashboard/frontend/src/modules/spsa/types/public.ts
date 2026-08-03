import type { NormalizedSpsaUpdateDetail, SpsaDashboardState } from './internal';
import type { NormalizedTournamentSummary, ParsedTimeControlSpec } from '@/modules/tournament/types';
import type { JsonObject } from '@/types/shared';
import type { SpsaDetailInclude, SpsaDetailViewMode, SpsaDetailWindowMode } from '@/modules/spsa/constants';

export type { SpsaDetailInclude, SpsaDetailViewMode, SpsaDetailWindowMode } from '@/modules/spsa/constants';

export type SpsaTabId = 'overview' | 'updates' | 'correlation' | 'convergence' | 'export';

export type AnalysisStatus = 'ready' | 'warming' | 'error';

export interface SpsaSummaryResponse {
    mode?: string;
    experiment_name?: string | null;
    wins: number;
    losses: number;
    draws: number;
    elo?: number | null;
    btd_elo?: number | null;
    btd_se?: number | null;
    btd_los?: number | null;
    games_total?: number;
    draw_rate?: number | null;
    tuned_black_wins?: number;
    tuned_black_losses?: number;
    tuned_white_wins?: number;
    tuned_white_losses?: number;
    total?: number | null;
    completed?: number;
    last_update_idx?: number | null;
    step_mean_20?: number | null;
    step_std_20?: number | null;
    recent_steps?: number[];
    delta_norm_last?: number | null;
    eta_seconds?: number | null;
    ltc_regression?: SpsaLtcSummary | null;
    engine_time_controls?: Record<string, string>;
    default_time_control?: string | null;
    engines?: string[];
    engine_stats?: Record<string, { wins?: number; losses?: number; draws?: number; games?: number }>;
    engine_instances?: Record<string, string | null | undefined>;
    engine_meta?: Record<string, JsonObject>;
    operational_status?: JsonObject;
    [key: string]: unknown;
}

export interface SpsaLtcBestEstimate {
    mean?: number | null;
    variance?: number | null;
    sigma?: number | null;
    lower?: number | null;
    upper?: number | null;
    source?: string | null;
    composition_depth?: number | null;
    prob_positive?: number | null;
    [key: string]: unknown;
}

export interface SpsaLtcSummary {
    enabled: boolean;
    status: string;
    config?: JsonObject | null;
    last_update_idx?: number | null;
    winrate?: number | null;
    elo?: number | null;
    pairs_played?: number | null;
    sprt?: JsonObject | null;
    sprt_decision?: string | null;
    latest?: SpsaLtcResultEntry | null;
    history_size?: number;
    best_estimate?: SpsaLtcBestEstimate | null;
    [key: string]: unknown;
}

export interface SpsaLtcResultEntry {
    update_idx?: number;
    status?: string;
    winrate?: number | null;
    elo?: number | null;
    timestamp?: number | null;
    baseline_update_idx?: number | null;
    ordinal?: number | null;
    variant_idx?: number | null;
    tuned_wins?: number | null;
    baseline_wins?: number | null;
    draws?: number | null;
    total_games?: number | null;
    delta_elo_mean?: number | null;
    delta_elo_variance?: number | null;
    delta_effective_games?: number | null;
    best_elo_mean_prior?: number | null;
    best_elo_variance_prior?: number | null;
    best_elo_sigma_prior?: number | null;
    best_elo_mean?: number | null;
    best_elo_variance?: number | null;
    best_elo_sigma?: number | null;
    best_elo_lower_1sigma?: number | null;
    best_elo_upper_1sigma?: number | null;
    best_estimate_source?: string | null;
    best_composition_depth?: number | null;
    best_update_idx?: number | null;
    best_positive_probability?: number | null;
    best_positive_probability_prior?: number | null;
    sprt?: JsonObject | null;
    sprt_result?: string | null;
    sprt_elo?: number | null;
    sprt_games?: number | null;
    sprt_llr?: number | null;
    sprt_lower_bound?: number | null;
    sprt_upper_bound?: number | null;
    sprt_winrate?: number | null;
    sprt_anchor_mean?: number | null;
    sprt_point_value?: number | null;
    direct_vs_initial?: boolean | null;
    composition_aligned_with_best?: boolean | null;
    [key: string]: unknown;
}

export interface SpsaLtcRegressionPayload {
    status?: string | null;
    winrate?: number | null;
    elo?: number | null;
    tuned_wins?: number;
    baseline_wins?: number;
    draws?: number;
    total_games?: number;
    total_pairs?: number | null;
    pairs_played?: number | null;
    is_accepted?: boolean | null;
    baseline_update_idx?: number | null;
    baseline_variant_token?: string | null;
    tuned_variant_token?: string | null;
    started_at?: number | string | null;
    completed_at?: number | string | null;
    fail_reasons?: unknown;
    sprt?: JsonObject | null;
    sprt_decision?: string | null;
    [key: string]: unknown;
}

export interface SpsaLtcResultsResponse {
    total: number;
    results: SpsaLtcResultEntry[];
    summary: SpsaLtcSummary;
}

export interface SpsaParameterEntry {
    name: string;
    type: string;
    v: number;
    min: number;
    max: number;
    step: number;
    delta: number;
    comment?: string;
    is_not_used?: boolean;
}

export interface SpsaParamsResponse {
    params: SpsaParameterEntry[];
    variant_id: string | null;
    num_params: number;
    num_used: number;
    num_clamped: number;
    clamped_ratio: number | null;
    initial_params: Record<string, number> | null;
    diffs: Record<string, number> | null;
    initial_variant_id?: string | null;
    [key: string]: unknown;
}

export interface SpsaUpdateEntry {
    update_idx: number;
    timestamp?: number;
    started_at?: string | number | null;
    ended_at?: string | number | null;
    delta_norm?: number;
    step?: number;
    s_plus?: number;
    s_minus?: number;
    games_completed?: number;
    gradients?: Record<string, number>;
    params?: Record<string, number> | SpsaParameterEntry[];
    deltas?: Record<string, number>;
    payload?: JsonObject;
    variant_id?: string | null;
    wins?: number;
    losses?: number;
    draws?: number;
    btd_elo?: number;
    phase_wdl?: Record<string, { wins?: number; losses?: number; draws?: number }>;
    perturbations?: {
        plus?: Record<string, number>;
        minus?: Record<string, number>;
    };
    is_pending?: boolean;
    c_k?: number;
    a_k?: number;
    ltc_regression?: SpsaLtcRegressionPayload | null;
    has_ltc_regression?: boolean;
    is_ltc_rejected?: boolean;
    ltc_reverted_to?: number | null;
    [key: string]: unknown;
}

export interface SpsaUpdateProgress {
    completed: number;
    total: number | null;
    percent?: number | null;
}

export interface SpsaUpdatesResponse {
    updates: SpsaUpdateEntry[];
    total: number;
    limit: number;
    offset: number;
    has_more: boolean;
    progress?: SpsaUpdateProgress;
    [key: string]: unknown;
}

export interface SpsaGameRecord {
    game_id: string;
    black_player: string | null;
    white_player: string | null;
    game_result: string | null;
    num_moves: number | null;
    phase?: string | null;
    status?: string | null;
    assigned_instance?: string | null;
    round?: number | null;
    start_time?: string | null;
    end_time?: string | null;
}

export interface SpsaUpdateDetailResponse {
    update_idx: number;
    engines: { baseline: string | null; tuned: string | null };
    wdl: { wins: number; losses: number; draws: number };
    variant_id: string | null;
    params: Record<string, number> | null;
    gradients: Record<string, number> | null;
    deltas: Record<string, number> | null;
    s_plus: number | null;
    s_minus: number | null;
    step: number | null;
    games: SpsaGameRecord[];
    games_count: number;
    ltc_games?: SpsaGameRecord[];
    ltc_games_count?: number;
    payload?: JsonObject;
    phase_wdl?: Record<string, { wins?: number; losses?: number; draws?: number }>;
    perturbations?: {
        plus?: Record<string, number>;
        minus?: Record<string, number>;
    };
    is_pending?: boolean;
    c_k?: number | null;
    a_k?: number | null;
    ltc_regression?: SpsaLtcRegressionPayload | null;
    has_ltc_regression?: boolean;
    [key: string]: unknown;
}

export interface SpsaParameterTimelinePoint {
    update_idx: number;
    actual: number;
    baseline: number;
    pending: boolean;
    ltc_invalidated: boolean;
    ltc_decision: 'accepted' | 'rejected' | null;
}

export interface SpsaCorrelationResponse {
    status: AnalysisStatus;
    correlations?: Record<string, number>;
    parameter_evolution?: Record<string, number[]>;
    gradient_evolution?: Record<string, number[]>;
    step_evolution?: number[];
    parameter_names?: string[];
    total?: number;
    num_updates?: number;
    parameter_timeline?: Record<string, SpsaParameterTimelinePoint[]>;
    message?: string;
    reason?: string;
    retry_after_ms?: number;
    updated_at?: number;
    [key: string]: unknown;
}

export interface SpsaConvergenceResponse {
    status: AnalysisStatus;
    convergence_metrics?: {
        is_converging?: boolean;
        recent_avg_delta_norm?: number;
        overall_avg_delta_norm?: number;
        recent_std_delta_norm?: number;
        trend_slope?: number;
        convergence_confidence?: number;
        recent_mean_vector_delta_norm?: number;
        recent_avg_mean_vector_delta_norm?: number;
    };
    prediction?: {
        estimated_updates_to_convergence?: number;
        convergence_probability?: number;
    };
    num_updates_analyzed?: number;
    delta_norm_history?: number[];
    delta_mean_vector_norm_history?: (number | null)[];
    delta_mean_vector_window?: number;
    message?: string;
    required_updates?: number;
    available_updates?: number;
    required_delta_norms?: number;
    available_delta_norms?: number;
    pending_updates?: number;
    total_updates_observed?: number;
    convergence_probability_history?: number[];
    convergence_confidence_history?: number[];
    convergence_history_indices?: number[];
    mobility_series?: {
        gain_ak?: number[];
        variant_indices?: number[];
    };
    ltc_results?: SpsaLtcResultsResponse;
    reason?: string;
    retry_after_ms?: number;
    updated_at?: number;
    [key: string]: unknown;
}

export interface SpsaRefreshOptions {
    readonly force?: boolean;
    readonly skipParams?: boolean;
}

export interface SpsaUpdateDetailRequestOptions {
    readonly view?: SpsaDetailViewMode;
    readonly include?: SpsaDetailInclude | readonly SpsaDetailInclude[];
    readonly window?: SpsaDetailWindowMode;
}

export interface DashboardSpsaPublicApi {
    connect: () => void;
    disconnect: (reason?: string) => void;
    getStateSnapshot: () => Readonly<SpsaDashboardState>;
    setActive: (active: boolean) => void;
    fetchUpdateDetail: (
        updateIdx: number,
        options?: SpsaUpdateDetailRequestOptions,
    ) => Promise<NormalizedSpsaUpdateDetail>;
    fetchCorrelationAnalysis: () => Promise<SpsaCorrelationResponse>;
    fetchConvergenceAnalysis: () => Promise<SpsaConvergenceResponse>;
    fetchLtcSummary: () => Promise<SpsaLtcSummary>;
    fetchLtcResults: (limit?: number) => Promise<SpsaLtcResultsResponse>;
    getLtcResultsSnapshot?: (limit?: number) => SpsaLtcResultsResponse | null;
    switchTab: (tab: SpsaTabId) => void;
    focusUpdate: (updateIdx: number, options?: { scroll?: boolean }) => void;
    notifyTabChange?: (tab: SpsaTabId) => void;
    getNormalizedSummary?: () => NormalizedTournamentSummary | null;
    parseTimeControlSpec?: (spec: string) => ParsedTimeControlSpec;
    formatTimeControlShort?: (spec: string) => string;
    ensureDetailHydration?: (trigger?: 'updates' | 'analysis' | 'ltc') => void;
    recordDetailPayloadMetrics?: (
        payload: unknown,
        context?: { view?: SpsaDetailViewMode; includeCount?: number; window?: SpsaDetailWindowMode | 'full' },
    ) => void;
}

export interface DashboardSpsaApi extends DashboardSpsaPublicApi {}
