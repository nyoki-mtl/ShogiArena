import type {
    NormalizedSpsaParameter,
    NormalizedSpsaParams,
    NormalizedSpsaUpdateEntry,
    SpsaCorrelationResponse,
} from '@/modules/spsa/types';
import type { ParameterCorrelationEntry, ParameterInsight, ParameterSelectionRequest } from './parameter-shared';
import { DEFAULT_PARAMETER_SORT_STATE, type ParameterSortState } from './correlation-table';
import { METRIC_ZERO_THRESHOLD } from './thresholds';
import { resolveNumeric } from './shared';

const BOUNDARY_THRESHOLD_RATIO = 0.05;
const EPSILON = 1e-9;
type ParameterTimelinePoint = {
    actual: number;
    baseline: number;
    variantIdx: number;
    isPending: boolean;
    ltcRejected?: boolean;
    ltcInvalidated?: boolean;
    ltcDecision?: 'accepted' | 'rejected' | null;
};

type BaselineSnapshotMap = Map<number, Record<string, number>>;
let parameterInsightCache: Map<string, ParameterInsight> = new Map();
let selectedParameterName: string | null = null;
let pendingParameterSelection: ParameterSelectionRequest | null = null;
let parameterSortState: ParameterSortState = { ...DEFAULT_PARAMETER_SORT_STATE };
let parameterInsightsList: ParameterInsight[] = [];

export function resetCorrelationState(): void {
    parameterInsightCache = new Map();
    selectedParameterName = null;
    pendingParameterSelection = null;
    parameterSortState = { ...DEFAULT_PARAMETER_SORT_STATE };
    parameterInsightsList = [];
}

export function setParameterInsights(insights: ParameterInsight[]): void {
    parameterInsightsList = insights;
    parameterInsightCache = new Map(insights.map((insight) => [insight.name, insight]));
}

export function getParameterInsights(): ParameterInsight[] {
    return parameterInsightsList;
}

export function getParameterInsight(name: string): ParameterInsight | null {
    return parameterInsightCache.get(name) ?? null;
}

export function hasParameterInsight(name: string): boolean {
    return parameterInsightCache.has(name);
}

export function setSelectedParameter(name: string | null): void {
    selectedParameterName = name;
}

export function getSelectedParameter(): string | null {
    return selectedParameterName;
}

export function setPendingParameterSelection(request: ParameterSelectionRequest | null): void {
    pendingParameterSelection = request;
}

export function getParameterSortState(): ParameterSortState {
    return parameterSortState;
}

export function updateParameterSortState(next: ParameterSortState): void {
    parameterSortState = next;
}

export function resolvePreferredSelection(insights: readonly ParameterInsight[]): ParameterSelectionRequest | null {
    if (pendingParameterSelection && parameterInsightCache.has(pendingParameterSelection.name)) {
        const target = pendingParameterSelection;
        pendingParameterSelection = null;
        return target;
    }

    if (selectedParameterName && parameterInsightCache.has(selectedParameterName)) {
        return { name: selectedParameterName, scrollIntoView: false };
    }

    if (insights[0]) {
        return { name: insights[0].name, scrollIntoView: false };
    }

    return null;
}

export function buildParameterInsights(
    data: SpsaCorrelationResponse,
    paramsData: NormalizedSpsaParams | null,
    updatesEntries: readonly NormalizedSpsaUpdateEntry[] | null,
    options?: { initialEntry?: NormalizedSpsaUpdateEntry | null },
): ParameterInsight[] {
    const names = new Set<string>();
    for (const name of data.parameter_names ?? []) {
        if (typeof name === 'string' && name.trim()) {
            names.add(name);
        }
    }
    for (const param of paramsData?.params ?? []) {
        if (param?.name && !param.notUsed) {
            names.add(param.name);
        }
    }

    const paramMap = new Map<string, NormalizedSpsaParameter>();
    for (const param of paramsData?.params ?? []) {
        if (param?.name) {
            paramMap.set(param.name, param);
        }
    }

    const initial = paramsData?.initialParams ?? {};
    const diffs = paramsData?.diffs ?? {};
    const parameterNames = data.parameter_names ?? [];
    const correlationMap = buildCorrelationMap(data.correlations ?? {}, parameterNames);
    const gradientResolution = resolveGradientResolution(updatesEntries ?? []);
    const gradients = gradientResolution.gradients;
    const deltas = gradientResolution.latestCompleted?.deltas ?? {};
    const initialParams = paramsData?.initialParams ?? {};
    const initialEntry = options?.initialEntry ?? null;

    const serverTimeline = data.parameter_timeline;
    const usingServerTimeline =
        !!serverTimeline && typeof serverTimeline === 'object' && !Array.isArray(serverTimeline);
    const timelineMap = usingServerTimeline
        ? buildParameterTimelineMapFromServer(
              serverTimeline as Record<
                  string,
                  Array<{
                      update_idx: number;
                      actual: number;
                      baseline: number;
                      pending: boolean;
                      ltc_invalidated?: boolean;
                      ltc_decision?: 'accepted' | 'rejected' | null;
                  }>
              >,
          )
        : buildParameterTimelineMap({
              updatesEntries: updatesEntries ?? [],
              initialEntry,
              initialParams,
          });
    const gradientEvolutionMap = data.gradient_evolution ?? {};

    const insights: ParameterInsight[] = [];
    names.forEach((name) => {
        const param = paramMap.get(name) ?? null;
        const rawEvolution = normalizeEvolutionSeries(data.parameter_evolution?.[name]);
        const baselineValue = resolveNumeric(initial[name]);
        const diffValue = resolveNumeric(diffs[name]);
        const timelinePoints = timelineMap.get(name);

        let series: ParameterTimelineSeries | null = null;
        if (timelinePoints?.length) {
            try {
                series = resolveSeriesFromTimeline(name, timelinePoints, {
                    source: usingServerTimeline ? 'server' : 'local',
                    initialParams,
                });
            } catch (error) {
                console.warn(`[SPSA] Failed to resolve timeline for "${name}": ${String(error)}`);
            }
        }

        const currentValueFromParams = resolveNumeric(param?.value) ?? rawEvolution.at(-1) ?? null;
        if (!series) {
            series = buildFallbackSeriesFromEvolution({
                parameterName: name,
                evolutionSeries: rawEvolution,
                baselineValue,
                currentValue: currentValueFromParams,
            });
        }

        if (!series) {
            console.warn(`[SPSA] Parameter "${name}" has no timeline data, skipping`);
            return;
        }

        const evolution = series.actual;
        const baselineEvolution = series.baseline;
        const variantIndexes = series.variantIndexes;
        const displayIndexes = series.displayIndexes;
        const pendingFlags = series.pending;
        const ltcRejectedFlags = series.ltcRejected;
        const ltcDecisionRejectedFlags = series.ltcDecisionRejectedFlags ?? new Array(evolution.length).fill(false);
        const currentValue = currentValueFromParams ?? evolution.at(-1) ?? null;
        const initialValue =
            baselineValue ??
            (currentValue !== null && diffValue !== null
                ? currentValue - diffValue
                : (evolution[0] ?? rawEvolution[0] ?? null));
        const delta = currentValue !== null && initialValue !== null ? currentValue - initialValue : diffValue;
        const gradientSeries = normalizeEvolutionSeries(gradientEvolutionMap[name]);
        const gradient =
            resolveNumeric(gradients[name]) ??
            resolveNumeric(gradientSeries.length ? gradientSeries[gradientSeries.length - 1] : null);
        const correlated = resolveCorrelatedList(correlationMap.get(name) ?? []);
        const minEvolution = evolution.length ? Math.min(...evolution) : null;
        const maxEvolution = evolution.length ? Math.max(...evolution) : null;
        const status = resolveParameterStatus(param);
        const latestDelta = resolveNumeric(deltas[name]);

        const minBound = typeof param?.min === 'number' && Number.isFinite(param?.min) ? (param?.min as number) : null;
        const maxBound = typeof param?.max === 'number' && Number.isFinite(param?.max) ? (param?.max as number) : null;
        const rangeScale = resolveRangeScale({
            minBound,
            maxBound,
            initialValue,
            currentValue,
            delta,
            minEvolution,
            maxEvolution,
        });
        const gradientScale = resolveGradientScale({
            minBound,
            maxBound,
            initialValue,
            currentValue,
        });
        const movementRange =
            minEvolution !== null && maxEvolution !== null
                ? Math.abs(maxEvolution - minEvolution)
                : Math.abs(delta ?? 0);
        const deltaRatio = normalizeRatio(delta, rangeScale);
        const latestDeltaRatio = normalizeRatio(latestDelta, rangeScale);
        const driftRatio = normalizeRatio(movementRange, rangeScale);
        const gradientEffect = gradient !== null && gradientScale !== null ? gradient * (0.01 * gradientScale) : null;

        insights.push({
            name,
            initialValue,
            currentValue,
            baselineValue,
            delta,
            gradient,
            latestDelta,
            deltaRatio,
            gradientEffect,
            latestDeltaRatio,
            driftRatio,
            rangeScale,
            variantsTracked: evolution.length,
            latestVariant: variantIndexes.length ? variantIndexes[variantIndexes.length - 1] : null,
            latestVariantValue: evolution.at(-1) ?? currentValue,
            evolution,
            baselineEvolution,
            variantIndexes,
            displayIndexes,
            pendingFlags,
            ltcRejectedFlags,
            minEvolution,
            maxEvolution,
            correlated,
            status,
            ltcDecisionRejectedFlags,
        });
    });

    return insights;
}

type ParameterTimelineSeries = {
    actual: number[];
    baseline: number[];
    variantIndexes: number[];
    displayIndexes: number[];
    pending: boolean[];
    ltcRejected: boolean[];
    ltcDecisionRejectedFlags?: boolean[];
};

function resolveGradientResolution(entries: readonly NormalizedSpsaUpdateEntry[]): {
    gradients: Record<string, number>;
    latestCompleted: NormalizedSpsaUpdateEntry | null;
    source: 'aggregate' | 'latest' | 'pending';
    aggregateSamples: number;
} {
    const aggregate = buildAggregatedGradients(entries);
    const latestCompleted = entries.find((entry) => entry && !entry.isPending) ?? null;
    if (aggregate.samples > 0) {
        return {
            gradients: aggregate.values,
            latestCompleted,
            source: 'aggregate',
            aggregateSamples: aggregate.samples,
        } as const;
    }
    if (latestCompleted) {
        return {
            gradients: latestCompleted.gradients ?? {},
            latestCompleted,
            source: 'latest',
            aggregateSamples: 1,
        } as const;
    }
    const fallback = entries[0] ?? null;
    return {
        gradients: fallback?.gradients ?? {},
        latestCompleted: fallback && !fallback.isPending ? fallback : null,
        source: 'pending',
        aggregateSamples: 0,
    } as const;
}

function buildAggregatedGradients(entries: readonly NormalizedSpsaUpdateEntry[]): {
    values: Record<string, number>;
    samples: number;
} {
    const sums = new Map<string, { sum: number; count: number }>();
    const contributors = new Set<number>();
    for (const entry of entries) {
        if (!entry || entry.isPending) continue;
        const gradients = entry.gradients ?? {};
        let contributed = false;
        for (const [name, value] of Object.entries(gradients)) {
            if (typeof value !== 'number' || !Number.isFinite(value)) continue;
            const bucket = sums.get(name) ?? { sum: 0, count: 0 };
            bucket.sum += value;
            bucket.count += 1;
            sums.set(name, bucket);
            contributed = true;
        }
        if (contributed && Number.isFinite(entry.updateIdx)) {
            contributors.add(entry.updateIdx as number);
        }
    }
    const values: Record<string, number> = {};
    sums.forEach((bucket, name) => {
        if (bucket.count > 0) {
            values[name] = bucket.sum / bucket.count;
        }
    });
    return { values, samples: contributors.size };
}

function buildParameterTimelineMapFromServer(
    serverTimeline: Record<
        string,
        Array<{
            update_idx: number;
            actual: number;
            baseline: number;
            pending: boolean;
            ltc_invalidated?: boolean;
            ltc_decision?: 'accepted' | 'rejected' | null;
        }>
    >,
): Map<string, ParameterTimelinePoint[]> {
    const timeline = new Map<string, ParameterTimelinePoint[]>();
    for (const [name, points] of Object.entries(serverTimeline)) {
        if (!Array.isArray(points)) continue;
        const converted: ParameterTimelinePoint[] = [];
        for (const point of points) {
            if (
                typeof point.update_idx === 'number' &&
                typeof point.actual === 'number' &&
                typeof point.baseline === 'number' &&
                Number.isFinite(point.update_idx) &&
                Number.isFinite(point.actual) &&
                Number.isFinite(point.baseline)
            ) {
                converted.push({
                    actual: point.actual,
                    baseline: point.baseline,
                    variantIdx: point.update_idx >= 0 ? point.update_idx : 0,
                    isPending: Boolean(point.pending),
                    ltcRejected: Boolean(point.ltc_invalidated),
                    ltcInvalidated: Boolean(point.ltc_invalidated),
                    ltcDecision:
                        point.ltc_decision === 'accepted' || point.ltc_decision === 'rejected'
                            ? point.ltc_decision
                            : null,
                });
            }
        }
        if (converted.length > 0) {
            timeline.set(name, converted);
        }
    }
    return timeline;
}

function buildParameterTimelineMap(input: {
    updatesEntries: readonly NormalizedSpsaUpdateEntry[];
    initialEntry: NormalizedSpsaUpdateEntry | null;
    initialParams: Record<string, number>;
}): Map<string, ParameterTimelinePoint[]> {
    const entries: NormalizedSpsaUpdateEntry[] = [];
    if (Array.isArray(input.updatesEntries) && input.updatesEntries.length) {
        entries.push(...input.updatesEntries);
    }
    if (input.initialEntry) {
        entries.push(input.initialEntry);
    }
    if (!entries.length) {
        return new Map();
    }

    const baselineSnapshots = buildVariantBaselineSnapshots({
        updatesEntries: entries,
        initialEntry: input.initialEntry,
        initialParams: input.initialParams,
    });

    const deduped = new Map<number, NormalizedSpsaUpdateEntry>();
    for (const entry of entries) {
        const idx = typeof entry.updateIdx === 'number' && Number.isFinite(entry.updateIdx) ? entry.updateIdx : null;
        if (idx === null) continue;
        deduped.set(idx, entry);
    }

    const ordered = [...deduped.entries()].sort((a, b) => a[0] - b[0]).map(([, entry]) => entry);
    const timeline = new Map<string, ParameterTimelinePoint[]>();
    for (const entry of ordered) {
        const params = entry.params ?? {};
        const paramDeltas = entry.deltas ?? {};
        const rawVariantIdx =
            typeof entry.updateIdx === 'number' && Number.isFinite(entry.updateIdx) ? entry.updateIdx : 0;
        const variantIdx = rawVariantIdx >= 0 ? rawVariantIdx : 0;
        const isPending = Boolean(entry.isPending);
        const ltcRejected = shouldRenderActualAsBaseline(entry);
        const ltcDecision =
            entry.ltcRegression?.accepted === true
                ? 'accepted'
                : entry.ltcRegression?.accepted === false
                  ? 'rejected'
                  : null;
        Object.entries(params).forEach(([name, rawValue]) => {
            if (typeof rawValue !== 'number' || !Number.isFinite(rawValue)) return;
            const rawDelta = paramDeltas[name];
            const numericDelta = typeof rawDelta === 'number' && Number.isFinite(rawDelta) ? rawDelta : 0;
            const baselineValue = resolveTimelineBaselineValue(
                baselineSnapshots,
                variantIdx,
                name,
                rawValue,
                numericDelta,
            );
            const resolvedBaselineValue = resolveBaselineValueForTimeline({
                rawValue,
                numericDelta,
                baselineValue,
            });
            const actualValue = resolveActualValueForTimeline({
                rawValue,
                resolvedBaselineValue,
                entry,
            });
            const points = timeline.get(name) ?? [];
            points.push({
                actual: actualValue,
                baseline: resolvedBaselineValue,
                variantIdx,
                isPending,
                ltcRejected,
                ltcInvalidated: ltcRejected,
                ltcDecision,
            });
            timeline.set(name, points);
        });
    }

    const baselineZeroSnapshot = baselineSnapshots.get(0) ?? baselineSnapshots.get(-1) ?? null;
    const initialParams = input.initialParams ?? {};

    const candidateNames = new Set<string>();
    timeline.forEach((_points, name) => {
        candidateNames.add(name);
    });
    if (baselineZeroSnapshot) {
        Object.keys(baselineZeroSnapshot).forEach((name) => {
            candidateNames.add(name);
        });
    }
    Object.keys(initialParams).forEach((name) => {
        candidateNames.add(name);
    });

    const resolveBaselineValue = (name: string): number | null => {
        const fromSnapshot = baselineZeroSnapshot?.[name];
        if (typeof fromSnapshot === 'number' && Number.isFinite(fromSnapshot)) {
            return fromSnapshot;
        }
        const fromInitial = initialParams[name];
        if (typeof fromInitial === 'number' && Number.isFinite(fromInitial)) {
            return fromInitial;
        }
        return null;
    };

    candidateNames.forEach((name) => {
        const points = timeline.get(name) ?? [];
        const hasBaseline = points.some((point) => point.variantIdx === 0);
        if (hasBaseline) {
            if (points.length) {
                points.sort((a, b) => a.variantIdx - b.variantIdx);
                timeline.set(name, points);
            }
            return;
        }

        const baselineValue =
            resolveBaselineValue(name) ??
            (points.length
                ? Number.isFinite(points[0]?.baseline)
                    ? (points[0].baseline as number)
                    : (points[0].actual as number)
                : null);

        if (baselineValue === null || !Number.isFinite(baselineValue)) {
            return;
        }

        points.push({
            actual: baselineValue,
            baseline: baselineValue,
            variantIdx: 0,
            isPending: false,
        });
        points.sort((a, b) => a.variantIdx - b.variantIdx);
        timeline.set(name, points);
    });

    return timeline;
}

function resolveSeriesFromTimeline(
    parameterName: string,
    timelinePoints: ParameterTimelinePoint[] | undefined,
    options?: { source?: 'server' | 'local'; initialParams?: Record<string, number> },
): ParameterTimelineSeries {
    const source = options?.source ?? 'local';
    if (!timelinePoints?.length) {
        throw new Error(`[SPSA] Missing variant timeline for parameter "${parameterName}"`);
    }

    const validPoints = timelinePoints.filter(
        (point) => Number.isFinite(point.actual) && Number.isFinite(point.baseline),
    );
    if (!validPoints.length) {
        throw new Error(`[SPSA] Invalid variant timeline for parameter "${parameterName}"`);
    }

    const sortedPoints = [...validPoints].sort((a, b) => {
        const left = Number(a.variantIdx);
        const right = Number(b.variantIdx);
        if (!Number.isFinite(left) || !Number.isFinite(right)) {
            return 0;
        }
        return left - right;
    });

    const baselinePoint = sortedPoints.find((point) => Number(point.variantIdx) === 0);
    if (!baselinePoint) {
        throw new Error(`[SPSA] Variant timeline (${source}) for "${parameterName}" does not include baseline (#0)`);
    }

    if (Number(sortedPoints[0]?.variantIdx) !== 0) {
        throw new Error(`[SPSA] Variant timeline (${source}) for "${parameterName}" must start at #0`);
    }

    const processedPoints = sortedPoints.map((point) => {
        const numeric = Number(point.variantIdx);
        if (!Number.isFinite(numeric)) {
            throw new Error(`[SPSA] Variant timeline (${source}) contains non-numeric index for "${parameterName}"`);
        }
        const decision =
            point.ltcDecision === 'accepted' || point.ltcDecision === 'rejected' ? point.ltcDecision : null;
        const invalidatedFlag = point.ltcInvalidated ?? point.ltcRejected ?? false;
        return {
            actual: point.actual,
            baseline: point.baseline,
            variantIdx: Math.trunc(numeric),
            isPending: Boolean(point.isPending),
            ltcRejected: Boolean(point.ltcRejected),
            ltcInvalidated: Boolean(invalidatedFlag),
            ltcDecision: decision,
        };
    });

    if (processedPoints.length === 0) {
        throw new Error(`[SPSA] Variant timeline for "${parameterName}" is empty`);
    }
    if (processedPoints[0].variantIdx !== 0) {
        throw new Error(`[SPSA] Variant timeline for "${parameterName}" must start at #0`);
    }

    // Always treat baseline (#0) as an unchanged reference point:
    // updated value == baseline value, not pending, not LTC-rejected.
    processedPoints[0] = {
        ...processedPoints[0],
        actual: processedPoints[0].baseline,
        isPending: false,
        ltcRejected: false,
        ltcInvalidated: false,
        ltcDecision: null,
    };

    // Additionally, if the dashboard exposes explicit initial parameters,
    // ensure that the synthetic baseline (#0) always reflects those values.
    // This prevents the first updated value (e.g. #1) from "leaking" into
    // the baseline point on the Parameter Detail chart.
    const initialParams = options?.initialParams ?? null;
    const explicitInitial =
        initialParams && Object.hasOwn(initialParams, parameterName) ? initialParams[parameterName] : undefined;
    if (typeof explicitInitial === 'number' && Number.isFinite(explicitInitial)) {
        processedPoints[0] = {
            ...processedPoints[0],
            actual: explicitInitial,
            baseline: explicitInitial,
        };
    }

    const actual = processedPoints.map((point) => point.actual);
    const baseline = processedPoints.map((point) => point.baseline);
    const variantIndexes = processedPoints.map((point) => point.variantIdx);
    const pending = processedPoints.map((point) => point.isPending);
    const ltcRejected = processedPoints.map((point) => Boolean(point.ltcInvalidated ?? point.ltcRejected));
    const ltcDecisionRejectedFlags = processedPoints.map((point) => point.ltcDecision === 'rejected');

    const displayIndexes: number[] = [];
    let displayCounter = 0;
    for (let i = 0; i < variantIndexes.length; i += 1) {
        const variantIdx = variantIndexes[i];
        if (i === 0 && variantIdx <= 0) {
            displayIndexes.push(0);
        } else {
            displayCounter += 1;
            displayIndexes.push(displayCounter);
        }
    }

    for (let idx = 1; idx < variantIndexes.length; idx += 1) {
        if (variantIndexes[idx] < variantIndexes[idx - 1]) {
            throw new Error(`[SPSA] Variant timeline for "${parameterName}" must be non-decreasing`);
        }
    }

    return {
        actual,
        baseline,
        variantIndexes,
        displayIndexes,
        pending,
        ltcRejected,
        ltcDecisionRejectedFlags,
    };
}

function buildFallbackSeriesFromEvolution(options: {
    parameterName: string;
    evolutionSeries: number[];
    baselineValue: number | null;
    currentValue: number | null;
}): ParameterTimelineSeries | null {
    const { parameterName, evolutionSeries, baselineValue, currentValue } = options;
    const normalizedBaseline =
        typeof baselineValue === 'number' && Number.isFinite(baselineValue) ? (baselineValue as number) : null;
    if (normalizedBaseline === null) {
        console.warn(
            `[SPSA] Fallback timeline for "${parameterName}" requires an explicit baseline; skipping parameter.`,
        );
        return null;
    }
    const resolvedCurrent = typeof currentValue === 'number' && Number.isFinite(currentValue) ? currentValue : null;
    const inferredBaseline = normalizedBaseline;

    const normalizedEvolution = (() => {
        if (!evolutionSeries.length) {
            return [] as number[];
        }
        const first = evolutionSeries[0];
        if (Math.abs(first - inferredBaseline) <= EPSILON) {
            return evolutionSeries.slice(1);
        }
        return evolutionSeries;
    })();

    const actualSeries = (() => {
        const points = [inferredBaseline, ...normalizedEvolution];
        if (points.length === 1 && resolvedCurrent !== null) {
            points.push(resolvedCurrent);
        }
        return points;
    })();

    const baselineSeries = buildStaticBaselineSeries(actualSeries);
    const variantIndexes = actualSeries.map((_value, index) => (index === 0 ? 0 : index));
    const displayIndexes = [...variantIndexes];
    const pending = actualSeries.map(() => false);
    const ltcRejected = actualSeries.map(() => false);

    return {
        actual: actualSeries,
        baseline: baselineSeries,
        variantIndexes,
        displayIndexes,
        pending,
        ltcRejected,
    };
}

export function buildStaticBaselineSeries(values: number[]): number[] {
    if (!values.length) {
        return [];
    }
    const startValue = values[0];
    return values.map(() => startValue);
}

function buildVariantBaselineSnapshots(input: {
    updatesEntries: readonly NormalizedSpsaUpdateEntry[];
    initialEntry: NormalizedSpsaUpdateEntry | null;
    initialParams: Record<string, number>;
}): BaselineSnapshotMap {
    const snapshots: BaselineSnapshotMap = new Map();
    const entries = Array.isArray(input.updatesEntries) ? [...input.updatesEntries] : [];
    const baselineSeed: Record<string, number> = {};

    const initialEntry = input.initialEntry;
    if (initialEntry?.params) {
        Object.entries(initialEntry.params).forEach(([name, value]) => {
            if (typeof value === 'number' && Number.isFinite(value)) {
                baselineSeed[name] = value;
            }
        });
    } else {
        Object.entries(input.initialParams ?? {}).forEach(([name, value]) => {
            if (typeof value === 'number' && Number.isFinite(value)) {
                baselineSeed[name] = value;
            }
        });
    }

    const currentBaseline = new Map<string, number>();
    Object.entries(baselineSeed).forEach(([name, value]) => {
        currentBaseline.set(name, value);
    });

    if (initialEntry?.updateIdx !== undefined) {
        snapshots.set(initialEntry.updateIdx, { ...baselineSeed });
    } else if (Object.keys(baselineSeed).length) {
        snapshots.set(-1, { ...baselineSeed });
    }

    if (!entries.length) {
        return snapshots;
    }

    type TimelineEvent = {
        time: number;
        type: 'start' | 'end';
        entry: NormalizedSpsaUpdateEntry;
        updateIdx: number;
    };

    const events: TimelineEvent[] = [];
    entries.forEach((entry) => {
        const idx = entry.updateIdx;
        if (!Number.isFinite(idx)) return;
        hydrateBaselineFromEntry(entry, currentBaseline);
        const startTime = resolveUpdateStartTime(entry);
        const endTime = resolveUpdateEndTime(entry, startTime);
        events.push({ time: startTime, type: 'start', entry, updateIdx: idx });
        events.push({ time: endTime, type: 'end', entry, updateIdx: idx });
    });

    events.sort((a, b) => {
        if (a.time !== b.time) return a.time - b.time;
        if (a.type !== b.type) return a.type === 'start' ? -1 : 1;
        return a.updateIdx - b.updateIdx;
    });

    const startSnapshots = new Map<number, Record<string, number>>();

    const cloneBaseline = (): Record<string, number> => {
        const out: Record<string, number> = {};
        currentBaseline.forEach((value, key) => {
            if (typeof value === 'number' && Number.isFinite(value)) {
                out[key] = value;
            }
        });
        return out;
    };

    events.forEach((event) => {
        const { entry, updateIdx } = event;
        if (!Number.isFinite(updateIdx)) {
            return;
        }
        if (event.type === 'start') {
            const snapshot = cloneBaseline();
            snapshots.set(updateIdx, snapshot);
            startSnapshots.set(updateIdx, snapshot);
        } else {
            const snapshot = startSnapshots.get(updateIdx);
            if (!shouldApplyBaselineTransition(entry)) {
                startSnapshots.delete(updateIdx);
                return;
            }
            const deltas = extractDeltaMap(entry, snapshot);
            if (Object.keys(deltas).length === 0) {
                startSnapshots.delete(updateIdx);
                return;
            }
            applyDeltaToBaseline(currentBaseline, deltas, snapshot);
            startSnapshots.delete(updateIdx);
        }
    });

    return snapshots;
}

function hydrateBaselineFromEntry(entry: NormalizedSpsaUpdateEntry, baseline: Map<string, number>): void {
    const params = entry.params ?? {};
    const deltas = entry.deltas ?? {};
    Object.entries(params).forEach(([name, value]) => {
        if (typeof value !== 'number' || !Number.isFinite(value) || baseline.has(name)) {
            return;
        }
        const delta = deltas[name];
        if (typeof delta === 'number' && Number.isFinite(delta)) {
            baseline.set(name, value - delta);
        } else {
            baseline.set(name, value);
        }
    });
}

function resolveUpdateStartTime(entry: NormalizedSpsaUpdateEntry): number {
    const candidates = [entry.startedAt, entry.timestamp, entry.endedAt];
    for (const value of candidates) {
        if (typeof value === 'number' && Number.isFinite(value)) {
            return value;
        }
    }
    return entry.updateIdx ?? 0;
}

function resolveUpdateEndTime(entry: NormalizedSpsaUpdateEntry, fallbackStart: number): number {
    const candidates = [entry.endedAt, entry.timestamp];
    for (const value of candidates) {
        if (typeof value === 'number' && Number.isFinite(value)) {
            return value;
        }
    }
    return fallbackStart + 1;
}

function shouldRenderActualAsBaseline(entry: NormalizedSpsaUpdateEntry): boolean {
    return Boolean(entry?.ltcRegression?.accepted === false);
}

function resolveActualValueForTimeline(options: {
    rawValue: number;
    resolvedBaselineValue: number;
    entry: NormalizedSpsaUpdateEntry;
}): number {
    const { rawValue, resolvedBaselineValue, entry } = options;
    if (shouldRenderActualAsBaseline(entry)) {
        return resolvedBaselineValue;
    }
    return rawValue;
}

function resolveBaselineValueForTimeline(options: {
    rawValue: number;
    numericDelta: number;
    baselineValue: number | null;
}): number {
    const { rawValue, numericDelta, baselineValue } = options;
    if (typeof baselineValue === 'number' && Number.isFinite(baselineValue)) {
        return baselineValue;
    }
    const resolved = rawValue - numericDelta;
    return Number.isFinite(resolved) ? resolved : rawValue;
}

function shouldApplyBaselineTransition(entry: NormalizedSpsaUpdateEntry): boolean {
    if (!entry) {
        return false;
    }
    if (entry.isPending) {
        return false;
    }
    // Only move the baseline forward when an LTC regression has been explicitly
    // accepted. Plain SPSA updates (without LTC) and rejected LTC runs should
    // not change the committed baseline.
    return entry.ltcRegression?.accepted === true;
}

function extractDeltaMap(
    entry: NormalizedSpsaUpdateEntry,
    baselineRef: Record<string, number> | undefined,
): Record<string, number> {
    const deltas = entry.deltas ?? {};
    const normalized: Record<string, number> = {};
    Object.entries(deltas).forEach(([name, value]) => {
        if (typeof value === 'number' && Number.isFinite(value)) {
            normalized[name] = value;
        }
    });
    if (Object.keys(normalized).length) {
        return normalized;
    }
    const params = entry.params ?? {};
    Object.entries(params).forEach(([name, value]) => {
        if (typeof value !== 'number' || !Number.isFinite(value)) {
            return;
        }
        const baseValue = baselineRef?.[name];
        if (typeof baseValue === 'number' && Number.isFinite(baseValue)) {
            normalized[name] = value - baseValue;
        }
    });
    return normalized;
}

function applyDeltaToBaseline(
    baseline: Map<string, number>,
    deltaSource: Record<string, number>,
    fallback: Record<string, number> | undefined,
): void {
    Object.entries(deltaSource).forEach(([name, delta]) => {
        if (typeof delta !== 'number' || !Number.isFinite(delta)) {
            return;
        }
        const prev = baseline.get(name);
        if (typeof prev === 'number' && Number.isFinite(prev)) {
            baseline.set(name, prev + delta);
            return;
        }
        const reference = fallback?.[name];
        if (typeof reference === 'number' && Number.isFinite(reference)) {
            baseline.set(name, reference + delta);
        } else {
            baseline.set(name, delta);
        }
    });
}

function resolveTimelineBaselineValue(
    snapshots: BaselineSnapshotMap,
    updateIdx: number,
    name: string,
    rawValue: number,
    numericDelta: number,
): number | null {
    if (Number.isFinite(updateIdx)) {
        const snapshot = snapshots.get(updateIdx);
        const snapshotValue = snapshot?.[name];
        if (typeof snapshotValue === 'number' && Number.isFinite(snapshotValue)) {
            return snapshotValue;
        }
    }
    const fallback = rawValue - numericDelta;
    return Number.isFinite(fallback) ? fallback : null;
}

function buildCorrelationMap(
    source: Record<string, number>,
    parameterNames: readonly string[],
): Map<string, ParameterCorrelationEntry[]> {
    const map = new Map<string, ParameterCorrelationEntry[]>();

    Object.entries(source).forEach(([key, value]) => {
        if (typeof value !== 'number' || !Number.isFinite(value)) return;

        let left: string | null = null;
        let right: string | null = null;

        for (const param of parameterNames) {
            if (key.startsWith(`${param}_`)) {
                const remainder = key.substring(param.length + 1);
                if (parameterNames.includes(remainder)) {
                    left = param;
                    right = remainder;
                    break;
                }
            }
        }

        if (!left || !right) {
            return;
        }

        const pair = { name: right, value };
        const reversePair = { name: left, value };
        if (!map.has(left)) map.set(left, []);
        if (!map.has(right)) map.set(right, []);
        map.get(left)?.push(pair);
        map.get(right)?.push(reversePair);
    });
    return map;
}

function resolveCorrelatedList(entries: ParameterCorrelationEntry[]): ParameterCorrelationEntry[] {
    // Sort by |correlation| descending to show strongest correlations first
    return entries.sort((a, b) => Math.abs(b.value) - Math.abs(a.value));
}

function resolveParameterStatus(param: NormalizedSpsaParameter | null): ParameterInsight['status'] {
    if (!param) return null;
    if (param.notUsed) return null;
    if (!Number.isFinite(param.value) || !Number.isFinite(param.min) || !Number.isFinite(param.max)) {
        return null;
    }
    const value = param.value as number;
    const min = param.min as number;
    const max = param.max as number;
    if (Math.abs(value - min) <= EPSILON || Math.abs(value - max) <= EPSILON) {
        return 'clamped';
    }
    const range = max - min;
    if (!Number.isFinite(range) || range <= 0) return null;
    const threshold = range * BOUNDARY_THRESHOLD_RATIO;
    if (value <= min + threshold || value >= max - threshold) {
        return 'boundary';
    }
    return null;
}

function normalizeEvolutionSeries(values: unknown): number[] {
    if (!Array.isArray(values)) return [];
    return values
        .map((value) => (typeof value === 'number' && Number.isFinite(value) ? (value as number) : null))
        .filter((value): value is number => value !== null);
}

function resolveRangeScale(options: {
    minBound: number | null;
    maxBound: number | null;
    initialValue: number | null;
    currentValue: number | null;
    delta: number | null;
    minEvolution: number | null;
    maxEvolution: number | null;
}): number | null {
    const { minBound, maxBound, initialValue, currentValue, delta, minEvolution, maxEvolution } = options;
    const candidates: number[] = [];

    if (minBound !== null && maxBound !== null) {
        const range = Math.abs(maxBound - minBound);
        if (range > METRIC_ZERO_THRESHOLD) candidates.push(range);
    }
    if (minEvolution !== null && maxEvolution !== null) {
        const evolutionRange = Math.abs(maxEvolution - minEvolution);
        if (evolutionRange > METRIC_ZERO_THRESHOLD) candidates.push(evolutionRange);
    }

    const magnitudeCandidates = [initialValue, currentValue, delta]
        .map((value) => (typeof value === 'number' && Number.isFinite(value) ? Math.abs(value) : null))
        .filter((value): value is number => value !== null && value > METRIC_ZERO_THRESHOLD);
    candidates.push(...magnitudeCandidates);

    if (!candidates.length) {
        return 1;
    }
    const scale = Math.max(...candidates);
    return scale > METRIC_ZERO_THRESHOLD ? scale : 1;
}

function resolveGradientScale(options: {
    minBound: number | null;
    maxBound: number | null;
    initialValue: number | null;
    currentValue: number | null;
}): number | null {
    const { minBound, maxBound, initialValue, currentValue } = options;
    const candidates: number[] = [];
    if (initialValue !== null && Number.isFinite(initialValue)) {
        const magnitude = Math.abs(initialValue);
        if (magnitude > METRIC_ZERO_THRESHOLD) candidates.push(magnitude);
    }
    if (currentValue !== null && Number.isFinite(currentValue)) {
        const magnitude = Math.abs(currentValue);
        if (magnitude > METRIC_ZERO_THRESHOLD) candidates.push(magnitude);
    }
    if (minBound !== null && maxBound !== null) {
        const range = Math.abs(maxBound - minBound);
        if (range > METRIC_ZERO_THRESHOLD) {
            candidates.push(range / 2);
        }
    }
    if (!candidates.length) {
        return 1;
    }
    const scale = Math.max(...candidates);
    return scale > METRIC_ZERO_THRESHOLD ? scale : 1;
}

export function normalizeRatio(value: number | null, scale: number | null): number | null {
    if (value === null || !Number.isFinite(value)) return null;
    if (scale === null || !Number.isFinite(scale) || scale <= METRIC_ZERO_THRESHOLD) return null;
    return value / scale;
}

export function getEpsilon(): number {
    return EPSILON;
}
