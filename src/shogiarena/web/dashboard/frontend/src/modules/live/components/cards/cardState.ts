import type { LiveCardState } from '@/modules/live/types';
import type { WorkerSnapshotRecord } from './types';

export function detectNewGame(
    cardState: LiveCardState,
    snapshot: WorkerSnapshotRecord,
    stableGameKey: string | null,
): boolean {
    const previousGameKey = cardState.lastGameId ? String(cardState.lastGameId) : null;
    const moveCount = Array.isArray(snapshot.moves) ? snapshot.moves.length : 0;
    const currentPlyValue = typeof snapshot.currentPly === 'number' ? snapshot.currentPly : 0;

    if (previousGameKey && stableGameKey && previousGameKey !== stableGameKey) {
        return true;
    }

    if (previousGameKey && previousGameKey !== (stableGameKey ?? 'startpos')) {
        return true;
    }

    const previousViewPly = Number(cardState.viewPly || 0);
    return previousViewPly > 0 && moveCount === 0 && currentPlyValue <= 1;
}

export function computeInitialViewPly(
    cardState: LiveCardState,
    snapshot: WorkerSnapshotRecord,
    computeAutoViewPly: (data: WorkerSnapshotRecord, includeTerminal?: boolean) => number,
): number {
    const hasTerminal = typeof snapshot.result_code === 'number' && Number.isFinite(snapshot.result_code);
    const includeTerminal = Boolean(cardState.autoSync) || hasTerminal;
    return computeAutoViewPly(snapshot, includeTerminal);
}

export function initializeViewPlyIfNeeded(
    cardState: LiveCardState,
    snapshot: WorkerSnapshotRecord,
    computeAutoViewPly: (data: WorkerSnapshotRecord, includeTerminal?: boolean) => number,
): boolean {
    if (cardState.viewPly !== undefined) return false;
    cardState.viewPly = computeInitialViewPly(cardState, snapshot, computeAutoViewPly);
    return true;
}
