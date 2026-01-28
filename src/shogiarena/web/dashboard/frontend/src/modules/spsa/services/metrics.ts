import {
    recordLiveDiagnosticsMetric,
    startDiagnosticsStopwatch,
    type LiveDiagnosticsStopwatch,
} from '@/modules/live/utils/liveNamespace';
import { DETAIL_FETCH_HYDRATION_METRIC, DETAIL_PAYLOAD_WARNING_THRESHOLD_KB } from './api.constants';
import type { SpsaDetailViewMode, SpsaDetailWindowMode } from '@/modules/spsa/types';

export type DetailPayloadContext = {
    view?: SpsaDetailViewMode;
    includeCount?: number;
    window?: SpsaDetailWindowMode | 'full';
};

type MetricsApi = {
    startHydrationAttempt: (metric: string) => LiveDiagnosticsStopwatch;
    recordHydrationCacheHit: (metric: string) => void;
    recordDetailPayloadMetrics: (payload: unknown, context?: DetailPayloadContext) => void;
    measurePayloadBytes: (payload: unknown) => number;
};

export function createMetricsHelpers(): MetricsApi {
    const sharedTextEncoder: TextEncoder | null = typeof TextEncoder === 'function' ? new TextEncoder() : null;

    const measurePayloadBytes = (payload: unknown): number => {
        try {
            const serialized = typeof payload === 'string' ? payload : JSON.stringify(payload);
            if (!serialized) {
                return 0;
            }
            if (sharedTextEncoder) {
                return sharedTextEncoder.encode(serialized).byteLength;
            }
            return serialized.length;
        } catch {
            return 0;
        }
    };

    const recordDetailPayloadMetrics = (payload: unknown, context?: DetailPayloadContext): void => {
        const totalBytes = measurePayloadBytes(payload);
        if (totalBytes <= 0) {
            return;
        }
        const payloadKb = totalBytes / 1024;
        const metricUpdate: Record<string, number> = { payloadKb };
        if (context?.view === 'slim') {
            metricUpdate.detailPayloadKbSlim = payloadKb;
        } else if (context?.view === 'full') {
            metricUpdate.detailPayloadKbFull = payloadKb;
        }
        if (typeof context?.includeCount === 'number' && Number.isFinite(context.includeCount)) {
            metricUpdate.detailIncludeCount = context.includeCount;
        }
        recordLiveDiagnosticsMetric(DETAIL_FETCH_HYDRATION_METRIC, metricUpdate);
        if (payloadKb >= DETAIL_PAYLOAD_WARNING_THRESHOLD_KB) {
            console.warn(
                `[SPSA] Update detail payload ${payloadKb.toFixed(1)}KB exceeds the ${DETAIL_PAYLOAD_WARNING_THRESHOLD_KB}KB guideline.`,
            );
        }
    };

    const startHydrationAttempt = (metric: string): LiveDiagnosticsStopwatch => startDiagnosticsStopwatch(metric);
    const recordHydrationCacheHit = (metric: string): void => {
        recordLiveDiagnosticsMetric(metric, { cacheHit: 1 });
    };

    return {
        startHydrationAttempt,
        recordHydrationCacheHit,
        recordDetailPayloadMetrics,
        measurePayloadBytes,
    };
}
