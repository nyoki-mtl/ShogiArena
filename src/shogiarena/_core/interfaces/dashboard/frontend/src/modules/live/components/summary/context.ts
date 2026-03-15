import { normalizeTournamentSummary } from '@/modules/tournament/services/normalizers';
import type {
    NormalizedTournamentSummary,
    TournamentDashboardAPI,
    TournamentSummary,
} from '@/modules/tournament/types';
import type { LiveNamespaceOwner } from '@/modules/live/utils/liveNamespace';
import { reportDashboardRecoverableFailure } from '@/modules/shared/utils/errors';
import { summaryStore } from '@/store';

export interface SummaryWindowBase extends LiveNamespaceOwner {
    DashboardTournament?: TournamentDashboardAPI;
    ARENA_DISABLE_SPSA?: boolean;
}

export interface SummarySnapshot {
    normalized: NormalizedTournamentSummary;
    raw: TournamentSummary;
}

export interface SummaryDataAccess {
    resolveSummarySnapshot: () => SummarySnapshot;
    getFinalRatingsFromSummary: () => Map<string, number>;
}

export function createSummaryDataAccess(owner: SummaryWindowBase): SummaryDataAccess {
    const resolveSummarySnapshot = (): SummarySnapshot => {
        const tournament = owner.DashboardTournament;
        if (tournament && typeof tournament.getNormalizedSummary === 'function') {
            const normalized = tournament.getNormalizedSummary();
            if (normalized) {
                return { normalized, raw: normalized.raw as TournamentSummary };
            }
        }

        const rawSummary = summaryStore.getRawSummary('tournament');
        if (rawSummary) {
            try {
                const normalized = normalizeTournamentSummary(rawSummary);
                return { normalized, raw: normalized.raw as TournamentSummary };
            } catch (error) {
                // Live summary module can remain installed even when other tabs emit `summary:update`
                // (e.g. SPSA/Convergence). Those payloads may be temporarily malformed; do not crash
                // the whole dashboard.
                reportDashboardRecoverableFailure(error, {
                    scope: 'Live.Summary.Validation',
                    userMessage: 'Summary payload was invalid; rendering fallback summary.',
                });
                const fallback = normalizeTournamentSummary({} as unknown as TournamentSummary);
                return { normalized: fallback, raw: fallback.raw as TournamentSummary };
            }
        }

        throw new Error('Tournament summary is unavailable for live summary module');
    };

    const getFinalRatingsFromSummary = (): Map<string, number> => {
        const tournament = owner.DashboardTournament;
        if (tournament && typeof tournament.getFinalRatingsFromSummary === 'function') {
            return tournament.getFinalRatingsFromSummary();
        }

        const { normalized } = resolveSummarySnapshot();
        const ratingInitial = normalized.ratingInitial;
        const ratings = new Map<string, number>();
        const btdRatings = normalized.btd?.ratings ?? null;

        if (btdRatings) {
            for (const [engineName, entry] of Object.entries(btdRatings)) {
                const mean = entry?.mean;
                if (typeof mean === 'number' && Number.isFinite(mean)) {
                    ratings.set(engineName, mean + ratingInitial);
                }
            }
        }

        normalized.engines.forEach((name) => {
            if (!name) return;
            if (!ratings.has(name)) {
                ratings.set(name, ratingInitial);
            }
        });

        return ratings;
    };

    return {
        resolveSummarySnapshot,
        getFinalRatingsFromSummary,
    } satisfies SummaryDataAccess;
}
