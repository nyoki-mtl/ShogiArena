import type { SpsaConvergenceResponse } from '@/modules/spsa/types';

import { state, resetConvergenceMetricsSnapshot } from './store';

function resolveMetric(value: unknown): number | null {
    if (typeof value === 'number' && Number.isFinite(value)) {
        return value;
    }
    if (typeof value === 'string') {
        const parsed = Number.parseFloat(value);
        if (Number.isFinite(parsed)) {
            return parsed;
        }
    }
    return null;
}

function assignMetrics(partial: {
    recentAvgDeltaNorm: number | null;
    recentMeanVectorDeltaNorm: number | null;
    recentAvgMeanVectorDeltaNorm: number | null;
    sampleSize: number | null;
}): void {
    state.convergenceMetrics = {
        recentAvgDeltaNorm: partial.recentAvgDeltaNorm,
        recentMeanVectorDeltaNorm: partial.recentMeanVectorDeltaNorm,
        recentAvgMeanVectorDeltaNorm: partial.recentAvgMeanVectorDeltaNorm,
        sampleSize: partial.sampleSize,
        updatedAt: Date.now(),
    };
}

export function applyConvergenceMetrics(response: SpsaConvergenceResponse | null): void {
    if (!response || typeof response !== 'object') {
        resetConvergenceMetricsSnapshot();
        return;
    }
    const rawMetrics = response.convergence_metrics;
    if (!rawMetrics || typeof rawMetrics !== 'object') {
        assignMetrics({
            recentAvgDeltaNorm: null,
            recentMeanVectorDeltaNorm: null,
            recentAvgMeanVectorDeltaNorm: null,
            sampleSize: null,
        });
        return;
    }
    const record = rawMetrics as Record<string, unknown>;
    // Get actual sample size from available_delta_norms
    const sampleSize = resolveMetric(response.available_delta_norms);
    assignMetrics({
        recentAvgDeltaNorm: resolveMetric(record.recent_avg_delta_norm),
        recentMeanVectorDeltaNorm: resolveMetric(record.recent_mean_vector_delta_norm),
        recentAvgMeanVectorDeltaNorm: resolveMetric(record.recent_avg_mean_vector_delta_norm),
        sampleSize,
    });
}
