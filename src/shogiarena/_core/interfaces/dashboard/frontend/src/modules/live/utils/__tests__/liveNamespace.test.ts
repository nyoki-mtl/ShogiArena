import { beforeEach, describe, expect, it, vi } from 'vitest';

import {
    DIAGNOSTICS_EVENT,
    LIVE_DIAGNOSTICS_WINDOW_MS,
    ensureLiveNamespace,
    getLiveNamespaceDiagnostics,
    logLiveNamespaceDiagnostics,
    recordLiveDiagnosticsMetric,
    registerLiveApi,
    resetLiveNamespace,
    startDiagnosticsStopwatch,
    type LiveNamespaceDiagnostics,
    type LiveNamespaceOwner,
} from '@/modules/live/utils/liveNamespace';
import type { LiveCardsApi, LiveTimeApi } from '@/types/live';

describe('Live namespace diagnostics', () => {
    let owner: LiveNamespaceOwner;

    beforeEach(() => {
        owner = { console } as unknown as LiveNamespaceOwner;
        resetLiveNamespace(owner);
    });

    it('collects provider metadata and timeline entries', () => {
        ensureLiveNamespace(owner);
        registerLiveApi(owner, 'time', {} as LiveTimeApi, { provider: 'test/time' });
        registerLiveApi(owner, 'cards', {} as LiveCardsApi, { provider: 'test/cards' });

        const diagnostics = getLiveNamespaceDiagnostics(owner);

        expect(diagnostics.order).toEqual(['time', 'cards']);
        expect(diagnostics.providers.time).toBe('test/time');
        expect(diagnostics.providers.cards).toBe('test/cards');
        expect(diagnostics.timeline.length).toBe(2);
        expect(diagnostics.timeline[0].sinceStart).toBeGreaterThanOrEqual(0);
        expect(owner.DashboardLiveDiagnostics).toBe(diagnostics);
    });

    it('mirrors registered APIs via accessors on the owner window', () => {
        ensureLiveNamespace(owner);
        const timeApi = {} as LiveTimeApi;
        registerLiveApi(owner, 'time', timeApi, { provider: 'test/time' });

        const descriptor = Object.getOwnPropertyDescriptor(owner, 'DashboardLiveTime');
        expect(descriptor?.get).toBeDefined();
        expect(descriptor?.value).toBeUndefined();
        expect(owner.DashboardLiveTime).toBe(timeApi);
    });

    it('dispatches diagnostics events with timeline snapshots', () => {
        const target = new EventTarget();
        const eventOwner = Object.assign(target, {
            console,
            CustomEvent,
            addEventListener: target.addEventListener.bind(target),
            removeEventListener: target.removeEventListener.bind(target),
            dispatchEvent: target.dispatchEvent.bind(target),
        }) as unknown as LiveNamespaceOwner;

        resetLiveNamespace(eventOwner);
        ensureLiveNamespace(eventOwner);

        const received: LiveNamespaceDiagnostics[] = [];
        eventOwner.addEventListener(DIAGNOSTICS_EVENT, (event) => {
            const detail = (event as CustomEvent<LiveNamespaceDiagnostics>).detail;
            if (detail) {
                received.push(detail);
            }
        });

        registerLiveApi(eventOwner, 'time', {} as LiveTimeApi, { provider: 'test/time' });
        registerLiveApi(eventOwner, 'cards', {} as LiveCardsApi, { provider: 'test/cards' });

        expect(received.length).toBe(2);
        expect(received[0].order).toEqual(['time']);
        expect(received[1].order).toEqual(['time', 'cards']);
        expect(received[1].providers.cards).toBe('test/cards');
    });

    it('logs timeline information to the console', () => {
        ensureLiveNamespace(owner);
        registerLiveApi(owner, 'time', {} as LiveTimeApi, { provider: 'test/time' });
        registerLiveApi(owner, 'cards', {} as LiveCardsApi, { provider: 'test/cards' });

        const groupCollapsed = vi.spyOn(console, 'groupCollapsed').mockImplementation(() => {});
        const table = vi.spyOn(console, 'table').mockImplementation(() => {});
        const debug = vi.spyOn(console, 'debug').mockImplementation(() => {});
        const groupEnd = vi.spyOn(console, 'groupEnd').mockImplementation(() => {});

        logLiveNamespaceDiagnostics(owner);

        expect(groupCollapsed).toHaveBeenCalledTimes(1);
        expect(table).toHaveBeenCalledTimes(1);
        expect(debug).toHaveBeenCalledWith('[DashboardLive] Providers', [
            { api: 'time', provider: 'test/time' },
            { api: 'cards', provider: 'test/cards' },
        ]);

        groupCollapsed.mockRestore();
        table.mockRestore();
        debug.mockRestore();
        groupEnd.mockRestore();
    });

    it('records hydration metrics via recordLiveDiagnosticsMetric', () => {
        ensureLiveNamespace(owner);
        registerLiveApi(owner, 'time', {} as LiveTimeApi, { provider: 'test/time' });
        recordLiveDiagnosticsMetric('spsa:hydration:analysis', { triggered: 1 }, owner);
        recordLiveDiagnosticsMetric('spsa:hydration:analysis', { failed: 1 }, owner);
        recordLiveDiagnosticsMetric('spsa:hydration:analysis', { cacheHit: 3 }, owner);

        const diagnostics = getLiveNamespaceDiagnostics(owner);
        const metric = diagnostics.metrics['spsa:hydration:analysis'];
        expect(metric).toBeDefined();
        expect(metric?.triggered).toBe(1);
        expect(metric?.failed).toBe(1);
        expect(metric?.cacheHit).toBe(3);
        expect(metric?.window?.durationMs).toBe(LIVE_DIAGNOSTICS_WINDOW_MS);
        expect(metric?.window?.triggered).toBeGreaterThanOrEqual(1);
        expect(metric?.window?.sampleCount).toBeGreaterThanOrEqual(2);
    });

    it('aggregates extra metric fields such as payloadKb', () => {
        ensureLiveNamespace(owner);
        registerLiveApi(owner, 'time', {} as LiveTimeApi, { provider: 'test/time' });
        recordLiveDiagnosticsMetric('spsa:hydration:update-detail', { payloadKb: 150 }, owner);
        recordLiveDiagnosticsMetric('spsa:hydration:update-detail', { payloadKb: 210 }, owner);

        const diagnostics = getLiveNamespaceDiagnostics(owner);
        const metric = diagnostics.metrics['spsa:hydration:update-detail'];
        expect(metric?.extras?.payloadKb).toEqual(
            expect.objectContaining({
                last: 210,
                total: 360,
                samples: 2,
                max: 210,
                min: 150,
            }),
        );
        expect(metric?.extraWindows?.payloadKb).toBeDefined();
        expect(metric?.extraWindows?.payloadKb?.latest).toBe(210);
        expect(metric?.extraWindows?.payloadKb?.values.length).toBeGreaterThan(0);
    });

    it('records latency and retry extras via diagnostics stopwatch', () => {
        ensureLiveNamespace(owner);
        registerLiveApi(owner, 'time', {} as LiveTimeApi, { provider: 'test/time' });
        const stopwatch = startDiagnosticsStopwatch('hydrator:test', owner);
        stopwatch.markRetry();
        stopwatch.markRetry();
        stopwatch.succeed();

        const diagnostics = getLiveNamespaceDiagnostics(owner);
        const metric = diagnostics.metrics['hydrator:test'];
        expect(metric).toBeDefined();
        expect(metric?.extras?.latencyMs?.samples).toBe(1);
        expect(metric?.extras?.latencyMs?.last).toBeGreaterThanOrEqual(0);
        expect(metric?.extras?.retries?.last).toBe(2);
    });

    it('rejects direct assignment to owner namespace keys', () => {
        ensureLiveNamespace(owner);
        expect(() => {
            (owner as unknown as Record<string, unknown>).DashboardLiveTime = {} as LiveTimeApi;
        }).toThrow(/must be registered via registerLiveApi/);
    });
});
