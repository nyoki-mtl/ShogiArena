import { createCardSync } from './card-sync';
import { normalizeWorkerSnapshotUpdate } from './normalizers';
import { createWorkerUpdatePipeline } from './worker-update-pipeline';
import type { LiveUpdatesContext, WorkerSnapshotUpdate } from '@/modules/live/types/updates';
import { createWorkerClock } from './worker-clock';
import { createWorkerCatchup } from './worker-catchup';

interface WorkerHandlersDeps {
    ctx: LiveUpdatesContext;
    safeClone: <T>(value: T) => T;
    emitEvent: (eventName: string, payload: unknown) => void;
}

export function createWorkerUpdateHandlers({ ctx, safeClone, emitEvent }: WorkerHandlersDeps) {
    const { state, cards, warnSoftFailure, normalizeSFEN } = ctx;

    const { applyClockSnapshot, applyClockIncrement } = createWorkerClock({ state });
    const { handleCatchupIfNeeded } = createWorkerCatchup({ state, cards, warnSoftFailure, normalizeSFEN });
    const lastUpdateWallClock = new Map<number, number>();
    const lastMoveUpdateWallClock = new Map<number, number>();
    const cardSync = createCardSync({ cards, state, warnSoftFailure });

    const { applyWorkerUpdate } = createWorkerUpdatePipeline({
        ctx,
        safeClone,
        emitEvent,
        applyClockSnapshot,
        applyClockIncrement,
        handleCatchupIfNeeded,
        cardSync,
        lastUpdateWallClock,
        lastMoveUpdateWallClock,
    });

    function onWorkerUpdate(worker_idx: number, payload: WorkerSnapshotUpdate | null | undefined): void {
        const normalized = normalizeWorkerSnapshotUpdate(payload);
        if (!normalized) {
            // bootstrapWorkers (REST) can legitimately return null when the worker has not started yet.
            // Ignore null updates to avoid crashing the dashboard initialization path.
            return;
        }
        applyWorkerUpdate(worker_idx, normalized);
    }

    return {
        applyClockSnapshot,
        applyClockIncrement,
        onWorkerUpdate,
    } as const;
}
