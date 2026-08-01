import { afterEach, describe, expect, it, vi } from 'vitest';
import type { DashboardCore } from '@/types/dashboard';
import type { DeferredHydrator } from '@/modules/shared/utils/hydration';
import { getState, setActive } from '../state';
import { createBootstrapManager } from './bootstrap';
import { createLifecycle } from './lifecycle';

function createHydrator(): DeferredHydrator {
    return {
        ensure: vi.fn(),
        reset: vi.fn(),
        markDirty: vi.fn(),
        isHydrated: () => false,
    };
}

afterEach(() => {
    setActive(false);
    vi.restoreAllMocks();
});

describe('SPSA browser lifecycle', () => {
    it('bootstraps again after a top-level tab deactivation and reactivation', async () => {
        const beginBootstrapSequence = vi.fn(async () => undefined);
        const resetHydrationState = vi.fn();
        const closeRevisionStream = vi.fn();
        const cancelOngoingRequests = vi.fn();
        const lifecycle = createLifecycle({
            core: {} as DashboardCore,
            document,
            callbacks: {},
            state: getState(),
            beginBootstrapSequence,
            resetHydrationState,
            resetUpdates: vi.fn(),
            recomputeTrend: vi.fn(),
            setActive,
            setVisibilityPaused: vi.fn(),
            hideRevisionStream: vi.fn(),
            markRevisionOffline: vi.fn(),
            closeRevisionStream,
            cancelOngoingRequests,
        });

        lifecycle.connect();
        await Promise.resolve();
        lifecycle.pause();
        lifecycle.connect();
        await Promise.resolve();

        expect(beginBootstrapSequence).toHaveBeenCalledTimes(2);
        expect(closeRevisionStream).toHaveBeenCalledOnce();
        expect(cancelOngoingRequests).toHaveBeenCalledOnce();
        expect(resetHydrationState).toHaveBeenCalledTimes(3);
    });

    it('closes streams while hidden or offline and rehydrates on recovery', async () => {
        let hidden = false;
        vi.spyOn(document, 'hidden', 'get').mockImplementation(() => hidden);
        setActive(true);

        const hideRevisionStream = vi.fn();
        const markRevisionOffline = vi.fn();
        const beginBootstrapSequence = vi.fn(async () => {});
        const resetHydrationState = vi.fn();
        const cancelOngoingRequests = vi.fn();
        const closeRevisionStream = vi.fn();
        const lifecycle = createLifecycle({
            core: {} as DashboardCore,
            document,
            callbacks: {},
            state: getState(),
            beginBootstrapSequence,
            resetHydrationState,
            resetUpdates: vi.fn(),
            recomputeTrend: vi.fn(),
            setActive,
            setVisibilityPaused: vi.fn(),
            hideRevisionStream,
            markRevisionOffline,
            closeRevisionStream,
            cancelOngoingRequests,
        });

        lifecycle.setupVisibilityRefresh(true);
        await Promise.resolve();
        beginBootstrapSequence.mockClear();

        hidden = true;
        document.dispatchEvent(new Event('visibilitychange'));
        expect(hideRevisionStream).toHaveBeenCalledOnce();
        expect(cancelOngoingRequests).toHaveBeenCalledOnce();
        expect(resetHydrationState).toHaveBeenCalledOnce();

        hidden = false;
        document.dispatchEvent(new Event('visibilitychange'));
        await Promise.resolve();
        expect(beginBootstrapSequence).toHaveBeenCalledOnce();

        window.dispatchEvent(new Event('offline'));
        expect(markRevisionOffline).toHaveBeenCalledOnce();
        expect(cancelOngoingRequests).toHaveBeenCalledTimes(2);
        expect(resetHydrationState).toHaveBeenCalledTimes(2);

        window.dispatchEvent(new Event('online'));
        await Promise.resolve();
        expect(beginBootstrapSequence).toHaveBeenCalledTimes(2);
    });

    it('invalidates a deferred bootstrap while hidden before opening the revision stream', async () => {
        let hidden = false;
        vi.spyOn(document, 'hidden', 'get').mockImplementation(() => hidden);

        const applied = { summary: '', params: '', updates: '' };
        const requestCounts = { summary: 0, params: 0, updates: 0 };
        const staleSignals: AbortSignal[] = [];
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
            await new Promise<void>((_resolve, reject) => {
                signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), {
                    once: true,
                });
            });
        };
        const startRealtimeStreams = vi.fn();
        const bootstrap = createBootstrapManager({
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
        const lifecycle = createLifecycle({
            core: {} as DashboardCore,
            document,
            callbacks: {},
            state: getState(),
            beginBootstrapSequence: bootstrap.beginBootstrapSequence,
            resetHydrationState: bootstrap.resetHydrationState,
            resetUpdates: vi.fn(),
            recomputeTrend: vi.fn(),
            setActive,
            setVisibilityPaused: vi.fn(),
            hideRevisionStream: vi.fn(),
            markRevisionOffline: vi.fn(),
            closeRevisionStream: vi.fn(),
            cancelOngoingRequests: vi.fn(),
        });

        lifecycle.setupVisibilityRefresh(true);
        lifecycle.connect();
        await Promise.resolve();

        hidden = true;
        document.dispatchEvent(new Event('visibilitychange'));
        await Promise.resolve();

        expect(staleSignals).toHaveLength(3);
        expect(staleSignals.every((signal) => signal.aborted)).toBe(true);
        expect(startRealtimeStreams).not.toHaveBeenCalled();

        hidden = false;
        document.dispatchEvent(new Event('visibilitychange'));
        await vi.waitFor(() => {
            expect(applied).toEqual({ summary: 'new', params: 'new', updates: 'new' });
            expect(startRealtimeStreams).toHaveBeenCalledOnce();
        });
    });
});
