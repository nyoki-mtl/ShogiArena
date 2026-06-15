import type { DashboardCore } from '@/types/dashboard';
import type { LiveViewSnapshot } from '@/modules/live/types';

export interface SprtConfigPayload {
    elo0?: number;
    elo1?: number;
    alpha?: number;
    beta?: number;
    min_games?: number;
    max_games?: number | null;
}

export interface SprtStatusPayload {
    llr?: number;
    lower?: number;
    upper?: number;
    decision?: string;
    wins?: number;
    draws?: number;
    losses?: number;
    games?: number;
    win_rate?: number | null;
    elo_estimate?: number | null;
}

export interface SprtTimelinePoint extends SprtStatusPayload {
    game_index: number;
}

export interface SprtSummaryPayload {
    mode?: string;
    tested?: string;
    baseline?: string;
    live_view?: LiveViewSnapshot | null;
    config?: SprtConfigPayload;
    status?: SprtStatusPayload;
    games?: { completed?: number; total?: number | null };
    timeline?: SprtTimelinePoint[];
    timestamp?: string;
}

export interface SprtModuleState {
    active: boolean;
    timerId: number | null;
}

export interface DashboardSprtApi {
    setActive: (active: boolean) => void;
    refresh: () => void;
    getState?: () => SprtModuleState;
}

export interface SprtWindow extends Window {
    DashboardCore?: DashboardCore;
    DashboardSprt?: DashboardSprtApi;
}
