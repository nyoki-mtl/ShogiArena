import type {
    TournamentDashboardAPI,
    TournamentRulesConfig,
    TournamentSprtSummary,
    TournamentSummary,
} from '@/modules/tournament/types';
import type { DashboardCore } from '@/types/dashboard';
import type { DashboardNavigationApi, DashboardTabId } from '@/types/globals';
import type { JsonObject } from '@/types/shared';
import type { DashboardRulesApi } from './public';

export interface RuleCard {
    title?: string;
    content: Node;
}

export type RulesListEntryAction =
    | { type: 'focus-engine'; engine: string }
    | { type: 'open-tab'; tab: DashboardTabId; selector?: string };

export interface RulesListEntry {
    label?: string;
    value?: string;
    action?: RulesListEntryAction | null;
}

export interface AdjudicationConfig {
    max_plies?: number;
    enable_max_plies?: boolean;
    max_moves_to_draw?: number;
    max_moves?: number;
    resign_threshold_cp?: number;
    resign_confirm_count?: number;
    hysteresis?: number;
    engine_max_ply_option_names?: string[];
    [key: string]: unknown;
}

export interface InitialPositionsInfo {
    type: string;
    source: string | null;
    flip_policy: string | null;
    randomize: boolean;
    count: number | null;
}

export interface SpsaLtcPassCriteria {
    min_winrate?: number | null;
    max_elo_drop?: number | null;
    sprt?: Record<string, unknown> | null;
    [key: string]: unknown;
}

export interface SpsaLtcRulesConfig {
    enabled?: boolean;
    every_n_updates?: number;
    total_pairs?: number;
    time_control?: Record<string, unknown> | null;
    pass_criteria?: SpsaLtcPassCriteria | null;
    [key: string]: unknown;
}

export interface SpsaAlgorithmConfig {
    num_updates?: number;
    mobility?: number;
    scale?: number;
    a0?: number;
    A?: number | null;
    alpha?: number;
    gamma?: number;
    crn_enabled?: boolean;
    int_rounding?: string;
    int_ck_floor?: number;
    update_mode?: string;
    snap_float_to_step?: boolean;
    early_stop?: Record<string, unknown> | null;
    update_batch_size?: number | null;
    inflight_factor?: number;
    ltc_regression?: SpsaLtcRulesConfig | null;
    [key: string]: unknown;
}

export interface RulesSummary extends TournamentSummary {
    rules?: TournamentRulesConfig | JsonObject;
    sprt?: TournamentSprtSummary | JsonObject | null;
    spsaConfig?: SpsaAlgorithmConfig | null;
    repetitionOccurrencesToDraw?: number;
    initialPositions?: JsonObject;
    engineTimeControls?: Record<string, string>;
    defaultTimeControl?: string;
    flipPolicy?: string | null;
    generateConfig?: JsonObject | null;
    recordsOutput?: JsonObject | null;
}

export interface RulesWindow extends Window {
    DashboardCore?: DashboardCore;
    DashboardRules?: DashboardRulesApi;
    DashboardTournament?: TournamentDashboardAPI;
    DashboardSpsa?: import('@/modules/spsa/types').DashboardSpsaPublicApi;
    DashboardNavigation?: DashboardNavigationApi;
}

export type WarnSoftFailure = (context: string, error: unknown) => void;
