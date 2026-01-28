import type { LiveBoardAdapter, LiveCardState } from '@/modules/live/types';
import type { DashboardCoreState } from '@/types/dashboard';
import type { WorkerSnapshotRecord } from './types';
import { createBoardSync } from './boardSync';
import { createClockSync } from './clockSync';
import { updateCardDisplay } from './cardDisplay';
import { detectNewGame, initializeViewPlyIfNeeded, shouldBlockUpdateForManualView } from './cardState';
import { resolveSnapshotForDbGameCard, resolveSnapshotForWorkerCard } from './snapshotResolvers';

export interface UpdateCardDataOptions {
    forceBootstrap?: boolean;
}

export interface CardDataDeps {
    state: DashboardCoreState;
    normalizeSFEN: (sfen: string | null | undefined) => string;
    computeAutoViewPly: (data: WorkerSnapshotRecord, includeTerminal?: boolean) => number;
    syncWorkerViewFromCard: (cardState: LiveCardState) => void;
    getStableGameKey: (snapshot: WorkerSnapshotRecord) => string | null;
    fetchAndCacheWorkerSnapshot: (workerIdx: number) => Promise<WorkerSnapshotRecord | null>;
    getCachedWorkerSnapshot: (workerIdx: number) => WorkerSnapshotRecord | null;
    getGameData: (gameId: string) => Promise<WorkerSnapshotRecord | null>;
    updateElement: (id: string, value: unknown) => void;
    formatTimeControlShort: (tc: string | null | undefined) => string;
    updateMoveSummaryCard: (cardId: number | string, data: WorkerSnapshotRecord) => void;
    updateEvalChartCard: (cardId: number | string, data: WorkerSnapshotRecord) => void;
    updateStaticClocksForCard: (cardId: number | string, data: WorkerSnapshotRecord) => void;
    getBoardAdapter: (state: DashboardCoreState, cardId: number | string) => LiveBoardAdapter | null | undefined;
    warnSoftFailure: (context: string, error: unknown) => void;
    closeKifuPopover: () => void;
}

export function createUpdateCardData(deps: CardDataDeps) {
    const {
        state,
        normalizeSFEN,
        computeAutoViewPly,
        syncWorkerViewFromCard,
        getStableGameKey,
        fetchAndCacheWorkerSnapshot,
        getCachedWorkerSnapshot,
        getGameData,
        updateElement,
        formatTimeControlShort,
        updateMoveSummaryCard,
        updateEvalChartCard,
        updateStaticClocksForCard,
        getBoardAdapter,
        warnSoftFailure,
        closeKifuPopover,
    } = deps;

    const syncBoard = createBoardSync({
        normalizeSFEN,
        computeAutoViewPly,
        syncWorkerViewFromCard,
        warnSoftFailure,
        getBoardAdapter,
        state,
    });

    const syncClocks = createClockSync({ updateStaticClocksForCard });

    return async function updateCardData(cardState: LiveCardState, options: UpdateCardDataOptions = {}): Promise<void> {
        const forceBootstrap = options.forceBootstrap === true;
        let data: WorkerSnapshotRecord | null = null;
        let stableGameKey: string | null = null;
        let newGameDetected = false;
        const isWorkerCard = cardState.source.startsWith('worker-latest:');

        if (isWorkerCard) {
            const workerIdx = parseInt(cardState.source.split(':')[1], 10);
            const mergeWorkerActive = Boolean((state as unknown as { mergeWorkerActive?: boolean }).mergeWorkerActive);
            data = await resolveSnapshotForWorkerCard(
                workerIdx,
                {
                    getCachedWorkerSnapshot,
                    fetchAndCacheWorkerSnapshot,
                    isMergeWorkerActive: () => mergeWorkerActive,
                },
                { forceBootstrap },
            );
            stableGameKey = data ? getStableGameKey(data) : null;

            if (shouldBlockUpdateForManualView(cardState, stableGameKey)) {
                return;
            }

            newGameDetected = detectNewGame(cardState, data, stableGameKey);
            if (!cardState.lastGameId || newGameDetected) {
                cardState.lastGameId = stableGameKey ?? 'startpos';
            }
            if (newGameDetected && Number.isFinite(workerIdx)) {
                // Merge Worker is already driven by the WS gid-stream (diff + replay). Pulling `/api/worker/{idx}`
                // here can overwrite the WS-merged snapshot with a stale REST snapshot (server may not update it
                // per-move), causing label freezes and illegal move sequences. Keep REST bootstrap only for the
                // legacy non-worker path.
                if (!mergeWorkerActive) {
                    const fresh = await fetchAndCacheWorkerSnapshot(workerIdx);
                    if (fresh) {
                        data = fresh;
                        stableGameKey = getStableGameKey(fresh);
                        cardState.lastGameId = stableGameKey ?? 'startpos';
                    }
                }
                const hasTerminal = typeof data?.result_code === 'number' && Number.isFinite(data.result_code);
                const includeTerminal = Boolean(cardState.autoSync) || hasTerminal;
                cardState.viewPly = cardState.autoSync ? computeAutoViewPly(data, includeTerminal) : 0;
                syncWorkerViewFromCard(cardState);
            }
        } else if (cardState.source.startsWith('db-game:')) {
            const gameId = cardState.source.split(':')[1];
            data = await resolveSnapshotForDbGameCard(gameId, getGameData);
            stableGameKey = data ? getStableGameKey(data) : null;
        }

        if (!data) {
            return;
        }

        if (initializeViewPlyIfNeeded(cardState, data, computeAutoViewPly)) {
            syncWorkerViewFromCard(cardState);
        }

        updateCardDisplay(
            {
                state,
                updateElement,
                formatTimeControlShort,
                updateMoveSummaryCard,
                updateEvalChartCard,
                syncBoard,
                syncClocks,
                closeKifuPopover,
            },
            cardState,
            data,
            { newGameDetected },
        );

        // Update syncing overlay CSS classes based on isSyncing state.
        const cardEl = document.getElementById(`card-${cardState.id}`);
        if (cardEl) {
            const isSyncing = Boolean(cardState.isSyncing);
            const syncingStartedAt = cardState.syncingStartedAt;
            const isDelayed = isSyncing && typeof syncingStartedAt === 'number' && Date.now() - syncingStartedAt > 3000;
            cardEl.classList.toggle('worker-card--syncing', isSyncing);
            cardEl.classList.toggle('worker-card--sync-delayed', isDelayed);
        }
    };
}
