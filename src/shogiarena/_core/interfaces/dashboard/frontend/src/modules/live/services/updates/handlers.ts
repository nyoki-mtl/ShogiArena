import type {
    ClockIncrementPayload,
    ClockSnapshotPayload,
    LiveUpdatesContext,
    WorkerSnapshotUpdate,
    SseSummaryPayload,
} from '@/modules/live/types/updates';
import { createWorkerUpdateHandlers } from './workerUpdates';
import { createSpsaProgressHandler } from './spsaProgress';

export interface LiveUpdateHandlers {
    emitEvent: (eventName: string, payload: unknown) => void;
    applyClockSnapshot: (workerIdx: number, payload: ClockSnapshotPayload | null | undefined) => void;
    applyClockIncrement: (workerIdx: number, payload: ClockIncrementPayload | null | undefined) => void;
    onWorkerUpdate: (worker_idx: number, payload: WorkerSnapshotUpdate | null | undefined) => void;
    onSummaryUpdate: (payload: SseSummaryPayload | null | undefined) => void;
}

export function createLiveUpdateHandlers(ctx: LiveUpdatesContext, safeClone: <T>(value: T) => T): LiveUpdateHandlers {
    const { events } = ctx;

    function emitEvent(eventName: string, payload: unknown): void {
        if (!events || typeof events.emit !== 'function') {
            return;
        }
        events.emit(eventName, payload);
    }

    const workerHandlers = createWorkerUpdateHandlers({ ctx, safeClone, emitEvent });
    const spsaHandlers = createSpsaProgressHandler({ ctx, safeClone, emitEvent });
    return {
        emitEvent,
        applyClockSnapshot: workerHandlers.applyClockSnapshot,
        applyClockIncrement: workerHandlers.applyClockIncrement,
        onWorkerUpdate: workerHandlers.onWorkerUpdate,
        onSummaryUpdate: spsaHandlers.onSummaryUpdate,
    };
}
