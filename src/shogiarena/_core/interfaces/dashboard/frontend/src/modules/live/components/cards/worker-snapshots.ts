import {
    getWorkerSnapshotRecord,
    peekWorkerSnapshotRecord,
    setWorkerSnapshotRecord,
} from '@/modules/live/state/updates';
import type { LiveCardState } from '@/modules/live/types';
import { hasTerminalResult } from '@/modules/live/utils';
import type { DashboardCoreState } from '@/types/dashboard';
import type { RequestError } from '@/types/shared';
import { peekWorkerState } from './state';
import type { WorkerSnapshotRecord } from './types';

interface WorkerSnapshotDeps {
    state: DashboardCoreState;
    warnSoftFailure: (message: string, error: unknown) => void;
    showApiError: (
        message: string,
        info: { endpoint: string; status?: number; message?: string | null; payload?: unknown },
    ) => void;
    notifyDashboardServerStopped: () => void;
    getApiBase: () => string;
    requestJson: <T>(url: string) => Promise<T>;
}

export function shouldShowCsaWaitingBoard(state: DashboardCoreState, workerIdx: number): boolean {
    if (!Array.isArray(state.csaRuns)) return false;
    const run = state.csaRuns.find(
        (entry) =>
            entry &&
            typeof entry === 'object' &&
            (entry as { worker_idx?: unknown }).worker_idx === workerIdx &&
            (entry as { stopped?: unknown }).stopped !== true,
    );
    if (!run || typeof run !== 'object') return false;
    const currentGameId = (run as { current_game_id?: unknown }).current_game_id;
    return typeof currentGameId !== 'string' || !currentGameId.trim();
}

export function clearCsaWaitingCardSync(
    state: DashboardCoreState,
    workerIdx: number,
    cardState: Pick<LiveCardState, 'isSyncing' | 'syncingStartedAt'>,
): boolean {
    if (!shouldShowCsaWaitingBoard(state, workerIdx)) return false;
    cardState.isSyncing = false;
    delete cardState.syncingStartedAt;
    return true;
}

export function createWorkerSnapshotsController(deps: WorkerSnapshotDeps) {
    const { state, warnSoftFailure, showApiError, notifyDashboardServerStopped, getApiBase, requestJson } = deps;

    function countContiguousMoves(raw: unknown): number {
        if (!Array.isArray(raw)) return 0;
        let count = 0;
        for (let i = 0; i < raw.length; i += 1) {
            const mv = String(raw[i] ?? '').trim();
            if (!mv) break;
            count += 1;
        }
        return count;
    }

    function cacheWorkerSnapshot(workerIdx: number, data: WorkerSnapshotRecord | null | undefined): void {
        if (!Number.isFinite(workerIdx)) return;
        if (data && typeof data === 'object') {
            setWorkerSnapshotRecord(state, workerIdx, data);
        }
    }

    function getCachedWorkerSnapshot(workerIdx: number): WorkerSnapshotRecord | undefined {
        return peekWorkerSnapshotRecord(state, workerIdx);
    }

    function getWorkerSnapshot(workerIdx: number): {
        data: WorkerSnapshotRecord;
        maxPly: number;
        liveDisplayPly: number;
    } {
        const data = getWorkerSnapshotRecord(state, workerIdx);
        const maxPly = countContiguousMoves(data.moves);
        const current_ply = Number(data.current_ply || 0);
        const hasTerminal = hasTerminalResult(data);
        // Prefer the contiguous move tail: some update types may delay or suppress current_ply bumps.
        const liveDisplayPly = hasTerminal
            ? maxPly + 1
            : maxPly >= current_ply
              ? maxPly
              : Math.min(current_ply, maxPly);
        return { data, maxPly, liveDisplayPly };
    }

    function findWorkerIndexByGameId(gameId: string | null | undefined): number | null {
        const gid = String(gameId || '').trim();
        if (!gid) return null;
        const totalWorkers = Number.isFinite(state.numWorkers) ? Number(state.numWorkers) : 0;
        for (let i = 0; i < totalWorkers; i++) {
            const data = peekWorkerSnapshotRecord(state, i);
            if (!data) continue;
            const wgid = data.game_id;
            if (wgid && String(wgid) === gid) {
                return i;
            }
        }
        return null;
    }

    function currentWorkerGameId(workerIdx: number): string | null {
        const snapshot = peekWorkerSnapshotRecord(state, workerIdx);
        const snapshotGid = snapshot?.game_id;
        if (snapshotGid) return String(snapshotGid);
        const ws = peekWorkerState(state, workerIdx);
        const fromState = ws?.currentGameId ?? ws?.lastGameId ?? null;
        if (fromState) return String(fromState);
        return null;
    }

    function workerLatestLabel(workerIdx: number): string {
        const idx = Number(workerIdx);
        const labelIdx = Number.isNaN(idx) ? workerIdx : idx;
        const csaRun = Array.isArray(state.csaRuns)
            ? state.csaRuns.find(
                  (run) => run && typeof run === 'object' && (run as { worker_idx?: unknown }).worker_idx === idx,
              )
            : null;
        if (csaRun && typeof csaRun === 'object') {
            if ((csaRun as { stopped?: unknown }).stopped === true) {
                return `Worker #${labelIdx}: Finished`;
            }
            const currentGameId = (csaRun as { current_game_id?: unknown }).current_game_id;
            if (typeof currentGameId === 'string' && currentGameId.trim()) {
                return `Worker #${labelIdx}: ${currentGameId.trim()}`;
            }
            return `Worker #${labelIdx}: Waiting for pairing`;
        }
        const gid = Number.isNaN(idx) ? null : currentWorkerGameId(idx);
        return gid ? `Worker #${labelIdx}: ${gid}` : `Worker #${labelIdx} (Latest)`;
    }

    function getStableGameKey(snapshot: WorkerSnapshotRecord | null | undefined): string {
        if (!snapshot) return 'startpos';
        const gid = snapshot.game_id;
        if (gid != null) {
            const trimmed = String(gid).trim();
            if (trimmed) return trimmed;
        }
        const initial = snapshot.initial_sfen;
        if (initial != null) {
            const trimmed = String(initial).trim();
            if (trimmed) return trimmed;
        }
        return 'startpos';
    }

    async function fetchAndCacheWorkerSnapshot(workerIdx: number): Promise<WorkerSnapshotRecord | null> {
        const API_BASE = getApiBase();
        const endpoint = `${API_BASE}/api/worker/${workerIdx}`;
        try {
            const snap = await requestJson<WorkerSnapshotRecord | null>(endpoint);
            if (!snap) {
                return null;
            }
            snap.moves = Array.isArray(snap.moves) ? snap.moves : [];
            snap.ki2_moves = Array.isArray(snap.ki2_moves) ? snap.ki2_moves : [];
            snap.eval_black = Array.isArray(snap.eval_black) ? snap.eval_black : [];
            snap.eval_white = Array.isArray(snap.eval_white) ? snap.eval_white : [];
            snap.nodes_values = Array.isArray(snap.nodes_values) ? snap.nodes_values : [];
            snap.depth_values = Array.isArray(snap.depth_values) ? snap.depth_values : [];
            snap.seldepth_values = Array.isArray(snap.seldepth_values) ? snap.seldepth_values : [];
            snap.move_times_ms = Array.isArray(snap.move_times_ms) ? snap.move_times_ms : [];
            cacheWorkerSnapshot(workerIdx, snap);
            return snap;
        } catch (err) {
            const requestError = err as RequestError | undefined;
            if (requestError?.status) {
                showApiError('Failed to fetch worker snapshot', {
                    endpoint,
                    status: requestError.status,
                    message: requestError.message,
                    payload: requestError.payload ?? null,
                });
            } else {
                warnSoftFailure('Network error while fetching worker snapshot', err);
                notifyDashboardServerStopped();
            }
            return null;
        }
    }

    return {
        cacheWorkerSnapshot,
        getCachedWorkerSnapshot,
        getWorkerSnapshot,
        findWorkerIndexByGameId,
        currentWorkerGameId,
        workerLatestLabel,
        getStableGameKey,
        fetchAndCacheWorkerSnapshot,
    };
}
