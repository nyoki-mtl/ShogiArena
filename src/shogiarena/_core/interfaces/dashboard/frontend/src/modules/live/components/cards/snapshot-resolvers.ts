import { createEmptySnapshot } from './state';
import type { WorkerSnapshotRecord } from './types';
import { recordLiveDiagnosticsMetric } from '@/modules/live/utils/liveNamespace';

export interface WorkerSnapshotResolverDeps {
    getCachedWorkerSnapshot: (workerIdx: number) => WorkerSnapshotRecord | null;
    fetchAndCacheWorkerSnapshot: (workerIdx: number) => Promise<WorkerSnapshotRecord | null>;
}

export async function resolveSnapshotForWorkerCard(
    workerIdx: number,
    deps: WorkerSnapshotResolverDeps,
    options: { forceBootstrap?: boolean } = {},
): Promise<WorkerSnapshotRecord | null> {
    const { getCachedWorkerSnapshot, fetchAndCacheWorkerSnapshot } = deps;
    const forceBootstrap = options.forceBootstrap === true;

    let data = getCachedWorkerSnapshot(workerIdx);
    const shouldFetchBootstrap = forceBootstrap || !data;
    const hasBrokenCachedSnapshot = data != null && (!Array.isArray(data.moves) || !data.initial_sfen);
    const needBootstrap = shouldFetchBootstrap || hasBrokenCachedSnapshot;

    if (needBootstrap) {
        const current_ply =
            typeof data?.current_ply === 'number' && Number.isFinite(data.current_ply) ? data.current_ply : null;
        const movesLen = Array.isArray(data?.moves) ? data.moves.length : null;
        const reasonCode = forceBootstrap ? 1 : !data ? 2 : !Array.isArray(data.moves) ? 3 : !data.initial_sfen ? 5 : 0;
        recordLiveDiagnosticsMetric('live.cards.worker_snapshot.bootstrap', {
            triggered: 1,
            worker: workerIdx,
            reason_code: reasonCode,
            current_ply: typeof current_ply === 'number' ? current_ply : -1,
            moves_len: typeof movesLen === 'number' ? movesLen : -1,
        });

        if (!shouldFetchBootstrap) {
            // WS catch-up snapshots are the primary recovery path.
            // Return cached data or empty snapshot while waiting for WS to deliver full state.
            recordLiveDiagnosticsMetric('live.cards.worker_snapshot.bootstrap', { cacheHit: 1, worker: workerIdx });
            return data ?? createEmptySnapshot();
        }

        const snap = await fetchAndCacheWorkerSnapshot(workerIdx);
        if (snap) {
            data = snap;
        }
    }

    return data ?? createEmptySnapshot();
}

export async function resolveSnapshotForDbGameCard(
    gameId: string,
    getGameData: (gameId: string) => Promise<WorkerSnapshotRecord | null>,
): Promise<WorkerSnapshotRecord> {
    const data = await getGameData(gameId);
    return data ?? createEmptySnapshot();
}
