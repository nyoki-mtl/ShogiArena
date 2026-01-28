import type { SpsaCacheEntry } from '@/modules/spsa/types';

export function createCacheEntry<T>(value: T, ttlMs: number): SpsaCacheEntry<T> {
    return {
        value,
        expiresAt: Date.now() + Math.max(ttlMs, 0),
    };
}
