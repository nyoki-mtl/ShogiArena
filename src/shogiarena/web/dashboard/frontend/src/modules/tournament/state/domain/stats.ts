import type { StatsDomainState } from '@/modules/tournament/types';

export function createStatsDomainState(): StatsDomainState {
    return {
        normalizedSummary: null,
        gamesList: null,
        gamesListCache: null,
        matchupCache: new Map(),
        openingStatsRendered: false,
        lastOpeningStatsCompletedGames: null,
        standingsSort: { key: null, direction: null },
        openingStatsSort: { key: 'games', direction: 'desc' },
        openingStatsData: null,
        engineFullOptions: new Map(),
        liveViewSnapshot: null,
    };
}
