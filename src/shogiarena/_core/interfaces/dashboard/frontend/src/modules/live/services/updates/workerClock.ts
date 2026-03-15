import type { LiveUpdatesContext, ClockSnapshotPayload, ClockIncrementPayload } from '@/modules/live/types/updates';
import { getWorkerState, setWorkerState } from '@/modules/live/state/updates';
import {
    maybeApplyImmediateClockStart,
    queueClockCorrections,
    updateTimeControlState,
} from '@/modules/live/utils/clockSync';

interface WorkerClockDeps {
    state: LiveUpdatesContext['state'];
}

export function createWorkerClock({ state }: WorkerClockDeps) {
    function applyClockSnapshot(workerIdx: number, payload: ClockSnapshotPayload | null | undefined): void {
        if (!payload || typeof payload !== 'object') return;
        const ws = getWorkerState(state, workerIdx);
        const now = Date.now();
        queueClockCorrections(ws, payload, 'clock_start', now);
        updateTimeControlState(ws, payload.time_control_black, payload.time_control_white);
        maybeApplyImmediateClockStart(ws, payload, now);
        if (payload.time_control_black) ws.timeControlBlack = payload.time_control_black;
        if (payload.time_control_white) ws.timeControlWhite = payload.time_control_white;
        if (typeof payload.byoyomi_ms_black === 'number') ws.byoyomiMsBlack = payload.byoyomi_ms_black;
        if (typeof payload.byoyomi_ms_white === 'number') ws.byoyomiMsWhite = payload.byoyomi_ms_white;
        ws.blackFrozenByoText = null;
        ws.whiteFrozenByoText = null;
        setWorkerState(state, workerIdx, ws);
    }

    function applyClockIncrement(workerIdx: number, payload: ClockIncrementPayload | null | undefined): void {
        if (!payload || typeof payload !== 'object') return;
        const ws = getWorkerState(state, workerIdx);
        const now = Date.now();
        queueClockCorrections(ws, payload, 'clock_increment', now);
        const side = String(payload.side ?? '').toLowerCase();
        if (side === 'black') ws.blackFrozenByoText = null;
        if (side === 'white') ws.whiteFrozenByoText = null;
        setWorkerState(state, workerIdx, ws);
    }

    return {
        applyClockSnapshot,
        applyClockIncrement,
    } as const;
}
