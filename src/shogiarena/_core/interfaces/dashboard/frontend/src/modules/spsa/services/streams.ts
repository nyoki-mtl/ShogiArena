import type {
    SpsaConvergenceResponse,
    SpsaCorrelationResponse,
    SpsaLtcResultsResponse,
    SpsaLtcSummary,
    SpsaRefreshOptions,
} from '@/modules/spsa/types';
import { setEventSource } from '../state';
import { createRevisionFeedController } from './revision-feed';

export type SpsaStreamCacheState = {
    latestConvergenceData: SpsaConvergenceResponse | null;
    convergenceCacheExpiry: number;
    correlationCache: { data: SpsaCorrelationResponse; expiresAt: number } | null;
    ltcSummaryCache: { data: SpsaLtcSummary; expiresAt: number } | null;
    ltcResultsCache: Map<number, { data: SpsaLtcResultsResponse; expiresAt: number }>;
    latestLtcResultsSnapshot: SpsaLtcResultsResponse | null;
};

type SpsaStreamDeps = {
    getApiBase: () => string;
    isDashboardOffline: () => boolean;
    reportRecoverableFailure: (
        context: string,
        error: unknown,
        options?: { notifyOffline?: boolean; title?: string },
    ) => void;
    handleError: (message: string, options?: { source?: 'sse' | 'generic' }) => void;
    refreshAll: (options?: SpsaRefreshOptions) => Promise<void>;
    clearSseDisconnectNotice: () => void;
    markAnalysisDataStale: () => void;
};

export type SpsaStreams = {
    startRealtimeStreams: () => void;
    hideRevisionStream: () => void;
    markRevisionOffline: () => void;
    closeRevisionStream: () => void;
    deriveCachedLtcResults: (limit: number) => SpsaLtcResultsResponse | null;
    resetCaches: () => void;
    invalidateCorrelationCache: () => void;
};

export function createSpsaStreams(cacheState: SpsaStreamCacheState, deps: SpsaStreamDeps): SpsaStreams {
    const revisionFeed = createRevisionFeedController({
        buildUrl: () => `${deps.getApiBase()}/api/spsa/revisions/stream`,
        isOffline: deps.isDashboardOffline,
        createEventSource: (url) => new EventSource(url),
        setConnection: (source, status) => setEventSource(source as EventSource | null, status),
        refreshSnapshot: async () => {
            await deps.refreshAll({ force: true });
        },
        markAnalysisDataStale: deps.markAnalysisDataStale,
        clearDisconnectNotice: deps.clearSseDisconnectNotice,
        reportFailure: deps.reportRecoverableFailure,
    });

    const startRealtimeStreams = (): void => {
        if (typeof EventSource !== 'function') {
            deps.handleError('SPSA revision feed requires EventSource support.', { source: 'sse' });
            return;
        }
        revisionFeed.open();
    };

    const resetCaches = (): void => {
        cacheState.latestConvergenceData = null;
        cacheState.convergenceCacheExpiry = 0;
        cacheState.correlationCache = null;
        cacheState.ltcSummaryCache = null;
        cacheState.ltcResultsCache.clear();
        cacheState.latestLtcResultsSnapshot = null;
        revisionFeed.reset();
    };

    const deriveCachedLtcResults = (limit: number): SpsaLtcResultsResponse | null => {
        const cached = cacheState.ltcResultsCache.get(limit);
        if (cached && cached.expiresAt > Date.now()) {
            return cached.data;
        }
        const latest = cacheState.latestLtcResultsSnapshot;
        if (!latest || !Array.isArray(latest.results) || latest.results.length < limit) {
            return null;
        }
        return {
            ...latest,
            results: latest.results.slice(0, limit),
        };
    };

    return {
        startRealtimeStreams,
        hideRevisionStream: revisionFeed.hide,
        markRevisionOffline: revisionFeed.goOffline,
        closeRevisionStream: revisionFeed.close,
        deriveCachedLtcResults,
        resetCaches,
        invalidateCorrelationCache: () => {
            cacheState.correlationCache = null;
        },
    };
}
