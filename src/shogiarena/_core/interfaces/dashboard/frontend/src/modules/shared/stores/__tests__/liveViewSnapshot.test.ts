import { describe, expect, it, vi } from 'vitest';

import type { DashboardCore } from '@/types/dashboard';
import { getLiveViewSnapshotStore, LIVE_VIEW_UPDATE_EVENT } from '../liveViewSnapshot';

type Listener = (payload?: unknown) => void;

function createMockCore(): DashboardCore {
    const state = {
        liveViewSnapshot: null,
    } as DashboardCore['state'];

    const listeners = new Map<string, Set<Listener>>();

    const on = (eventName: string, handler: Listener): (() => void) => {
        const bucket = listeners.get(eventName) ?? new Set();
        bucket.add(handler);
        listeners.set(eventName, bucket);
        return () => {
            bucket.delete(handler);
        };
    };

    const emit = (eventName: string, payload?: unknown): void => {
        const bucket = listeners.get(eventName);
        if (!bucket) return;
        for (const handler of bucket) {
            handler(payload);
        }
    };

    return {
        state,
        events: {
            on,
            off: () => {},
            emit,
        },
        mutateState: ((key: string, updater: unknown) => {
            const stateRecord = state as unknown as { [key: string]: unknown };
            const current = stateRecord[key];
            const next = typeof updater === 'function' ? (updater as (value: unknown) => unknown)(current) : updater;
            stateRecord[key] = next;
            return next as never;
        }) as DashboardCore['mutateState'],
        getStateSlice: () => state as never,
        registerFetcher: vi.fn(),
        getFetcher: () => null,
        warnSoftFailure: (context: unknown, error: unknown) => {
            throw error instanceof Error ? error : new Error(String(context));
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

describe('getLiveViewSnapshotStore', () => {
    it('hydrates from raw payload and dispatches update events', () => {
        const core = createMockCore();
        const store = getLiveViewSnapshotStore(core);
        const listener = vi.fn();
        store.subscribe(listener);

        const snapshot = store.hydrateFromPayload(
            {
                version: 1,
                mode: 'spsa',
                progress: {
                    kind: 'updates',
                    unitLabel: 'updates',
                    completed: 4,
                    total: 16,
                },
            },
            'testPayload',
        );

        expect(snapshot).not.toBeNull();
        expect(core.state.liveViewSnapshot).not.toBeNull();
        expect(listener).toHaveBeenCalledTimes(1);
        expect(listener.mock.calls[0][0]).toMatchObject({ mode: 'spsa' });
    });

    it('reflects SSE-driven updates from DashboardCore events', () => {
        const core = createMockCore();
        const store = getLiveViewSnapshotStore(core);
        const listener = vi.fn();
        store.subscribe(listener);

        core.events.emit(LIVE_VIEW_UPDATE_EVENT, {
            version: 1,
            mode: 'tournament',
            progress: null,
        });

        expect(store.getSnapshot()).toMatchObject({ mode: 'tournament' });
        expect(listener).toHaveBeenCalledTimes(1);
    });
});
