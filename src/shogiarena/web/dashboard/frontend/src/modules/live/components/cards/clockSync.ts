import type { LiveCardState } from '@/modules/live/types';
import type { WorkerSnapshotRecord } from './types';

interface ClockSyncDeps {
    updateStaticClocksForCard: (cardId: number | string, data: WorkerSnapshotRecord) => void;
}

export function createClockSync({ updateStaticClocksForCard }: ClockSyncDeps) {
    return function syncClocks(cardState: LiveCardState, data: WorkerSnapshotRecord): void {
        if (cardState.source.startsWith('db-game:')) {
            updateStaticClocksForCard(cardState.id, data);
            return;
        }

        if (cardState.source.startsWith('worker-latest:')) {
            const maxPly = Array.isArray(data.moves) ? data.moves.length : 0;
            const liveDisplayPly = Math.min(Number(data.currentPly || 0), maxPly);
            const hasTerminal = typeof data.result_code === 'number' && Number.isFinite(data.result_code);
            const atTerminal = hasTerminal && Number(cardState.viewPly || 0) === maxPly + 1;
            const isLiveView =
                Boolean(cardState.autoSync) && Number(cardState.viewPly || 0) === liveDisplayPly && !atTerminal;
            if (!isLiveView) {
                updateStaticClocksForCard(cardState.id, data);
            }
        }
    };
}
