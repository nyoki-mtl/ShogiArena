import type { DashboardCore } from '@/types/dashboard';
import type { LiveViewSnapshot } from '@/modules/live/types';

export interface MatchCounts {
    wins: number;
    losses: number;
    draws: number;
    games: number;
}

export interface MatchColorStats extends MatchCounts {
    win_rate?: number | null;
}

export interface MatchTimelinePoint extends MatchCounts {
    game_index: number;
    win_rate?: number | null;
    win_rate_ci95?: { lower?: number | null; upper?: number | null };
    elo_estimate?: number | null;
    black?: MatchColorStats;
    white?: MatchColorStats;
}

export interface MatchSummaryPayload {
    mode?: string;
    tested?: string;
    baseline?: string;
    live_view?: LiveViewSnapshot | null;
    games?: {
        completed?: number;
        total?: number | null;
        wins?: number;
        losses?: number;
        draws?: number;
    };
    win_rate?: number | null;
    win_rate_ci95?: { lower?: number | null; upper?: number | null };
    elo_estimate?: number | null;
    elo_ci95?: { lower?: number | null; upper?: number | null };
    colors?: { black?: MatchCounts; white?: MatchCounts };
    timeline?: MatchTimelinePoint[];
    timestamp?: string;
}

export interface MatchModuleState {
    active: boolean;
    timerId: number | null;
}

export interface DashboardMatchApi {
    setActive: (active: boolean) => void;
    refresh: () => void;
    getState?: () => MatchModuleState;
}

export interface MatchWindow extends Window {
    DashboardCore?: DashboardCore;
    DashboardMatch?: DashboardMatchApi;
}
