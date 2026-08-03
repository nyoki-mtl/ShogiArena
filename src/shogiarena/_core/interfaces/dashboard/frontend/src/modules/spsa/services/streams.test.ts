import { describe, expect, it, vi } from 'vitest';
import type { SpsaRefreshOptions } from '@/modules/spsa/types';
import * as revisionFeed from './revision-feed';
import { createSpsaStreams, type SpsaStreamCacheState } from './streams';

function createCacheState(): SpsaStreamCacheState {
    return {
        latestConvergenceData: null,
        convergenceCacheExpiry: 0,
        correlationCache: null,
        ltcSummaryCache: null,
        ltcResultsCache: new Map(),
        latestLtcResultsSnapshot: null,
    };
}

describe('createSpsaStreams', () => {
    it('refreshes the snapshot without refetching the parameter space', async () => {
        // The revision feed fires whenever projected data moves, so this path runs on every
        // poll during a live run. The parameter space cannot change mid-run, so refetching it
        // each time would be pure waste.
        const calls: SpsaRefreshOptions[] = [];
        const spy = vi.spyOn(revisionFeed, 'createRevisionFeedController');

        createSpsaStreams(createCacheState(), {
            getApiBase: () => 'http://localhost',
            isDashboardOffline: () => false,
            reportRecoverableFailure: () => {},
            handleError: () => {},
            refreshAll: async (options?: SpsaRefreshOptions) => {
                calls.push(options ?? {});
            },
            clearSseDisconnectNotice: () => {},
            markAnalysisDataStale: () => {},
        });

        const deps = spy.mock.calls.at(-1)?.[0];
        spy.mockRestore();
        expect(deps, 'createSpsaStreams must build the revision feed controller').toBeDefined();

        await deps?.refreshSnapshot(false);

        expect(calls).toEqual([{ force: true, skipParams: true }]);
    });
});
