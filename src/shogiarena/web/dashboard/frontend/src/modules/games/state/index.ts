import type { GamesScheduleSnapshot, GamesSortDirection, GamesSortKey, NormalizedGameRow } from '@/modules/games/types';

export interface GamesModuleState {
    active: boolean;
    retryTimer: number | null;
    rowsByGame: Map<string, NormalizedGameRow>;
    selectedGameId: string | null;
    instanceFilter: string | null;
    lastSnapshot: GamesScheduleSnapshot | null;
    sortKey: GamesSortKey;
    sortDirection: GamesSortDirection;
    instanceOptions: string[];
    assignmentGameId: string | null;
    gamesUnavailable: boolean;
    consecutiveErrors: number;
    lastErrorMessage: string | null;
    fetchInFlight: boolean;
    searchQuery: string;
    sseConnected: boolean;
    sseRetryDelayMs: number | null;
}

export function createInitialGamesState(): GamesModuleState {
    return {
        active: false,
        retryTimer: null,
        rowsByGame: new Map<string, NormalizedGameRow>(),
        selectedGameId: null,
        instanceFilter: null,
        lastSnapshot: null,
        sortKey: 'order',
        sortDirection: 'asc',
        instanceOptions: [],
        assignmentGameId: null,
        gamesUnavailable: false,
        consecutiveErrors: 0,
        lastErrorMessage: null,
        fetchInFlight: false,
        searchQuery: '',
        sseConnected: false,
        sseRetryDelayMs: null,
    };
}

export const gamesState = createInitialGamesState();
