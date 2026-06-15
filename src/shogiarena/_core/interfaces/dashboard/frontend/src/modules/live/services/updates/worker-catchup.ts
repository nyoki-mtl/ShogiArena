import type { LiveUpdatesContext } from '@/modules/live/types/updates';
import { getWorkerSnapshotRecord, getWorkerState, setWorkerState } from '@/modules/live/state/updates';
import { syncClockToTurnBoundary } from '@/modules/live/utils/clock-sync';

const CATCHUP_PLY_THRESHOLD = 12;
const CATCHUP_THROTTLE_MS = 1500;

interface WorkerCatchupDeps {
    state: LiveUpdatesContext['state'];
    cards: LiveUpdatesContext['cards'];
    warnSoftFailure: LiveUpdatesContext['warnSoftFailure'];
    normalizeSFEN: LiveUpdatesContext['normalizeSFEN'];
}

function isLiveCardState(value: unknown): value is { id: string | number; source: string } {
    if (!value || typeof value !== 'object') return false;
    const candidate = value as { id?: unknown; source?: unknown };
    const hasId = typeof candidate.id === 'string' || typeof candidate.id === 'number';
    return hasId && typeof candidate.source === 'string';
}

export function createWorkerCatchup({ state, cards, warnSoftFailure, normalizeSFEN }: WorkerCatchupDeps) {
    const pendingCatchup = new Set<number>();

    async function requestWorkerCatchup(worker_idx: number, _meta: Record<string, unknown> = {}): Promise<void> {
        if (pendingCatchup.has(worker_idx)) {
            return;
        }
        if (typeof cards.updateCardData !== 'function') {
            return;
        }
        const cardList = Array.isArray(state.cards) ? (state.cards as unknown[]) : [];
        const workerCards: { id: string | number; source: string }[] = [];
        for (const entry of cardList) {
            if (isLiveCardState(entry) && entry.source === `worker-latest:${worker_idx}`) {
                workerCards.push(entry);
            }
        }
        if (!workerCards.length) {
            return;
        }
        pendingCatchup.add(worker_idx);
        try {
            await Promise.all(
                workerCards.map(async (card) => {
                    try {
                        const result = cards.updateCardData?.(card);
                        if (result && typeof (result as Promise<unknown>).then === 'function') {
                            await result;
                        }
                    } catch (error) {
                        warnSoftFailure('Failed to refresh worker snapshot during catch-up', error);
                    }
                }),
            );
            const refreshedSnapshot = getWorkerSnapshotRecord(state, worker_idx);
            const refreshedPly =
                typeof refreshedSnapshot.current_ply === 'number' ? refreshedSnapshot.current_ply : undefined;
            if (refreshedPly !== undefined) {
                const refreshedState = getWorkerState(state, worker_idx);
                refreshedState.lastAppliedPly = refreshedPly;
                refreshedState.lastAppliedAtMs = Date.now();
                syncClockToTurnBoundary({
                    ws: refreshedState,
                    current_ply: refreshedPly,
                    initialSfen: refreshedSnapshot.initial_sfen ?? null,
                    normalizeSFEN,
                    nowMs: refreshedState.lastAppliedAtMs,
                });
                setWorkerState(state, worker_idx, refreshedState);
            }
        } finally {
            pendingCatchup.delete(worker_idx);
        }
    }

    function handleCatchupIfNeeded(worker_idx: number, incomingPly: number | null, previousAppliedPly: number): void {
        const now = Date.now();
        if (incomingPly === null) return;
        if (incomingPly <= previousAppliedPly) return;
        if (incomingPly - previousAppliedPly < CATCHUP_PLY_THRESHOLD) return;

        const workerState = getWorkerState(state, worker_idx);
        const lastCatchupAt =
            typeof workerState.lastCatchupRequestAt === 'number' ? workerState.lastCatchupRequestAt : 0;
        if (now - lastCatchupAt < CATCHUP_THROTTLE_MS) {
            return;
        }
        workerState.lastCatchupRequestAt = now;
        setWorkerState(state, worker_idx, workerState);
        void requestWorkerCatchup(worker_idx, {
            reason: 'ply_gap',
            deltaPly: incomingPly - previousAppliedPly,
            previousAppliedPly,
            incomingPly,
        });
    }

    return {
        handleCatchupIfNeeded,
    } as const;
}
