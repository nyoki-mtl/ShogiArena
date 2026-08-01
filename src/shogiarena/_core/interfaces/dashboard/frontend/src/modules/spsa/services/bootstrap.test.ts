import { describe, expect, it, vi } from 'vitest';
import type { DeferredHydrator } from '@/modules/shared/utils/hydration';
import { createBootstrapManager } from './bootstrap';

function createHydrator(): DeferredHydrator {
    return {
        ensure: vi.fn(),
        reset: vi.fn(),
        markDirty: vi.fn(),
        isHydrated: () => false,
    };
}

describe('SPSA bootstrap lifecycle', () => {
    it('aborts stale bootstrap requests before they can overwrite reactivated state', async () => {
        const applied = { summary: '', params: '', updates: '' };
        const staleSignals: AbortSignal[] = [];
        const staleResolvers: Array<() => void> = [];
        const requestCounts = { summary: 0, params: 0, updates: 0 };
        const request = async (key: keyof typeof applied, signal: AbortSignal | undefined): Promise<void> => {
            requestCounts[key] += 1;
            if (requestCounts[key] > 1) {
                applied[key] = 'new';
                return;
            }
            if (!signal) {
                throw new Error(`${key} bootstrap request requires an AbortSignal`);
            }
            staleSignals.push(signal);
            await new Promise<void>((resolve, reject) => {
                staleResolvers.push(() => {
                    if (signal.aborted) {
                        return;
                    }
                    applied[key] = 'old';
                    resolve();
                });
                signal.addEventListener(
                    'abort',
                    () => {
                        reject(new DOMException('Aborted', 'AbortError'));
                    },
                    { once: true },
                );
            });
        };
        const startRealtimeStreams = vi.fn();
        const manager = createBootstrapManager({
            fetchers: {
                requestSummary: vi.fn(async (_force, signal) => request('summary', signal)),
                requestParams: vi.fn(async (_force, signal) => request('params', signal)),
                requestUpdates: vi.fn(async (options) => request('updates', options.signal)),
                resetAnalysisFetches: vi.fn(),
            },
            hydrators: {
                updatesHydrator: createHydrator(),
                analysisHydrator: createHydrator(),
                ltcHydrator: createHydrator(),
            },
            resetCaches: vi.fn(),
            reportRecoverableFailure: vi.fn(),
            startRealtimeStreams,
        });

        const staleBootstrap = manager.beginBootstrapSequence();
        manager.resetHydrationState();
        await staleBootstrap;

        expect(staleSignals).toHaveLength(3);
        expect(staleSignals.every((signal) => signal.aborted)).toBe(true);
        expect(startRealtimeStreams).not.toHaveBeenCalled();

        await manager.beginBootstrapSequence();
        for (const resolve of staleResolvers) {
            resolve();
        }
        await Promise.resolve();

        expect(applied).toEqual({ summary: 'new', params: 'new', updates: 'new' });
        expect(startRealtimeStreams).toHaveBeenCalledTimes(1);
    });
});
