import { isAbortError } from './errors';
import { BOOTSTRAP_RETRY_DELAY_MS, INITIAL_BOOTSTRAP_UPDATES_LIMIT, MAX_BOOTSTRAP_ATTEMPTS } from './api-constants';

import type { SpsaFetchers } from './fetchers';
import type { DeferredHydrator } from '@/modules/shared/utils/hydration';

type BootstrapDeps = {
    fetchers: Pick<SpsaFetchers, 'requestSummary' | 'requestParams' | 'requestUpdates' | 'resetAnalysisFetches'>;
    hydrators: {
        updatesHydrator: DeferredHydrator;
        analysisHydrator: DeferredHydrator;
        ltcHydrator: DeferredHydrator;
    };
    resetCaches: () => void;
    reportRecoverableFailure: (context: string, error: unknown, options?: { notifyOffline?: boolean }) => void;
    startRealtimeStreams: () => void;
};

export type BootstrapManager = {
    beginBootstrapSequence: () => Promise<void>;
    resetHydrationState: () => void;
};

export function createBootstrapManager(deps: BootstrapDeps): BootstrapManager {
    const { fetchers, hydrators, resetCaches, reportRecoverableFailure, startRealtimeStreams } = deps;

    let bootstrapStarted = false;
    let bootstrapAttempts = 0;
    let bootstrapRetryTimer: number | null = null;
    let bootstrapPromise: Promise<void> | null = null;
    let bootstrapAbortController: AbortController | null = null;
    let generation = 0;

    const resetHydrationState = (): void => {
        generation += 1;
        bootstrapAbortController?.abort();
        bootstrapAbortController = null;
        bootstrapStarted = false;
        bootstrapAttempts = 0;
        if (bootstrapRetryTimer !== null) {
            window.clearTimeout(bootstrapRetryTimer);
            bootstrapRetryTimer = null;
        }
        bootstrapPromise = null;
        hydrators.updatesHydrator.reset();
        hydrators.analysisHydrator.reset();
        hydrators.ltcHydrator.reset();
        fetchers.resetAnalysisFetches();
        resetCaches();
    };

    const scheduleBootstrapRetry = (): void => {
        if (bootstrapRetryTimer !== null) {
            return;
        }
        if (bootstrapAttempts >= MAX_BOOTSTRAP_ATTEMPTS) {
            console.warn('[SPSA] bootstrap retry limit reached');
            return;
        }
        bootstrapRetryTimer = window.setTimeout(() => {
            bootstrapRetryTimer = null;
            void beginBootstrapSequence();
        }, BOOTSTRAP_RETRY_DELAY_MS);
    };

    const bootstrapInitialData = async (): Promise<void> => {
        if (bootstrapStarted) {
            return bootstrapPromise ?? Promise.resolve();
        }
        bootstrapStarted = true;
        bootstrapAttempts += 1;
        const attemptGeneration = generation;
        const abortController = new AbortController();
        bootstrapAbortController = abortController;
        const signal = abortController.signal;
        const attempt = (async () => {
            const withBootstrapReporting = async <T>(promise: Promise<T>, context: string): Promise<T> => {
                try {
                    return await promise;
                } catch (error) {
                    if (!isAbortError(error)) {
                        reportRecoverableFailure(context, error, { notifyOffline: true });
                    }
                    throw error;
                }
            };

            await Promise.all([
                withBootstrapReporting(
                    fetchers.requestSummary(true, signal, { propagateError: true }),
                    'SpsaApi.bootstrap(summary)',
                ),
                withBootstrapReporting(
                    fetchers.requestParams(true, signal, { propagateError: true }),
                    'SpsaApi.bootstrap(params)',
                ),
                withBootstrapReporting(
                    fetchers.requestUpdates({
                        limit: INITIAL_BOOTSTRAP_UPDATES_LIMIT,
                        offset: 0,
                        signal,
                        propagateError: true,
                    }),
                    'SpsaApi.bootstrap(updates)',
                ),
            ]);
            bootstrapAttempts = 0;
        })()
            .catch((error) => {
                if (attemptGeneration !== generation) {
                    return;
                }
                if (!isAbortError(error)) {
                    reportRecoverableFailure('SpsaApi.bootstrap', error, { notifyOffline: true });
                    scheduleBootstrapRetry();
                }
                throw error;
            })
            .finally(() => {
                if (attemptGeneration !== generation) {
                    return;
                }
                bootstrapStarted = false;
                bootstrapPromise = null;
                if (bootstrapAbortController === abortController) {
                    bootstrapAbortController = null;
                }
            });

        bootstrapPromise = attempt;
        return attempt;
    };

    const beginBootstrapSequence = (): Promise<void> => {
        const expectedGeneration = generation;
        return bootstrapInitialData()
            .then(() => {
                if (expectedGeneration === generation) {
                    startRealtimeStreams();
                }
            })
            .catch((error) => {
                throw error;
            });
    };

    return {
        beginBootstrapSequence,
        resetHydrationState,
    };
}
