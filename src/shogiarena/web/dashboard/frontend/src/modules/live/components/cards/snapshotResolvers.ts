import { createEmptySnapshot } from './state';
import type { WorkerSnapshotRecord } from './types';
import { recordLiveDiagnosticsMetric } from '@/modules/live/utils/liveNamespace';

const lastBootstrapMsByWorker = new Map<number, number>();

export interface WorkerSnapshotResolverDeps {
    getCachedWorkerSnapshot: (workerIdx: number) => WorkerSnapshotRecord | null;
    fetchAndCacheWorkerSnapshot: (workerIdx: number) => Promise<WorkerSnapshotRecord | null>;
    isMergeWorkerActive?: () => boolean;
}

export async function resolveSnapshotForWorkerCard(
    workerIdx: number,
    deps: WorkerSnapshotResolverDeps,
    options: { forceBootstrap?: boolean } = {},
): Promise<WorkerSnapshotRecord | null> {
    const { getCachedWorkerSnapshot, fetchAndCacheWorkerSnapshot } = deps;
    const forceBootstrap = options.forceBootstrap === true;
    const mergeWorkerActive = deps.isMergeWorkerActive?.() === true;

    let data = getCachedWorkerSnapshot(workerIdx);

    // In Merge Worker mode, WS catch-up snapshots are the primary recovery path.
    // Avoid O(ply) prefix scans per render when we won't trigger REST bootstrap anyway.
    const shouldCheckMovePrefix = !mergeWorkerActive || forceBootstrap;

    const now =
        typeof performance !== 'undefined' && typeof performance.now === 'function' ? performance.now() : Date.now();
    // Throttle REST bootstraps to avoid request storms if the snapshot cannot be repaired
    // (e.g. stale /api/worker responses in debug flood mode).

    const hasMissingPrefix = (() => {
        if (!shouldCheckMovePrefix) return false;
        if (!data || !Array.isArray(data.moves) || typeof data.currentPly !== 'number') return true;
        const upto = Math.max(0, data.currentPly - 1);
        for (let i = 0; i < upto; i++) {
            if (data.moves[i] == null || data.moves[i] === '') return true;
        }
        return false;
    })();

    const needBootstrap =
        forceBootstrap ||
        !data ||
        !Array.isArray(data.moves) ||
        // In Merge Worker mode, tolerate transient ply/moves mismatches and rely on WS snapshots.
        (!mergeWorkerActive && typeof data.currentPly === 'number' && data.currentPly > (data.moves?.length || 0)) ||
        !data.initial_sfen ||
        (!mergeWorkerActive && hasMissingPrefix);

    if (needBootstrap) {
        const currentPly =
            typeof data?.currentPly === 'number' && Number.isFinite(data.currentPly) ? data.currentPly : null;
        const movesLen = Array.isArray(data?.moves) ? data.moves.length : null;
        const reasonCode = forceBootstrap
            ? 1
            : !data
              ? 2
              : !Array.isArray(data.moves)
                ? 3
                : typeof currentPly === 'number' && typeof movesLen === 'number' && currentPly > movesLen
                  ? 4
                  : !data.initial_sfen
                    ? 5
                    : hasMissingPrefix
                      ? 6
                      : 0;
        recordLiveDiagnosticsMetric('live.cards.worker_snapshot.bootstrap', {
            triggered: 1,
            worker: workerIdx,
            reason_code: reasonCode,
            current_ply: typeof currentPly === 'number' ? currentPly : -1,
            moves_len: typeof movesLen === 'number' ? movesLen : -1,
            missing_prefix: hasMissingPrefix ? 1 : 0,
            merge_worker_active: mergeWorkerActive ? 1 : 0,
            prefix_check_skipped: shouldCheckMovePrefix ? 0 : 1,
        });

        if (mergeWorkerActive && !forceBootstrap) {
            // In Merge Worker mode, WS catch-up snapshots are the primary recovery path.
            // Avoid hammering `/api/worker/{idx}` on every render when the client briefly has
            // an incomplete prefix (e.g. during catch-up or when diffs are still buffered).
            recordLiveDiagnosticsMetric('live.cards.worker_snapshot.bootstrap', { cacheHit: 1, worker: workerIdx });
            return data ?? createEmptySnapshot();
        }

        const last = lastBootstrapMsByWorker.get(workerIdx);
        const throttleMs = forceBootstrap ? 0 : 1000;
        if (throttleMs > 0 && typeof last === 'number' && now - last < throttleMs) {
            recordLiveDiagnosticsMetric('live.cards.worker_snapshot.bootstrap', { cacheHit: 1, worker: workerIdx });
        } else {
            lastBootstrapMsByWorker.set(workerIdx, now);
            const snap = await fetchAndCacheWorkerSnapshot(workerIdx);
            if (snap) {
                data = snap;
            }
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
