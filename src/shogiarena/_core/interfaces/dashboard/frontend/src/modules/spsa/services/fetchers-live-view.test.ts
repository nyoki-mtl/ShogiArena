import { describe, expect, it, vi } from 'vitest';

import { getLiveViewSnapshotStore } from '@/modules/shared/stores/live-view-snapshot';
import type { SpsaSummaryResponse } from '@/modules/spsa/types';
import type { DashboardCore } from '@/types/dashboard';
import { hydrateSpsaSummaryLiveView } from './fetchers';

type Listener = (payload?: unknown) => void;

function createMockCore(): DashboardCore {
    const state = { liveViewSnapshot: null } as DashboardCore['state'];
    const listeners = new Map<string, Set<Listener>>();
    const on = (eventName: string, handler: Listener): (() => void) => {
        const bucket = listeners.get(eventName) ?? new Set();
        bucket.add(handler);
        listeners.set(eventName, bucket);
        return () => bucket.delete(handler);
    };
    const emit = (eventName: string, payload?: unknown): void => {
        for (const handler of listeners.get(eventName) ?? []) {
            handler(payload);
        }
    };

    return {
        state,
        events: { on, off: () => {}, emit },
        mutateState: ((key: string, updater: unknown) => {
            const record = state as unknown as Record<string, unknown>;
            const current = record[key];
            record[key] = typeof updater === 'function' ? (updater as (value: unknown) => unknown)(current) : updater;
            return record[key] as never;
        }) as DashboardCore['mutateState'],
        getStateSlice: () => state as never,
        registerFetcher: vi.fn(),
        getFetcher: () => null,
        warnSoftFailure: (_context: unknown, error: unknown) => {
            throw error instanceof Error ? error : new Error(String(error));
        },
        showApiError: vi.fn(),
        showSpsaDetailStreamError: vi.fn(),
        showNotice: vi.fn(),
        notifyDashboardServerStopped: vi.fn(),
        setDisplay: vi.fn(),
        getApiBase: () => '/api',
        updateElement: vi.fn(),
    } as DashboardCore;
}

describe('hydrateSpsaSummaryLiveView', () => {
    it('reconciles a stale SSE snapshot with the REST summary after reconnect', () => {
        const core = createMockCore();
        const store = getLiveViewSnapshotStore(core);
        store.hydrateFromPayload({
            version: 1,
            mode: 'spsa',
            progress: {
                kind: 'updates',
                unit_label: 'updates',
                completed: 9,
                total: 10,
                is_final: false,
            },
        });

        hydrateSpsaSummaryLiveView(core, {
            wins: 0,
            losses: 0,
            draws: 0,
            live_view: {
                version: 1,
                mode: 'spsa',
                progress: {
                    kind: 'updates',
                    unit_label: 'updates',
                    completed: 10,
                    total: 10,
                    is_final: true,
                },
            },
        } satisfies SpsaSummaryResponse);

        expect(store.getSnapshot()?.progress).toMatchObject({
            completed: 10,
            total: 10,
            is_final: true,
        });
    });
});
