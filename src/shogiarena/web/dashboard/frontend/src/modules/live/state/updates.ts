import type { DashboardCore } from '@/types/dashboard';
import { createEmptyWorkerSnapshot } from '@/modules/live/utils';
import type { WorkerRuntimeState, WorkerSnapshotRecord } from '@/modules/live/types';

function ensureWorkersMap(state: DashboardCore['state']): Map<number, WorkerRuntimeState> {
    if (!(state.workers instanceof Map)) {
        state.workers = new Map();
    }
    return state.workers as unknown as Map<number, WorkerRuntimeState>;
}

export function getWorkerState(state: DashboardCore['state'], workerIdx: number): WorkerRuntimeState {
    const workers = ensureWorkersMap(state);
    const existing = workers.get(workerIdx);
    if (existing && typeof existing === 'object') {
        return existing;
    }
    const created: WorkerRuntimeState = {};
    workers.set(workerIdx, created);
    return created;
}

export function setWorkerState(state: DashboardCore['state'], workerIdx: number, value: WorkerRuntimeState): void {
    ensureWorkersMap(state).set(workerIdx, value);
}

function ensureSnapshotMap(state: DashboardCore['state']): Map<number, WorkerSnapshotRecord> {
    if (!(state.workerSnapshots instanceof Map)) {
        state.workerSnapshots = new Map();
    }
    return state.workerSnapshots as unknown as Map<number, WorkerSnapshotRecord>;
}

export function getWorkerSnapshotRecord(state: DashboardCore['state'], workerIdx: number): WorkerSnapshotRecord {
    const snapshots = ensureSnapshotMap(state);
    const existing = snapshots.get(workerIdx);
    if (existing && typeof existing === 'object') {
        return existing;
    }
    const created = createEmptyWorkerSnapshot() as WorkerSnapshotRecord;
    snapshots.set(workerIdx, created);
    return created;
}

export function setWorkerSnapshotRecord(
    state: DashboardCore['state'],
    workerIdx: number,
    snapshot: WorkerSnapshotRecord,
): void {
    ensureSnapshotMap(state).set(workerIdx, snapshot);
}

export function peekWorkerSnapshotRecord(
    state: DashboardCore['state'],
    workerIdx: number,
): WorkerSnapshotRecord | undefined {
    if (!(state.workerSnapshots instanceof Map)) {
        return undefined;
    }
    const value = state.workerSnapshots.get(workerIdx);
    if (value && typeof value === 'object') {
        return value as WorkerSnapshotRecord;
    }
    return undefined;
}
