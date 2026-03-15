import type { LiveCardState } from '@/modules/live/types';
import { hasTerminalResult } from '@/modules/live/utils';
import type { DashboardCoreState } from '@/types/dashboard';
import type { WorkerSnapshotRecord } from './types';
import { assignWorkerState } from './state';

export function resolveCardView(
    cardState: LiveCardState,
    snapshot: { data: WorkerSnapshotRecord; maxPly: number; liveDisplayPly: number },
): {
    viewPly: number;
    terminal: boolean;
    isLiveView: boolean;
} {
    const viewPly = Number(cardState.viewPly || 0);
    const hasTerminal = hasTerminalResult(snapshot.data);
    const terminal = hasTerminal && viewPly === snapshot.maxPly + 1;
    const isLiveView = Boolean(cardState.autoSync) && viewPly === snapshot.liveDisplayPly && !terminal;
    return { viewPly, terminal, isLiveView };
}

export function computeAutoViewPly(data: unknown, includeTerminal = false): number {
    if (!data) return 0;
    const snapshot = data as WorkerSnapshotRecord;
    const moves = Array.isArray(snapshot.moves) ? snapshot.moves : [];
    let maxPly = 0;
    for (let i = 0; i < moves.length; i += 1) {
        const mv = String(moves[i] ?? '').trim();
        if (!mv) break;
        maxPly += 1;
    }
    const hasTerminal = hasTerminalResult(snapshot);
    const maxView = maxPly + (hasTerminal ? 1 : 0);
    const raw =
        typeof snapshot.currentDisplayPly === 'number'
            ? snapshot.currentDisplayPly
            : typeof snapshot.current_ply === 'number'
              ? snapshot.current_ply
              : maxPly;
    const clamped = Math.min(Math.max(0, raw), maxView);
    if (includeTerminal && hasTerminal) {
        return maxView;
    }
    return clamped;
}

export function syncWorkerViewFromCard(state: DashboardCoreState, cardState: LiveCardState): void {
    if (!cardState || typeof cardState.source !== 'string') return;
    if (!cardState.source.startsWith('worker-latest:')) return;
    const workerIdx = Number(cardState.source.split(':')[1]);
    if (Number.isNaN(workerIdx)) return;
    assignWorkerState(state, workerIdx, { viewPly: Number(cardState.viewPly || 0) });
}
