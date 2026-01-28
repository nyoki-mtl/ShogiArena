import { createEmptyWorkerSnapshot } from '@/modules/live/utils';
import type { LiveBoardAdapter, LiveCardId, LiveCardState, WorkerRuntimeState } from '@/modules/live/types';
import type { DashboardCoreState } from '@/types/dashboard';
import type { WorkerSnapshotRecord } from './types';

function ensureCardList(state: DashboardCoreState): LiveCardState[] {
    const cardsRaw = state.cards;
    if (Array.isArray(cardsRaw)) {
        return cardsRaw as LiveCardState[];
    }
    const empty: LiveCardState[] = [];
    state.cards = empty;
    return empty;
}

export function getCardList(state: DashboardCoreState): LiveCardState[] {
    return ensureCardList(state);
}

export function setCardList(state: DashboardCoreState, next: LiveCardState[]): void {
    state.cards = next;
}

export function pushCard(state: DashboardCoreState, cardState: LiveCardState): void {
    ensureCardList(state).push(cardState);
}

export function findCardById(state: DashboardCoreState, cardId: LiveCardId): LiveCardState | undefined {
    return ensureCardList(state).find((card) => card && card.id === cardId);
}

export function findCardBySource(state: DashboardCoreState, source: string): LiveCardState | undefined {
    return ensureCardList(state).find((card) => card && card.source === source);
}

export function parseLiveCardId(raw: string): LiveCardId {
    const trimmed = raw.trim();
    if (!trimmed) return raw;
    const asNumber = Number(trimmed);
    return Number.isFinite(asNumber) && String(asNumber) === trimmed ? asNumber : trimmed;
}

export function toNumber(value: unknown): number {
    if (typeof value === 'number') {
        return Number.isFinite(value) ? value : 0;
    }
    if (typeof value === 'string') {
        const trimmed = value.trim();
        if (!trimmed) return 0;
        const parsed = Number(trimmed);
        return Number.isFinite(parsed) ? parsed : 0;
    }
    if (typeof value === 'bigint') {
        return Number(value);
    }
    return 0;
}

export function createEmptySnapshot(): WorkerSnapshotRecord {
    return { ...createEmptyWorkerSnapshot() } as WorkerSnapshotRecord;
}

export function ensureWorkerSnapshot(snapshot: WorkerSnapshotRecord | undefined): WorkerSnapshotRecord {
    return snapshot && typeof snapshot === 'object' ? snapshot : createEmptySnapshot();
}

function ensureWorkersMap(state: DashboardCoreState): Map<number, WorkerRuntimeState> {
    if (!(state.workers instanceof Map)) {
        state.workers = new Map();
    }
    return state.workers as Map<number, WorkerRuntimeState>;
}

export function getWorkerStateEntry(state: DashboardCoreState, workerIdx: number): WorkerRuntimeState {
    const workers = ensureWorkersMap(state);
    const existing = workers.get(workerIdx);
    if (existing && typeof existing === 'object') {
        return existing;
    }
    const created: WorkerRuntimeState = {};
    workers.set(workerIdx, created);
    return created;
}

export function peekWorkerState(state: DashboardCoreState, workerIdx: number): WorkerRuntimeState | undefined {
    if (!(state.workers instanceof Map)) return undefined;
    const entry = state.workers.get(workerIdx);
    if (entry && typeof entry === 'object') {
        return entry as WorkerRuntimeState;
    }
    return undefined;
}

export function setWorkerStateEntry(state: DashboardCoreState, workerIdx: number, value: WorkerRuntimeState): void {
    const workers = ensureWorkersMap(state);
    workers.set(workerIdx, value);
}

export function assignWorkerState(
    state: DashboardCoreState,
    workerIdx: number,
    patch: Partial<WorkerRuntimeState>,
): WorkerRuntimeState {
    const current = getWorkerStateEntry(state, workerIdx);
    const next = { ...current, ...patch } satisfies WorkerRuntimeState;
    setWorkerStateEntry(state, workerIdx, next);
    return next;
}

function ensureBoardAdapterMap(state: DashboardCoreState): Map<LiveCardId, LiveBoardAdapter> {
    if (!(state.boardAdapters instanceof Map)) {
        state.boardAdapters = new Map();
    }
    return state.boardAdapters as Map<LiveCardId, LiveBoardAdapter>;
}

export function setBoardAdapter(state: DashboardCoreState, cardId: LiveCardId, adapter: LiveBoardAdapter): void {
    const adapters = ensureBoardAdapterMap(state);
    adapters.set(cardId, adapter);
}

export function getBoardAdapter(state: DashboardCoreState, cardId: LiveCardId): LiveBoardAdapter | undefined {
    const adapters = ensureBoardAdapterMap(state);
    return adapters.get(cardId);
}

export function deleteBoardAdapter(state: DashboardCoreState, cardId: LiveCardId): LiveBoardAdapter | undefined {
    const adapters = ensureBoardAdapterMap(state);
    const existing = adapters.get(cardId);
    adapters.delete(cardId);
    return existing;
}

export function listBoardAdapterIds(state: DashboardCoreState): LiveCardId[] {
    const adapters = ensureBoardAdapterMap(state);
    return Array.from(adapters.keys());
}
