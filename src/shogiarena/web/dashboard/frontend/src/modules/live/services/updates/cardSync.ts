import type { LiveCardState } from '@/modules/live/types';
import { getWorkerSnapshotRecord } from '@/modules/live/state/updates';
import type { DashboardCore } from '@/types/dashboard';

export type CardSyncDeps = {
    cards: {
        computeAutoViewPly: (snapshot: unknown, preferLatest?: boolean) => number;
        syncWorkerViewFromCard: (card: LiveCardState) => void;
    };
    state: DashboardCore['state'];
    warnSoftFailure: (context: string, error: unknown) => void;
};

export type CardSync = {
    scheduleCardUpdate: (workerIdx: number) => void;
};

export function createCardSync({ cards, state, warnSoftFailure }: CardSyncDeps): CardSync {
    const pendingCardUpdates = new Map<number, number>(); // worker_idx -> raf id
    const pendingWhileHidden = new Set<number>();

    const isMergeWorkerActive = () =>
        Boolean((state as unknown as { mergeWorkerActive?: boolean }).mergeWorkerActive === true);

    const isDocumentVisible = (): boolean =>
        typeof document === 'undefined' ||
        typeof document.visibilityState !== 'string' ||
        document.visibilityState === 'visible';

    function applyCardUpdate(workerIdx: number): void {
        if (isMergeWorkerActive()) {
            return;
        }
        const snapshot = getWorkerSnapshotRecord(state, workerIdx);
        const cardList = Array.isArray(state.cards) ? (state.cards as unknown[]) : [];
        for (const entry of cardList) {
            const card = entry as LiveCardState;
            if (!card || (card as unknown as { source?: string }).source !== `worker-latest:${workerIdx}`) continue;
            if (!card.autoSync) continue;
            try {
                const autoPly = cards.computeAutoViewPly(snapshot, true);
                card.viewPly = autoPly;
                cards.syncWorkerViewFromCard(card);
            } catch (error) {
                warnSoftFailure('Failed to sync worker card view', error);
            }
        }
    }

    function scheduleCardUpdate(workerIdx: number): void {
        if (isMergeWorkerActive()) {
            return; // Merge Worker 経路では VM 側で viewPly 同期済み
        }
        if (pendingCardUpdates.has(workerIdx)) {
            return; // Already scheduled
        }

        if (!isDocumentVisible()) {
            pendingWhileHidden.add(workerIdx);
            return;
        }

        if (typeof requestAnimationFrame !== 'function') {
            applyCardUpdate(workerIdx);
            return;
        }

        const rafId = requestAnimationFrame(() => {
            pendingCardUpdates.delete(workerIdx);
            applyCardUpdate(workerIdx);
        });

        pendingCardUpdates.set(workerIdx, rafId);
    }

    const flushOnVisibility = (): void => {
        if (!isDocumentVisible()) {
            return;
        }
        if (!pendingWhileHidden.size) {
            return;
        }
        const workers = Array.from(pendingWhileHidden);
        pendingWhileHidden.clear();
        for (const workerIdx of workers) {
            scheduleCardUpdate(workerIdx);
        }
    };

    if (typeof document !== 'undefined' && typeof document.addEventListener === 'function') {
        document.addEventListener('visibilitychange', flushOnVisibility);
    }

    return { scheduleCardUpdate };
}
