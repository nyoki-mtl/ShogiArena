import type { DashboardCore } from '@/types/dashboard';
import type { LiveCardState } from '@/modules/live/types';
import type { WorkerSnapshotRecord } from './types';
import type { WorkerViewModelMessage } from '@/modules/live/services/updates/workerBridge';
import { shouldRunEngineClock } from '@/modules/live/utils/engineStatus';
import { getWorkerStateEntry } from './state';

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
    stopWorkerClockTimer: (workerIdx: number) => void;
    updateWorkerClockDisplay: (workerIdx: number) => void;
    showNotice?: DashboardCore['showNotice'];
    state: DashboardCore['state'];
    onVm?: (vm: WorkerViewModelMessage, receivedAt: number) => void;
    setMergeWorkerActive?: (active: boolean) => void;
    handleEngineLogEvent?: (payload: unknown) => void;
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
        stopWorkerClockTimer,
        updateWorkerClockDisplay,
        showNotice,
        state,
        onVm,
        setMergeWorkerActive,
        handleEngineLogEvent,
    } = deps;

    let sseDisconnectedNotified = false;
    const cleanups: Array<() => void> = [];
    let mergeWorkerActive = false;
    const parseSide = (value: unknown): 'black' | 'white' | null => {
        if (typeof value !== 'string') return null;
        const normalized = value.trim().toLowerCase();
        if (normalized === 'black' || normalized === 'white') return normalized;
        return null;
    };
    const inferIncrementSide = (clockData: Record<string, unknown>): 'black' | 'white' | null => {
        const direct = parseSide(clockData.side);
        if (direct) return direct;
        const active = parseSide(clockData.active);
        if (active === 'black') return 'white';
        if (active === 'white') return 'black';
        const preBlack = Number(clockData.pre_black_remain_ms);
        const preWhite = Number(clockData.pre_white_remain_ms);
        const postBlack = Number(clockData.black_remain_ms);
        const postWhite = Number(clockData.white_remain_ms);
        const hasPrePost =
            Number.isFinite(preBlack) &&
            Number.isFinite(preWhite) &&
            Number.isFinite(postBlack) &&
            Number.isFinite(postWhite);
        if (hasPrePost) {
            const deltaBlack = postBlack - preBlack;
            const deltaWhite = postWhite - preWhite;
            if (deltaBlack !== 0 && deltaWhite === 0) return 'black';
            if (deltaWhite !== 0 && deltaBlack === 0) return 'white';
        }
        return null;
    };

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
            const side = inferIncrementSide(clockData);
            if (!mergeWorkerActive) {
                flashIncrement(
                    idx,
                    (side ?? undefined) as string | undefined,
                    Number(inc ?? clockData.appliedIncrementMs ?? 0),
                );
                applyByoyomiFreezeCache(idx, clockData);
            }
        }

        updateTimeControlLabelsForWorker(idx, clockData);
        const workerState = getWorkerStateEntry(state, idx);
        const engineStatus = workerState.engineStatus;
        const allowClock = Boolean(engineStatus) && shouldRunEngineClock(engineStatus);
        if (allowClock) {
            startWorkerClockTimer(idx);
        } else {
            stopWorkerClockTimer(idx);
        }
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
    register('worker:snapshot', (payload: unknown) =>
        handleWorkerSnapshotEvent(payload as { workerIdx?: number | string; snapshot?: { data?: unknown } | null }),
    );
    register('live:merge-worker-enabled', () => {
        mergeWorkerActive = true;
        (state as unknown as { mergeWorkerActive?: boolean }).mergeWorkerActive = true;
        setMergeWorkerActive?.(true);
    });
    register('live:merge-worker-disabled', () => {
        mergeWorkerActive = false;
        (state as unknown as { mergeWorkerActive?: boolean }).mergeWorkerActive = false;
        setMergeWorkerActive?.(false);
    });
    if (handleEngineLogEvent) {
        register('live:engine-log', (payload: unknown) => handleEngineLogEvent(payload));
    }
    register('live:worker-vm', (vm: unknown) => {
        if (!vm || typeof vm !== 'object' || typeof (vm as { workerIdx?: unknown }).workerIdx !== 'number') return;
        const getNow = () =>
            typeof performance !== 'undefined' && typeof performance.now === 'function'
                ? performance.now()
                : Date.now();
        onVm?.(vm as WorkerViewModelMessage, getNow());
    });
    register('worker:clock', (payload: unknown) =>
        handleClockEvent(payload as { workerIdx?: number | string; clock?: unknown; kind?: string }),
    );

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
