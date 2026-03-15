import { createDeferredHydrator } from '@/modules/shared';
import type { LiveDiagnosticsStopwatch } from '@/modules/live/utils/liveNamespace';
import { DETAIL_HYDRATION_DELAY_MS } from './api.constants';
import type { SpsaStreamCacheState } from './streams';

export function createStreamCacheState(): SpsaStreamCacheState {
    return {
        latestConvergenceData: null,
        convergenceCacheExpiry: 0,
        latestCorrelationData: null,
        correlationCache: null,
        ltcSummaryCache: null,
        ltcResultsCache: new Map(),
        latestLtcResults: null,
        latestLtcResultsAt: null,
        latestLtcResultsSeq: null,
        ltcResultsGapDetected: false,
        ltcResultsStreamOpenedAt: null,
    };
}

export function createScopedHydrator(
    metric: string,
    hydrateFn: () => Promise<void>,
    scope: string,
    startHydrationAttempt: (metric: string) => LiveDiagnosticsStopwatch,
    reportRecoverableFailure: (context: string, error: unknown, options?: { notifyOffline?: boolean }) => void,
) {
    return createDeferredHydrator({
        delayMs: DETAIL_HYDRATION_DELAY_MS,
        hydrate: async () => {
            const attempt = startHydrationAttempt(metric);
            try {
                await hydrateFn();
                attempt.succeed();
            } catch (error) {
                attempt.fail();
                throw error;
            }
        },
        onError: (error) => {
            reportRecoverableFailure(scope, error, { notifyOffline: true });
        },
    });
}
