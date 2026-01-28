import { createSafeClone } from '@/modules/live/services/updates/common';
import { normalizeLiveViewSnapshot } from '@/modules/live/services/updates/normalizers';
import type { LiveViewSnapshot } from '@/modules/live/types';
import type { DashboardCore } from '@/types/dashboard';

export type LiveViewSnapshotListener = (snapshot: LiveViewSnapshot | null) => void;

export interface LiveViewSnapshotStore {
    readonly getSnapshot: () => LiveViewSnapshot | null;
    readonly subscribe: (listener: LiveViewSnapshotListener) => () => void;
    readonly hydrateFromPayload: (payload: unknown, context?: string) => LiveViewSnapshot | null;
    readonly publishSnapshot: (snapshot: LiveViewSnapshot | null) => void;
}

export const LIVE_VIEW_UPDATE_EVENT = 'liveView:update';

const stores = new WeakMap<DashboardCore, LiveViewSnapshotStore>();

function cloneSnapshot(snapshot: LiveViewSnapshot | null, safeClone: <T>(value: T) => T): LiveViewSnapshot | null {
    return snapshot ? safeClone(snapshot) : null;
}

export function getLiveViewSnapshotStore(core: DashboardCore): LiveViewSnapshotStore {
    const existing = stores.get(core);
    if (existing) {
        return existing;
    }

    const safeClone = createSafeClone(core.warnSoftFailure);
    let current: LiveViewSnapshot | null = cloneSnapshot(core.state?.liveViewSnapshot ?? null, safeClone);
    const listeners = new Set<LiveViewSnapshotListener>();

    const notifyListeners = (): void => {
        for (const listener of listeners) {
            try {
                listener(cloneSnapshot(current, safeClone));
            } catch (error) {
                console.error('[LiveViewSnapshotStore] listener failed', error);
            }
        }
    };

    core.events?.on?.(LIVE_VIEW_UPDATE_EVENT, (payload?: LiveViewSnapshot | null) => {
        current = cloneSnapshot(payload ?? null, safeClone);
        notifyListeners();
    });

    const dispatchSnapshot = (snapshot: LiveViewSnapshot | null): void => {
        const stateSnapshot = cloneSnapshot(snapshot, safeClone);
        core.mutateState('liveViewSnapshot', () => stateSnapshot);
        const eventPayload = cloneSnapshot(snapshot, safeClone);
        core.events?.emit?.(LIVE_VIEW_UPDATE_EVENT, eventPayload);
    };

    const store: LiveViewSnapshotStore = {
        getSnapshot: () => cloneSnapshot(current, safeClone),
        subscribe: (listener: LiveViewSnapshotListener) => {
            listeners.add(listener);
            return () => {
                listeners.delete(listener);
            };
        },
        hydrateFromPayload: (payload: unknown, context = 'liveView') => {
            const normalized = normalizeLiveViewSnapshot(payload, context);
            if (!normalized) {
                return null;
            }
            dispatchSnapshot(normalized);
            return normalized;
        },
        publishSnapshot: (snapshot: LiveViewSnapshot | null) => {
            dispatchSnapshot(snapshot);
        },
    };

    stores.set(core, store);
    return store;
}
