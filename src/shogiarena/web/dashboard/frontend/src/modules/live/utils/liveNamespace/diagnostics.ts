import { getNamespaceMeta, getTimestamp, ensureLiveNamespace, LIVE_DIAGNOSTICS_WINDOW_MS } from './core';
import type {
    LiveNamespaceDiagnostics,
    LiveNamespaceMeta,
    LiveNamespaceKey,
    LiveNamespaceMetric,
    LiveNamespaceMetricExtraWindow,
    LiveNamespaceMetricWindow,
    LiveNamespaceOwner,
    LiveNamespaceTimelineEntry,
} from './core';

const DIAGNOSTICS_EVENT = 'dashboardlive:diagnostics';

function summarizeMetricWindow(
    samples: LiveNamespaceMeta['metricWindows'][string] | undefined,
): LiveNamespaceMetricWindow | undefined {
    if (!samples || samples.length === 0) {
        return undefined;
    }
    let triggered = 0;
    let failed = 0;
    let cacheHit = 0;
    for (const sample of samples) {
        triggered += sample.triggered;
        failed += sample.failed;
        cacheHit += sample.cacheHit;
    }
    if (triggered <= 0 && failed <= 0 && cacheHit <= 0) {
        return undefined;
    }
    return {
        durationMs: LIVE_DIAGNOSTICS_WINDOW_MS,
        triggered,
        failed,
        cacheHit,
        sampleCount: samples.length,
    };
}

function downsampleValues(values: number[], limit: number): number[] {
    if (values.length <= limit) {
        return values.slice();
    }
    const step = values.length / limit;
    const buckets: number[] = [];
    for (let bucketIdx = 0; bucketIdx < limit; bucketIdx += 1) {
        const start = Math.floor(bucketIdx * step);
        const end = Math.max(Math.floor((bucketIdx + 1) * step), start + 1);
        let sum = 0;
        let count = 0;
        for (let index = start; index < end && index < values.length; index += 1) {
            sum += values[index];
            count += 1;
        }
        buckets.push(count > 0 ? sum / count : values[Math.min(start, values.length - 1)]);
    }
    return buckets;
}

function summarizeExtraWindow(
    samples: LiveNamespaceMeta['extraWindows'][string][string] | undefined,
): LiveNamespaceMetricExtraWindow | undefined {
    if (!samples || samples.length === 0) {
        return undefined;
    }
    let max = -Infinity;
    let min = Infinity;
    let total = 0;
    for (const sample of samples) {
        max = Math.max(max, sample.value);
        min = Math.min(min, sample.value);
        total += sample.value;
    }
    const values = downsampleValues(
        samples.map((sample) => sample.value),
        24,
    );
    return {
        durationMs: LIVE_DIAGNOSTICS_WINDOW_MS,
        samples: samples.length,
        latest: samples[samples.length - 1]?.value ?? 0,
        average: total / samples.length,
        max,
        min,
        values,
    } satisfies LiveNamespaceMetricExtraWindow;
}

function buildLiveNamespaceDiagnostics(meta: LiveNamespaceMeta): LiveNamespaceDiagnostics {
    const timeline = meta.events.map<LiveNamespaceTimelineEntry>((event, index) => {
        const previousTimestamp = index > 0 ? meta.events[index - 1].timestamp : meta.startedAt;
        const sinceStart = Math.max(0, event.timestamp - meta.startedAt);
        const sincePrevious = Math.max(0, event.timestamp - previousTimestamp);
        return Object.freeze({
            key: event.key,
            provider: event.provider,
            timestamp: event.timestamp,
            sinceStart,
            sincePrevious,
        });
    });

    const metricsEntries = Object.fromEntries(
        Object.entries(meta.metrics).map(([name, metric]) => {
            const windowSummary = summarizeMetricWindow(meta.metricWindows[name]);
            const frozenWindow = windowSummary ? Object.freeze(windowSummary) : undefined;
            const extraWindowMeta = meta.extraWindows[name];
            let extraWindows: Record<string, LiveNamespaceMetricExtraWindow> | undefined;
            if (extraWindowMeta) {
                const summaryEntries: Array<[string, LiveNamespaceMetricExtraWindow]> = [];
                for (const [extraKey, samples] of Object.entries(extraWindowMeta)) {
                    const summary = summarizeExtraWindow(samples);
                    if (summary) {
                        summaryEntries.push([extraKey, Object.freeze(summary)]);
                    }
                }
                if (summaryEntries.length) {
                    extraWindows = Object.freeze(Object.fromEntries(summaryEntries));
                }
            }
            const payload: LiveNamespaceMetric = { ...metric };
            if (frozenWindow) {
                payload.window = frozenWindow;
            }
            if (extraWindows) {
                payload.extraWindows = extraWindows;
            }
            return [name, Object.freeze(payload)];
        }),
    );

    return Object.freeze({
        order: Object.freeze([...meta.order]) as readonly LiveNamespaceKey[],
        providers: Object.freeze({ ...meta.providers }),
        timeline: Object.freeze(timeline),
        metrics: Object.freeze(metricsEntries),
        startedAt: meta.startedAt,
        lastUpdated: meta.lastUpdated,
    });
}

export function refreshLiveNamespaceDiagnostics(
    owner: LiveNamespaceOwner,
    meta: LiveNamespaceMeta,
): LiveNamespaceDiagnostics {
    const diagnostics = buildLiveNamespaceDiagnostics(meta);
    owner.DashboardLiveDiagnostics = diagnostics;
    const dispatcher = owner.dispatchEvent?.bind(owner);
    const ownerWithCustomEvent = owner as LiveNamespaceOwner & { CustomEvent?: typeof CustomEvent };
    const CustomEventCtor =
        typeof ownerWithCustomEvent.CustomEvent === 'function'
            ? ownerWithCustomEvent.CustomEvent
            : typeof CustomEvent === 'function'
              ? CustomEvent
              : null;
    if (dispatcher && typeof dispatcher === 'function' && CustomEventCtor) {
        try {
            const event = new CustomEventCtor(DIAGNOSTICS_EVENT, { detail: diagnostics });
            dispatcher(event);
        } catch (error) {
            console.debug?.('[DashboardLive] Failed to dispatch diagnostics event', error);
        }
    }
    return diagnostics;
}

function pruneMetricWindowSamples(
    samples: LiveNamespaceMeta['metricWindows'][string] | undefined,
    now: number,
): boolean {
    if (!samples || samples.length === 0) {
        return false;
    }
    const oldestAllowed = now - LIVE_DIAGNOSTICS_WINDOW_MS;
    let dropCount = 0;
    while (dropCount < samples.length && samples[dropCount].timestamp < oldestAllowed) {
        dropCount += 1;
    }
    if (dropCount > 0) {
        samples.splice(0, dropCount);
        return true;
    }
    return false;
}

function pruneExtraWindowSamples(
    samples: LiveNamespaceMeta['extraWindows'][string][string] | undefined,
    now: number,
): boolean {
    if (!samples || samples.length === 0) {
        return false;
    }
    const oldestAllowed = now - LIVE_DIAGNOSTICS_WINDOW_MS;
    let dropCount = 0;
    while (dropCount < samples.length && samples[dropCount].timestamp < oldestAllowed) {
        dropCount += 1;
    }
    let mutated = false;
    if (dropCount > 0) {
        samples.splice(0, dropCount);
        mutated = true;
    }
    const MAX_EXTRA_WINDOW_SAMPLES = 240;
    if (samples.length > MAX_EXTRA_WINDOW_SAMPLES) {
        const excess = samples.length - MAX_EXTRA_WINDOW_SAMPLES;
        samples.splice(0, excess);
        mutated = true;
    }
    return mutated;
}

function pruneAllMetricWindows(meta: LiveNamespaceMeta): boolean {
    let mutated = false;
    const now = getTimestamp();
    for (const samples of Object.values(meta.metricWindows)) {
        if (pruneMetricWindowSamples(samples, now)) {
            mutated = true;
        }
    }
    for (const extraWindows of Object.values(meta.extraWindows)) {
        for (const samples of Object.values(extraWindows)) {
            if (pruneExtraWindowSamples(samples, now)) {
                mutated = true;
            }
        }
    }
    if (mutated) {
        meta.lastUpdated = now;
    }
    return mutated;
}

export function getLiveNamespaceDiagnostics(
    owner: LiveNamespaceOwner = window as LiveNamespaceOwner,
): LiveNamespaceDiagnostics {
    const handle = ensureLiveNamespace(owner);
    const meta = getNamespaceMeta(handle.live);
    const windowsPruned = pruneAllMetricWindows(meta);
    const cached = owner.DashboardLiveDiagnostics;
    if (windowsPruned || !cached || cached.lastUpdated !== meta.lastUpdated) {
        return refreshLiveNamespaceDiagnostics(owner, meta);
    }
    return cached;
}

export function logLiveNamespaceDiagnostics(owner: LiveNamespaceOwner = window as LiveNamespaceOwner): void {
    if (typeof console === 'undefined') {
        return;
    }
    const diagnostics = getLiveNamespaceDiagnostics(owner);
    const entries = diagnostics.timeline;
    if (!entries.length) {
        console.info?.('[DashboardLive] No APIs registered yet.');
        return;
    }

    const rows = entries.map((entry) => ({
        api: entry.key,
        provider: entry.provider ?? '(unknown)',
        'Δ since start (ms)': entry.sinceStart.toFixed(2),
        'Δ since previous (ms)': entry.sincePrevious.toFixed(2),
    }));

    const providerSummary = diagnostics.order.map((key) => ({
        api: key,
        provider: diagnostics.providers[key] ?? '(unknown)',
    }));

    if (typeof console.groupCollapsed === 'function') {
        console.groupCollapsed(`[DashboardLive] Initialization timeline (${rows.length})`);
    }
    if (typeof console.table === 'function') {
        console.table(rows);
    } else {
        rows.forEach((row) => {
            console.debug?.('[DashboardLive]', row);
        });
    }
    console.debug?.('[DashboardLive] Providers', providerSummary);
    if (typeof console.groupEnd === 'function') {
        console.groupEnd();
    }
}

export { DIAGNOSTICS_EVENT };
