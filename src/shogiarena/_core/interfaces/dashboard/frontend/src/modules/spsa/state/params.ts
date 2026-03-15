import type { NormalizedSpsaParams } from '@/modules/spsa/types';

import { createCacheEntry } from './cache';
import { clearInitialUpdateArtifacts, recomputeInitialUpdateArtifacts } from './initial-artifacts';
import { state, PARAMS_CACHE_TTL_MS } from './store';

export function resolveParams(data: NormalizedSpsaParams): void {
    state.params.data = data;
    state.params.status = 'ready';
    state.params.error = null;
    state.params.lastFetched = Date.now();
    recomputeInitialUpdateArtifacts();
}

export function recordParamsError(message: string): void {
    state.params.status = 'error';
    state.params.error = message;
    state.params.lastFetched = Date.now();
    clearInitialUpdateArtifacts();
}

export function getCachedParams(now = Date.now()): NormalizedSpsaParams | null {
    const cache = state.paramsCache;
    if (!cache) return null;
    return cache.expiresAt > now ? cache.value : null;
}

export function setParamsCache(data: NormalizedSpsaParams, ttlMs = PARAMS_CACHE_TTL_MS): void {
    state.paramsCache = createCacheEntry(data, ttlMs);
}

export function clearParamsCache(): void {
    state.paramsCache = null;
}
