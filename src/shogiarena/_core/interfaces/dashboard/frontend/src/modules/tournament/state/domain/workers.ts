import type { WorkerDomainState } from '@/modules/tournament/types';

export function createWorkerDomainState(workerCount: number): WorkerDomainState {
    const normalizedCount = Number.isFinite(workerCount) && workerCount >= 0 ? Math.trunc(workerCount) : 0;

    return {
        cards: [],
        workers: new Map(),
        boardAdapters: new Map(),
        numWorkers: normalizedCount,
        maxLiveBoards: 6,
        nextCardId: 0,
        gameDataCache: new Map(),
        gamesSignature: '',
        gamesSignatureByCard: new Map(),
        offlineNotified: false,
        engineFinalRatings: new Map(),
        highlightEngine: null,
        prevRatings: new Map(),
        ratingDeltas: new Map(),
        ratingDeltaTimerId: null,
        pendingDeltaHint: new Map(),
        pentaCache: new Map(),
    };
}
