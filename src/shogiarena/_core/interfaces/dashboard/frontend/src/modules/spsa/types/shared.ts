import type { JsonObject } from '@/types/shared';

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

export interface SpsaRefreshOptions {
    readonly force?: boolean;
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
