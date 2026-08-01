import { afterEach, describe, expect, it, vi } from 'vitest';
import type { SpsaConnectionStatus } from '@/modules/spsa/types';
import { createRevisionFeedController } from './revision-feed';

class FakeEventSource {
    onopen: ((event: Event) => void) | null = null;
    onmessage: ((event: MessageEvent<string>) => void) | null = null;
    onerror: ((event: Event) => void) | null = null;
    closed = false;
    private listeners = new Map<string, (event: MessageEvent<string>) => void>();

    addEventListener(type: string, listener: (event: MessageEvent<string>) => void): void {
        this.listeners.set(type, listener);
    }

    close(): void {
        this.closed = true;
    }

    emitOpen(): void {
        this.onopen?.(new Event('open'));
    }

    emitRevision(data: unknown): void {
        const event = new MessageEvent<string>('spsa_revision', { data: JSON.stringify(data) });
        this.listeners.get('spsa_revision')?.(event);
    }

    emitRawRevision(data: string): void {
        const event = new MessageEvent<string>('spsa_revision', { data });
        this.listeners.get('spsa_revision')?.(event);
    }

    emitError(): void {
        this.onerror?.(new Event('error'));
    }
}

function createHarness(options: { refreshSnapshot?: (gapDetected: boolean) => Promise<void> } = {}) {
    const sources: FakeEventSource[] = [];
    const statuses: SpsaConnectionStatus[] = [];
    const refreshes: boolean[] = [];
    const failures: string[] = [];
    let offline = false;
    const controller = createRevisionFeedController({
        buildUrl: () => '/api/spsa/revisions/stream',
        isOffline: () => offline,
        createEventSource: () => {
            const source = new FakeEventSource();
            sources.push(source);
            return source;
        },
        setConnection: (_source, status) => statuses.push(status),
        refreshSnapshot: async (gapDetected) => {
            refreshes.push(gapDetected);
            await options.refreshSnapshot?.(gapDetected);
        },
        markAnalysisDataStale: vi.fn(),
        clearDisconnectNotice: vi.fn(),
        reportFailure: (context) => failures.push(context),
    });
    return {
        controller,
        sources,
        statuses,
        refreshes,
        failures,
        setOffline: (value: boolean) => {
            offline = value;
        },
    };
}

afterEach(() => {
    vi.useRealTimers();
});

describe('revision feed connection state machine', () => {
    it('opens and ignores duplicate revisions when detecting a gap', async () => {
        const harness = createHarness();
        harness.controller.open();
        expect(harness.statuses.at(-1)).toBe('connecting');

        harness.sources[0].emitOpen();
        expect(harness.statuses.at(-1)).toBe('open');

        harness.sources[0].emitRevision({ data: { revision: 5, gap_detected: false, terminal: false } });
        harness.sources[0].emitRevision({ data: { revision: 5, gap_detected: false, terminal: false } });
        harness.sources[0].emitRevision({ data: { revision: 7, gap_detected: false, terminal: false } });
        await Promise.resolve();

        expect(harness.refreshes).toEqual([false, false, true]);
        expect(harness.failures).toEqual([]);
    });

    it('uses capped backoff instead of permanently closing after onerror', () => {
        vi.useFakeTimers();
        const harness = createHarness();
        harness.controller.open();
        harness.sources[0].emitError();

        expect(harness.sources[0].closed).toBe(true);
        expect(harness.statuses.at(-1)).toBe('recovering');
        vi.advanceTimersByTime(999);
        expect(harness.sources).toHaveLength(1);
        vi.advanceTimersByTime(1);
        expect(harness.sources).toHaveLength(2);
        expect(harness.statuses.at(-1)).toBe('recovering');

        harness.sources[1].emitError();
        vi.advanceTimersByTime(1_999);
        expect(harness.sources).toHaveLength(2);
        vi.advanceTimersByTime(1);
        expect(harness.sources).toHaveLength(3);
    });

    it('closes while hidden and rehydrates before visible recovery', async () => {
        const harness = createHarness();
        harness.controller.open();
        harness.sources[0].emitOpen();

        harness.controller.hide();
        expect(harness.sources[0].closed).toBe(true);
        expect(harness.statuses.at(-1)).toBe('hidden');

        harness.controller.recover();
        await Promise.resolve();
        expect(harness.refreshes).toEqual([false]);
        expect(harness.sources).toHaveLength(2);
        expect(harness.statuses.at(-1)).toBe('recovering');
    });

    it('stays offline until an explicit recovery signal', () => {
        const harness = createHarness();
        harness.controller.open();
        harness.setOffline(true);
        harness.controller.goOffline();
        expect(harness.sources[0].closed).toBe(true);
        expect(harness.statuses.at(-1)).toBe('offline');

        harness.controller.open();
        expect(harness.sources).toHaveLength(1);
        harness.setOffline(false);
        harness.controller.recover();
        expect(harness.sources).toHaveLength(2);
    });

    it('reopens after a top-level tab close and reset cycle', () => {
        const harness = createHarness();
        harness.controller.open();
        harness.controller.close();
        harness.controller.reset();
        harness.controller.open();

        expect(harness.sources[0].closed).toBe(true);
        expect(harness.sources).toHaveLength(2);
        expect(harness.statuses.at(-1)).toBe('connecting');
    });

    it('closes permanently after a terminal revision while refreshing the final snapshot', async () => {
        const harness = createHarness();
        harness.controller.open();
        harness.sources[0].emitRevision({ data: { revision: 9, gap_detected: false, terminal: true } });
        await Promise.resolve();
        await Promise.resolve();

        expect(harness.sources[0].closed).toBe(true);
        expect(harness.statuses.at(-1)).toBe('closed');
        expect(harness.refreshes).toEqual([false]);
        harness.controller.recover();
        expect(harness.sources).toHaveLength(1);
        expect(harness.statuses.at(-1)).toBe('closed');
    });

    it('retries a failed terminal snapshot before closing permanently', async () => {
        vi.useFakeTimers();
        const refreshSnapshot = vi
            .fn<(gapDetected: boolean) => Promise<void>>()
            .mockRejectedValueOnce(new Error('temporary failure'))
            .mockResolvedValue(undefined);
        const harness = createHarness({ refreshSnapshot });
        harness.controller.open();
        harness.sources[0].emitRevision({ data: { revision: 9, gap_detected: false, terminal: true } });
        await Promise.resolve();
        await Promise.resolve();

        expect(harness.sources[0].closed).toBe(true);
        expect(harness.statuses.at(-1)).toBe('recovering');
        expect(refreshSnapshot).toHaveBeenCalledTimes(1);

        await vi.advanceTimersByTimeAsync(1_000);

        expect(refreshSnapshot).toHaveBeenCalledTimes(2);
        expect(harness.statuses.at(-1)).toBe('closed');
        harness.controller.recover();
        expect(harness.sources).toHaveLength(1);
    });

    it('ignores a terminal snapshot completion from before reset', async () => {
        let resolveTerminalSnapshot = (): void => {
            throw new Error('terminal snapshot was not requested');
        };
        const harness = createHarness({
            refreshSnapshot: async () => {
                await new Promise<void>((resolve) => {
                    resolveTerminalSnapshot = resolve;
                });
            },
        });
        harness.controller.open();
        harness.sources[0].emitRevision({ data: { revision: 9, gap_detected: false, terminal: true } });
        harness.controller.close();
        harness.controller.reset();
        harness.controller.open();
        resolveTerminalSnapshot();
        await Promise.resolve();
        await Promise.resolve();

        expect(harness.sources).toHaveLength(2);
        expect(harness.statuses.at(-1)).toBe('connecting');
    });

    it('recovers malformed events through an authoritative snapshot', async () => {
        const harness = createHarness();
        harness.controller.open();
        harness.sources[0].emitRawRevision('{');
        await Promise.resolve();

        expect(harness.failures).toContain('SpsaRevisionFeed.onmessage');
        expect(harness.refreshes).toEqual([true]);
    });
});
