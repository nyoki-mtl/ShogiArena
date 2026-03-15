import {
    DIAGNOSTICS_EVENT,
    getNamespaceMeta,
    getTimestamp,
    ensureLiveNamespace,
    LIVE_DIAGNOSTICS_WINDOW_MS,
    refreshLiveNamespaceDiagnostics,
} from './core';
import type { LiveNamespaceDiagnostics, LiveNamespaceMeta, LiveNamespaceOwner } from './core';

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
