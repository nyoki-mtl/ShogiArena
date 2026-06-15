import type { SpsaConvergenceResponse, SpsaLtcResultEntry } from '@/modules/spsa/types';
import { computeNiceTicks } from '@/modules/shared/utils/chart';
import { resolveNumeric } from './shared';

export type ProcessedLtcRun = {
    updateIdx: number | null;
    ordinal: number;
    status: string;
    bestMean: number | null;
    bestLower: number | null;
    bestUpper: number | null;
    bestSigma: number | null;
    bestSource: string | null;
    bestDepth: number | null;
    bestPrior: number | null;
    probPositive: number | null;
    deltaMean: number | null;
    deltaVariance: number | null;
    effectiveGames: number | null;
    totalGames: number | null;
    sprtElo: number | null;
    sprtDecision: string | null;
    sprtGames: number | null;
    sprtPoint: number | null;
};

export function normalizeDeltaSeries(values: readonly unknown[] | undefined): number[] {
    if (!Array.isArray(values)) return [];
    return values.map((value) => {
        const resolved = resolveNumeric(value);
        return resolved !== null ? resolved : Number.NaN;
    });
}

export function normalizeDirectionalSeries(values: readonly unknown[] | undefined): number[] {
    if (!Array.isArray(values)) return [];
    return values.map((value) => {
        const resolved = resolveNumeric(value);
        return resolved !== null ? resolved : Number.NaN;
    });
}

export function computeConvergenceDomainSize(data: SpsaConvergenceResponse): number {
    const deltaValuesCount = Array.isArray(data.delta_norm_history) ? data.delta_norm_history.length : 0;
    const analyzedUpdates = resolveNumeric(data.num_updates_analyzed);
    const domainCandidates = [
        deltaValuesCount,
        analyzedUpdates ?? undefined,
        resolveNumeric(data.available_delta_norms) ?? undefined,
    ].filter((value): value is number => typeof value === 'number' && Number.isFinite(value) && value >= 0);
    const completedCount = Math.round(Math.max(0, ...domainCandidates));
    return Math.max(1, completedCount + 1);
}

export function buildDeltaMetaLabel(
    deltaSeries: readonly number[],
    directionalSeries: readonly number[],
    windowSize: number | null,
): string {
    const deltaSamples = deltaSeries.filter((value) => Number.isFinite(value)) as number[];
    const directionalSamples = directionalSeries.filter((value) => Number.isFinite(value)) as number[];
    const metaSegments: string[] = [];
    if (deltaSamples.length) {
        metaSegments.push(`‖Δ‖ μ ${average(deltaSamples).toFixed(4)} · σ ${stdDev(deltaSamples).toFixed(4)}`);
    }
    if (directionalSamples.length) {
        const latestDirectional = directionalSamples[directionalSamples.length - 1];
        const windowLabel =
            typeof windowSize === 'number' && Number.isFinite(windowSize) ? ` (win=${Math.round(windowSize)})` : '';
        metaSegments.push(`‖meanΔ‖${windowLabel} ${latestDirectional.toFixed(4)}`);
    }
    const sampleCount = Math.max(deltaSamples.length, directionalSamples.length);
    return metaSegments.length ? `Last ${sampleCount} updates · ${metaSegments.join(' · ')}` : '';
}

export function normalizeLtcResults(results: readonly SpsaLtcResultEntry[]): ProcessedLtcRun[] {
    const normalizeStatus = (raw: unknown): string => {
        if (typeof raw === 'string') {
            const normalized = raw.toLowerCase();
            if (normalized === 'passed' || normalized === 'failed' || normalized === 'pending') {
                return normalized;
            }
            return normalized || 'unknown';
        }
        return 'unknown';
    };

    return [...results]
        .map((entry): ProcessedLtcRun | null => {
            const updateIdx = resolveNumeric(entry.update_idx);
            const ordinalRaw = resolveNumeric(entry.ordinal);
            const ordinal = ordinalRaw !== null ? ordinalRaw : updateIdx;
            const bestMean = resolveNumeric(entry.best_elo_mean);
            if (ordinal === null || bestMean === null || !Number.isFinite(bestMean)) {
                return null;
            }
            const status = normalizeStatus(entry.status);
            const bestLower = resolveNumeric(entry.best_elo_lower_1sigma);
            const bestUpper = resolveNumeric(entry.best_elo_upper_1sigma);
            const bestSigma = resolveNumeric(entry.best_elo_sigma);
            const bestSource = typeof entry.best_estimate_source === 'string' ? entry.best_estimate_source : null;
            const bestDepth = resolveNumeric(entry.best_composition_depth);
            const bestPrior = resolveNumeric(entry.best_elo_mean_prior);
            const probPositive = resolveNumeric(entry.best_positive_probability);
            const deltaMean = resolveNumeric(entry.delta_elo_mean);
            const deltaVariance = resolveNumeric(entry.delta_elo_variance);
            const effectiveGames = resolveNumeric(entry.delta_effective_games ?? entry.delta_games);
            const totalGames = resolveNumeric(entry.total_games);
            const sprtElo = resolveNumeric(entry.sprt_elo);
            const sprtDecision =
                typeof entry.sprt_result === 'string'
                    ? entry.sprt_result
                    : typeof entry.sprt_decision === 'string'
                      ? entry.sprt_decision
                      : null;
            const sprtGames = resolveNumeric(entry.sprt_games);
            const sprtPoint = resolveNumeric(entry.sprt_point_value);
            return {
                updateIdx,
                ordinal,
                status,
                bestMean,
                bestLower,
                bestUpper,
                bestSigma,
                bestSource,
                bestDepth,
                bestPrior,
                probPositive,
                deltaMean,
                deltaVariance,
                effectiveGames,
                totalGames,
                sprtElo,
                sprtDecision,
                sprtGames,
                sprtPoint,
            };
        })
        .filter((value): value is ProcessedLtcRun => value !== null)
        .sort((a, b) => a.ordinal - b.ordinal);
}

export function resolveLtcYAxis(processed: readonly ProcessedLtcRun[]): {
    minValue: number;
    maxValue: number;
    yTickInfo: ReturnType<typeof computeNiceTicks>;
} {
    const valueCandidates: number[] = [];
    const pushCandidate = (value: number | null) => {
        if (typeof value === 'number' && Number.isFinite(value)) {
            valueCandidates.push(value);
        }
    };
    processed.forEach((item) => {
        pushCandidate(item.bestMean);
        pushCandidate(item.bestLower);
        pushCandidate(item.bestUpper);
        pushCandidate(item.sprtPoint);
    });
    valueCandidates.push(0);

    let minValue = Math.min(...valueCandidates);
    let maxValue = Math.max(...valueCandidates);
    if (!Number.isFinite(minValue) || !Number.isFinite(maxValue)) {
        minValue = -50;
        maxValue = 50;
    } else if (maxValue === minValue) {
        maxValue += 10;
        minValue -= 10;
    } else {
        const pad = Math.max(12, (maxValue - minValue) * 0.08);
        maxValue += pad;
        minValue -= pad;
    }

    const DISPLAY_PADDING = 200;
    const clampedLowerBound = Math.min(...valueCandidates) - DISPLAY_PADDING;
    const clampedUpperBound = Math.max(...valueCandidates) + DISPLAY_PADDING;
    minValue = Math.max(minValue, clampedLowerBound);
    maxValue = Math.min(maxValue, clampedUpperBound);
    if (!(maxValue - minValue > 1e-6)) {
        const center = (clampedLowerBound + clampedUpperBound) / 2;
        minValue = center - DISPLAY_PADDING;
        maxValue = center + DISPLAY_PADDING;
    }

    const yTickInfo = computeNiceTicks(minValue, maxValue, 5);
    return { minValue: yTickInfo.min, maxValue: yTickInfo.max, yTickInfo };
}

function average(series: readonly number[]): number {
    if (!series.length) return Number.NaN;
    const sum = series.reduce((acc, value) => acc + value, 0);
    return sum / series.length;
}

function stdDev(series: readonly number[]): number {
    if (series.length < 2) return 0;
    const mean = average(series);
    const variance = series.reduce((acc, value) => acc + (value - mean) ** 2, 0) / (series.length - 1);
    return Math.sqrt(variance);
}
