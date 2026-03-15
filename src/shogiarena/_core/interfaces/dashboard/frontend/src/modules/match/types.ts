import type { DashboardCore } from '@/types/dashboard';
import type { LiveViewSnapshot } from '@/modules/live/types';

export interface MatchCounts {
    wins: number;
    losses: number;
    draws: number;
    games: number;
}

export interface MatchColorStats extends MatchCounts {
    winRate?: number | null;
}

export interface MatchTimelinePoint extends MatchCounts {
    gameIndex: number;
    winRate?: number | null;
    winRateCi95?: { lower?: number | null; upper?: number | null };
    eloEstimate?: number | null;
    black?: MatchColorStats;
    white?: MatchColorStats;
}

export interface MatchSummaryPayload {
    mode?: string;
    tested?: string;
    baseline?: string;
    liveView?: LiveViewSnapshot | null;
    games?: {
        completed?: number;
        total?: number | null;
        wins?: number;
        losses?: number;
        draws?: number;
    };
    winRate?: number | null;
    winRateCi95?: { lower?: number | null; upper?: number | null };
    eloEstimate?: number | null;
    eloCi95?: { lower?: number | null; upper?: number | null };
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
