import { requestJson } from '@/modules/shared/services/api';
import { normalizeWorkerSnapshotUpdate } from './normalizers';
import type { WorkerSnapshotUpdate } from '@/modules/live/types/updates';

interface BootstrapWorkersDeps {
    getApiBase: (path?: string) => string;
    onWorkerUpdate: (workerIdx: number, payload: WorkerSnapshotUpdate | null) => void;
}

/**
 * Fetch initial worker snapshots for the active cards.
 * - requestJson 経由で HTTP エラーや JSON パース失敗を即座に例外化。
 * - payload は normalizer で検証し、想定外フィールドは fail-fast。
 */
export async function bootstrapWorkers(activeWorkers: Iterable<number>, deps: BootstrapWorkersDeps): Promise<void> {
    const workerList = Array.from(activeWorkers);
    if (!workerList.length) {
        return;
    }

    const base = deps.getApiBase() || '';

    await Promise.all(
        workerList.map(async (workerIdx) => {
            const url = `${base}/api/worker/${workerIdx}`;
            const snapshot = await requestJson<WorkerSnapshotUpdate | null>(url, { credentials: 'same-origin' });
            if (!snapshot) {
                return;
            }
            const normalized = normalizeWorkerSnapshotUpdate(snapshot, `worker_snapshot[${workerIdx}]`);
            if (!normalized) {
                return;
            }
            deps.onWorkerUpdate(workerIdx, normalized);
        }),
    );
}
