import { METRIC_ZERO_THRESHOLD } from './thresholds';

export type ParameterInsightStatus = 'clamped' | 'boundary' | null;

export type ParameterCorrelationEntry = {
    name: string;
    value: number;
};

export type ParameterInsight = {
    name: string;
    initialValue: number | null;
    currentValue: number | null;
    baselineValue: number | null;
    delta: number | null;
    gradient: number | null;
    latestDelta: number | null;
    deltaRatio: number | null;
    gradientEffect: number | null;
    latestDeltaRatio: number | null;
    driftRatio: number | null;
    rangeScale: number | null;
    variantsTracked: number;
    latestVariant: number | null;
    latestVariantValue: number | null;
    evolution: number[];
    baselineEvolution: number[];
    variantIndexes: number[];
    displayIndexes: number[];
    pendingFlags: boolean[];
    ltcRejectedFlags: boolean[];
    ltcDecisionRejectedFlags?: boolean[];
    minEvolution: number | null;
    maxEvolution: number | null;
    correlated: ParameterCorrelationEntry[];
    status: ParameterInsightStatus;
};

export type ParameterSelectionRequest = {
    name: string;
    scrollIntoView: boolean;
};

function normalizeMetricValue(value: number | null): number | null {
    if (value === null || typeof value !== 'number' || !Number.isFinite(value)) return null;
    return Math.abs(value) <= METRIC_ZERO_THRESHOLD ? 0 : value;
}

export function formatParameterValue(value: number | null): string {
    const normalized = normalizeMetricValue(value);
    if (normalized === null) return '—';
    const abs = Math.abs(normalized);
    if (abs !== 0 && abs < 1e-3) {
        return normalized.toExponential(1);
    }
    if (abs >= 1e6) {
        return normalized.toExponential(1);
    }
    return normalized.toFixed(3);
}
