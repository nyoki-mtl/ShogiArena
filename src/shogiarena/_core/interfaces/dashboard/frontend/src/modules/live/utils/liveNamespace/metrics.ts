import {
    ensureLiveNamespace,
    getNamespaceMeta,
    getTimestamp,
    refreshLiveNamespaceDiagnostics,
    LIVE_DIAGNOSTICS_WINDOW_MS,
    type LiveDiagnosticsMetricUpdate,
    type LiveNamespaceMetric,
    type LiveNamespaceMetricExtra,
    type LiveNamespaceMetricExtraSample,
    type LiveNamespaceMetricWindowSample,
    type LiveNamespaceOwner,
} from './core';

const RESERVED_METRIC_FIELDS = new Set(['triggered', 'failed', 'cacheHit']);
const MAX_EXTRA_WINDOW_SAMPLES = 240;
const DIAGNOSTICS_REFRESH_THROTTLE_MS = 1000;
let lastDiagnosticsRefreshAt = 0;
let pendingDiagnosticsRefresh: ReturnType<typeof setTimeout> | null = null;

type MetricExtraEntry = [key: string, value: number];

function collectMetricExtraEntries(update: LiveDiagnosticsMetricUpdate): MetricExtraEntry[] {
    const entries: MetricExtraEntry[] = [];
    for (const [key, rawValue] of Object.entries(update)) {
        if (RESERVED_METRIC_FIELDS.has(key)) {
            continue;
        }
        if (typeof rawValue !== 'number' || !Number.isFinite(rawValue)) {
            continue;
        }
        entries.push([key, rawValue]);
    }
    return entries;
}

function mergeMetricExtras(
    existing: Record<string, LiveNamespaceMetricExtra> | undefined,
    entries: MetricExtraEntry[],
): Record<string, LiveNamespaceMetricExtra> | undefined {
    let next = existing ? { ...existing } : undefined;
    for (const [key, rawValue] of entries) {
        if (!next) {
            next = {};
        }
        const prev = next[key];
        if (!prev) {
            next[key] = {
                total: rawValue,
                last: rawValue,
                samples: 1,
                max: rawValue,
                min: rawValue,
            };
            continue;
        }
        next[key] = {
            total: prev.total + rawValue,
            last: rawValue,
            samples: prev.samples + 1,
            max: Math.max(prev.max, rawValue),
            min: Math.min(prev.min, rawValue),
        };
    }
    return next;
}

function ensureExtraWindowSamples(
    meta: ReturnType<typeof getNamespaceMeta>,
    metricName: string,
    extraKey: string,
): LiveNamespaceMetricExtraSample[] {
    let metricExtras = meta.extraWindows[metricName];
    if (!metricExtras) {
        metricExtras = {};
        meta.extraWindows[metricName] = metricExtras;
    }
    let samples = metricExtras[extraKey];
    if (!samples) {
        samples = [];
        metricExtras[extraKey] = samples;
    }
    return samples;
}

function pruneMetricWindowSamples(samples: LiveNamespaceMetricWindowSample[] | undefined, now: number): boolean {
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

function pruneExtraWindowSamples(samples: LiveNamespaceMetricExtraSample[] | undefined, now: number): boolean {
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
    if (samples.length > MAX_EXTRA_WINDOW_SAMPLES) {
        const excess = samples.length - MAX_EXTRA_WINDOW_SAMPLES;
        samples.splice(0, excess);
        mutated = true;
    }
    return mutated;
}

export function recordLiveDiagnosticsMetric(
    name: string,
    update: LiveDiagnosticsMetricUpdate,
    owner: LiveNamespaceOwner = window as LiveNamespaceOwner,
): void {
    if (!name) {
        return;
    }
    const handle = ensureLiveNamespace(owner);
    const meta = getNamespaceMeta(handle.live);
    const stamped = getTimestamp();
    const deltaTriggered = Math.max(0, update.triggered ?? 0);
    const deltaFailed = Math.max(0, update.failed ?? 0);
    const deltaCacheHit = Math.max(0, update.cacheHit ?? 0);
    const existing = meta.metrics[name] ?? { name, triggered: 0, failed: 0, cacheHit: 0, updatedAt: stamped };
    const nextTriggered = Math.max(0, existing.triggered + deltaTriggered);
    const nextFailed = Math.max(0, existing.failed + deltaFailed);
    const nextCacheHit = Math.max(0, existing.cacheHit + deltaCacheHit);
    const extraEntries = collectMetricExtraEntries(update);
    const nextExtras = mergeMetricExtras(existing.extras, extraEntries);
    const metric: LiveNamespaceMetric = {
        name,
        triggered: nextTriggered,
        failed: nextFailed,
        cacheHit: nextCacheHit,
        updatedAt: stamped,
    };
    if (nextExtras) {
        metric.extras = nextExtras;
    }
    meta.metrics[name] = metric;
    if (extraEntries.length) {
        for (const [extraKey, value] of extraEntries) {
            const samples = ensureExtraWindowSamples(meta, name, extraKey);
            samples.push({ timestamp: stamped, value });
            pruneExtraWindowSamples(samples, stamped);
        }
    }
    const hasWindowDelta = deltaTriggered > 0 || deltaFailed > 0 || deltaCacheHit > 0;
    if (hasWindowDelta) {
        let samples = meta.metricWindows[name];
        if (!samples) {
            samples = [];
            meta.metricWindows[name] = samples;
        }
        samples.push({
            timestamp: stamped,
            triggered: deltaTriggered,
            failed: deltaFailed,
            cacheHit: deltaCacheHit,
        });
        pruneMetricWindowSamples(samples, stamped);
    }
    meta.lastUpdated = stamped;
    // Throttle expensive diagnostics refresh to avoid degrading performance as metrics accumulate.
    // The full diagnostics object is rebuilt and dispatched via CustomEvent, which becomes increasingly
    // expensive as the number of samples grows over a session.
    const now =
        typeof performance !== 'undefined' && typeof performance.now === 'function' ? performance.now() : Date.now();
    if (now - lastDiagnosticsRefreshAt >= DIAGNOSTICS_REFRESH_THROTTLE_MS) {
        lastDiagnosticsRefreshAt = now;
        refreshLiveNamespaceDiagnostics(owner, meta);
    } else if (!pendingDiagnosticsRefresh) {
        pendingDiagnosticsRefresh = setTimeout(() => {
            pendingDiagnosticsRefresh = null;
            lastDiagnosticsRefreshAt =
                typeof performance !== 'undefined' && typeof performance.now === 'function'
                    ? performance.now()
                    : Date.now();
            refreshLiveNamespaceDiagnostics(owner, meta);
        }, DIAGNOSTICS_REFRESH_THROTTLE_MS);
    }
}
