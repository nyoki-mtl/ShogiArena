import type { DashboardCore } from '@/types/dashboard';
import type { LiveCardState } from '@/modules/live/types';
import type { WorkerSnapshotRecord } from './types';
import type { WorkerViewModelMessage } from '@/modules/live/services/updates/workerBridge';

export interface EventHandlers {
    handleWorkerSnapshotEvent: (event: { workerIdx?: number | string; snapshot?: { data?: unknown } | null }) => void;
    handleClockEvent: (event: { workerIdx?: number | string; clock?: unknown; kind?: string }) => void;
    handleSseDisconnected: () => void;
    handleSseReconnected: () => void;
}

export interface EventDeps {
    events: DashboardCore['events'] | null | undefined;
    getCards?: () => LiveCardState[];
    freezeAllWorkerClocks: () => void;
    refreshCardsForWorker: (workerIdx: number) => void;
    cacheWorkerSnapshot: (workerIdx: number, data: WorkerSnapshotRecord | null | undefined) => void;
    ensureIncrementPlaceholders: (workerIdx: number) => void;
    flashIncrement: (workerIdx: number, side: string | undefined, appliedIncrementMs: number) => void;
    applyByoyomiFreezeCache: (workerIdx: number, clockData: Record<string, unknown>) => void;
    updateTimeControlLabelsForWorker: (workerIdx: number, clockData: Record<string, unknown>) => void;
    startWorkerClockTimer: (workerIdx: number) => void;
    updateWorkerClockDisplay: (workerIdx: number) => void;
    showNotice?: DashboardCore['showNotice'];
    state: DashboardCore['state'];
    onVm?: (vm: WorkerViewModelMessage, receivedAt: number) => void;
    setMergeWorkerActive?: (active: boolean) => void;
}

export interface EventSubscription {
    handlers: EventHandlers;
    unsubscribe: () => void;
}

export function createCardEventHandlers(deps: EventDeps): EventSubscription {
    const {
        events,
        freezeAllWorkerClocks,
        refreshCardsForWorker,
        cacheWorkerSnapshot,
        ensureIncrementPlaceholders,
        flashIncrement,
        applyByoyomiFreezeCache,
        updateTimeControlLabelsForWorker,
        startWorkerClockTimer,
        updateWorkerClockDisplay,
        showNotice,
        state,
        onVm,
        setMergeWorkerActive,
    } = deps;

    let sseDisconnectedNotified = false;
    const cleanups: Array<() => void> = [];
    let mergeWorkerActive = false;

    const register = (eventName: string, handler: (payload: unknown) => void): void => {
        if (!events) return;
        if (typeof events.on === 'function') {
            const off = events.on(eventName, handler);
            if (typeof off === 'function') {
                cleanups.push(off);
                return;
            }
        }
        if (typeof events.off === 'function') {
            cleanups.push(() => events.off?.(eventName, handler));
        }
    };

    const handleWorkerSnapshotEvent = (event: {
        workerIdx?: number | string;
        snapshot?: { data?: unknown } | null;
    }) => {
        if (!event || event.workerIdx == null) return;
        if (mergeWorkerActive) return; // Merge Worker 使用時は VM 経由で描画する
        const idx = Number(event.workerIdx);
        if (!Number.isFinite(idx)) return;
        const snapshotData = event.snapshot && typeof event.snapshot.data === 'object' ? event.snapshot.data : null;
        if (snapshotData) {
            cacheWorkerSnapshot(idx, snapshotData as WorkerSnapshotRecord);
        }
        refreshCardsForWorker(idx);
    };

    const handleClockEvent = (event: { workerIdx?: number | string; clock?: unknown; kind?: string }) => {
        if (!event || event.workerIdx == null) return;
        const idx = Number(event.workerIdx);
        if (!Number.isFinite(idx)) return;
        const clockData =
            event.clock && typeof event.clock === 'object' ? (event.clock as Record<string, unknown>) : {};

        if (event.kind === 'clock_start') {
            ensureIncrementPlaceholders(idx);
        }
        if (event.kind === 'clock_increment') {
            const inc = (clockData as { applied_increment_ms?: number; appliedIncrementMs?: number })
                .applied_increment_ms;
            flashIncrement(idx, clockData.side as string | undefined, Number(inc ?? clockData.appliedIncrementMs ?? 0));
            applyByoyomiFreezeCache(idx, clockData);
        }

        updateTimeControlLabelsForWorker(idx, clockData);
        startWorkerClockTimer(idx);
        updateWorkerClockDisplay(idx);
    };

    const handleSseDisconnected = (): void => {
        freezeAllWorkerClocks();
        if (state.offlineNotified) {
            return;
        }
        if (!sseDisconnectedNotified) {
            showNotice?.('ライブ更新ストリームが一時的に切断されました。再接続を試行します。', 'warn', {
                timeout: 5000,
            });
        }
        sseDisconnectedNotified = true;
    };

    const handleSseReconnected = (): void => {
        if (sseDisconnectedNotified && !state.offlineNotified) {
            showNotice?.('ライブ更新ストリームに再接続しました。', 'success', { timeout: 3000 });
        }
        sseDisconnectedNotified = false;
    };

    register('dashboard:offline', freezeAllWorkerClocks);
    register('live:sse-disconnected', handleSseDisconnected);
    register('live:sse-reconnected', handleSseReconnected);
    register('worker:snapshot', handleWorkerSnapshotEvent);
    register('live:merge-worker-enabled', () => {
        mergeWorkerActive = true;
        (state as unknown as { mergeWorkerActive?: boolean }).mergeWorkerActive = true;
        setMergeWorkerActive?.(true);
        console.debug?.('[Live] merge worker enabled');
    });
    register('live:merge-worker-disabled', () => {
        mergeWorkerActive = false;
        (state as unknown as { mergeWorkerActive?: boolean }).mergeWorkerActive = false;
        setMergeWorkerActive?.(false);
        console.debug?.('[Live] merge worker disabled');
    });
    register('live:worker-vm', (vm: unknown) => {
        if (!vm || typeof vm !== 'object' || typeof (vm as { workerIdx?: unknown }).workerIdx !== 'number') return;
        const getNow = () =>
            typeof performance !== 'undefined' && typeof performance.now === 'function'
                ? performance.now()
                : Date.now();
        onVm?.(vm as WorkerViewModelMessage, getNow());
    });
    register('worker:clock', handleClockEvent);

    return {
        handlers: {
            handleWorkerSnapshotEvent,
            handleClockEvent,
            handleSseDisconnected,
            handleSseReconnected,
        },
        unsubscribe: () => {
            for (const off of cleanups) {
                try {
                    off();
                } catch {
                    // ignore teardown errors
                }
            }
            cleanups.length = 0;
        },
    };
}
