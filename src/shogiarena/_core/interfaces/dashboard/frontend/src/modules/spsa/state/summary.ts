import type { NormalizedSpsaSummary, SpsaUpdateProgress } from '@/modules/spsa/types';
import type { JsonObject } from '@/types/shared';
import { summaryStore } from '@/store';

import { createCacheEntry } from './cache';
import { state, SUMMARY_CACHE_TTL_MS } from './store';

export function resolveSummary(data: NormalizedSpsaSummary): void {
    state.summary.data = data;
    state.summary.status = 'ready';
    state.summary.error = null;
    state.summary.lastFetched = Date.now();

    // Push to unified store for cross-module consistency
    // NOTE: We do NOT call setActiveSource here - that should only be called
    // when the tab becomes active, not on every data update.
    summaryStore.applySpsaSummary({
        engines: data.engines,
        engineMeta: data.engineMeta as unknown as JsonObject,
        engineTimeControls: data.engineTimeControls,
        engineInstances: data.engineInstances,
        defaultTimeControl: data.defaultTimeControl,
    });
}

export function recordSummaryError(message: string): void {
    state.summary.status = 'error';
    state.summary.error = message;
    state.summary.lastFetched = Date.now();
}

export function getCachedSummary(now = Date.now()): NormalizedSpsaSummary | null {
    const cache = state.summaryCache;
    if (!cache) return null;
    return cache.expiresAt > now ? cache.value : null;
}

export function setSummaryCache(data: NormalizedSpsaSummary, ttlMs = SUMMARY_CACHE_TTL_MS): void {
    state.summaryCache = createCacheEntry(data, ttlMs);
}

export function clearSummaryCache(): void {
    state.summaryCache = null;
}

export function applySummaryProgressPatch(progress: SpsaUpdateProgress | null | undefined): void {
    if (!progress || !state.summary.data) {
        return;
    }
    const current = state.summary.data;
    const completed = Number(progress.completed ?? current.updatesCompleted ?? 0);
    const totalRaw = progress.total;
    const total = typeof totalRaw === 'number' && Number.isFinite(totalRaw) ? totalRaw : (current.numUpdates ?? 0);
    state.summary.data = {
        ...current,
        updatesCompleted: completed,
        numUpdates: Number(total),
    };
}
