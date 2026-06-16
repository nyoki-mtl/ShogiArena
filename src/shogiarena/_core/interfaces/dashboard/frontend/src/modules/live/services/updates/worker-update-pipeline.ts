import { normalizeSFEN as fallbackNormalizeSFEN } from '@/modules/live/services/time/sfen';
import { DEFAULT_INITIAL_SFEN } from '@/modules/live/utils';
import {
    buildSnapshotFromArrays,
    createEmptySnapshotForGame,
    hasSnapshotArrayPayload,
    updateEvalCollections,
    updateMoveCollections,
    updateSearchStatistics,
} from './snapshots';
import type { EngineStatusSnapshot } from '@/modules/live/utils/engine-status';
import type { LiveUpdatesContext, WorkerSnapshotUpdate } from '@/modules/live/types/updates';
import {
    getWorkerSnapshotRecord,
    getWorkerState,
    setWorkerSnapshotRecord,
    setWorkerState,
} from '@/modules/live/state/updates';
import {
    resetWorkerRuntimeClockStateForNewGame,
    syncClockToTurnBoundary,
    updateTimeControlState,
} from '@/modules/live/utils/clock-sync';

interface PipelineDeps {
    ctx: LiveUpdatesContext;
    safeClone: <T>(value: T) => T;
    emitEvent: (eventName: string, payload: unknown) => void;
    applyClockSnapshot: (workerIdx: number, payload: WorkerSnapshotUpdate | null | undefined) => void;
    applyClockIncrement: (workerIdx: number, payload: WorkerSnapshotUpdate | null | undefined) => void;
    handleCatchupIfNeeded: (workerIdx: number, incomingPly: number | null, previousAppliedPly: number) => void;
    cardSync: { scheduleCardUpdate: (workerIdx: number) => void };
    lastUpdateWallClock: Map<number, number>;
    lastMoveUpdateWallClock: Map<number, number>;
}

/**
 * ワーカーごとの更新を適用し、状態・カード・イベントを同期する。
 * SSE 契約に沿った正規化済みペイロードを前提とする。
 */
export function createWorkerUpdatePipeline(deps: PipelineDeps) {
    const {
        ctx,
        safeClone,
        emitEvent,
        applyClockSnapshot,
        applyClockIncrement,
        handleCatchupIfNeeded,
        cardSync,
        lastUpdateWallClock,
        lastMoveUpdateWallClock,
    } = deps;
    const { state, normalizeSFEN = fallbackNormalizeSFEN } = ctx;
    const scope = 'Live.WorkerUpdate';

    function reconcileGameChange(
        _workerIdx: number,
        snapshot: ReturnType<typeof getWorkerSnapshotRecord>,
        workerState: ReturnType<typeof getWorkerState>,
        update: WorkerSnapshotUpdate,
    ) {
        const incomingGameId = update.game_id ?? null;
        const existingGameId = snapshot.game_id ? String(snapshot.game_id) : null;
        if (incomingGameId && snapshot.game_id && snapshot.game_id !== incomingGameId) {
            if (!update.initial_sfen) {
                throw new Error(`${scope}: Worker update missing initial_sfen for new game`);
            }
            workerState.prevGameId = existingGameId;
            resetWorkerRuntimeClockStateForNewGame(workerState);
            workerState.engineStatus = undefined;
            snapshot = createEmptySnapshotForGame(update, snapshot);
        }

        if (incomingGameId) {
            snapshot.game_id = incomingGameId;
        }

        if (typeof update.current_ply === 'number') {
            snapshot.current_ply = update.current_ply;
        }

        const activeGid = snapshot.game_id ?? null;
        if (activeGid) {
            if (workerState.currentGameId && workerState.currentGameId !== activeGid) {
                workerState.prevGameId = workerState.currentGameId;
            }
            workerState.currentGameId = String(activeGid);
            workerState.lastGameId = String(activeGid);
        }

        if (update.initial_sfen && (!snapshot.initial_sfen || snapshot.initial_sfen === DEFAULT_INITIAL_SFEN)) {
            snapshot.initial_sfen = update.initial_sfen;
        }
        if (update.sfen) snapshot.sfen = update.sfen;
        if (update.black_name && !snapshot.black_name) snapshot.black_name = update.black_name;
        if (update.white_name && !snapshot.white_name) snapshot.white_name = update.white_name;

        return snapshot;
    }

    function applyCollections(snapshot: ReturnType<typeof getWorkerSnapshotRecord>, update: WorkerSnapshotUpdate) {
        const plyIndex = Math.max(0, (snapshot.current_ply ?? 0) - 1);
        updateMoveCollections(snapshot, update, plyIndex, normalizeSFEN);
        updateEvalCollections(snapshot, update, plyIndex, normalizeSFEN);
        updateSearchStatistics(snapshot, update);
    }

    function updateWallClockMaps(workerIdx: number, nowWall: number, update: WorkerSnapshotUpdate) {
        const lastWall = lastUpdateWallClock.get(workerIdx);
        if (lastWall !== undefined) {
            // gap measurement removed with debug logging
        }
        lastUpdateWallClock.set(workerIdx, nowWall);

        const isClockEvent = update.type === 'clock_start' || update.type === 'clock_increment';
        const hasSnapshotArrays =
            Array.isArray(update.moves) ||
            Array.isArray(update.ki2_moves) ||
            Array.isArray(update.eval_black) ||
            Array.isArray(update.eval_white) ||
            Array.isArray(update.nodes_values) ||
            Array.isArray(update.depth_values) ||
            Array.isArray(update.seldepth_values) ||
            Array.isArray(update.move_times_ms) ||
            Array.isArray(update.wall_times_ms) ||
            Array.isArray(update.engine_wall_times_ms) ||
            Array.isArray(update.latency_deltas_ms) ||
            Array.isArray(update.latency_alerts);

        const incomingPly = typeof update.current_ply === 'number' ? update.current_ply : null;
        const isMoveEvent = !isClockEvent && !hasSnapshotArrays && (update.move != null || incomingPly !== null);
        if (isMoveEvent) {
            const lastMoveWall = lastMoveUpdateWallClock.get(workerIdx);
            if (lastMoveWall !== undefined) {
                // gap measurement removed with debug logging
            }
            lastMoveUpdateWallClock.set(workerIdx, nowWall);

            if (incomingPly !== null) {
                // delta logging removed
            }
        }
    }

    function applyClockUpdate(workerIdx: number, update: WorkerSnapshotUpdate): boolean {
        if (update.type === 'clock_start') {
            applyClockSnapshot(workerIdx, update);
            return true;
        }
        if (update.type === 'clock_increment') {
            applyClockIncrement(workerIdx, update);
            return true;
        }
        return false;
    }

    function emitClockEvent(workerIdx: number, update: WorkerSnapshotUpdate): void {
        if (update.type !== 'clock_start' && update.type !== 'clock_increment') return;
        emitEvent('worker:clock', {
            workerIdx,
            kind: update.type,
            clock: safeClone(update),
        });
    }

    function emitSnapshot(workerIdx: number, snapshot: ReturnType<typeof getWorkerSnapshotRecord>) {
        emitEvent('worker:snapshot', {
            workerIdx,
            snapshot: {
                data: safeClone(snapshot),
                workerState: safeClone(getWorkerState(state, workerIdx)),
            },
        });
    }

    function applyWorkerUpdate(workerIdx: number, update: WorkerSnapshotUpdate) {
        let snapshot = getWorkerSnapshotRecord(state, workerIdx);
        const workerState = getWorkerState(state, workerIdx);

        if (hasSnapshotArrayPayload(update)) {
            snapshot = buildSnapshotFromArrays(update, snapshot);
        }

        const previousAppliedPly =
            typeof workerState.lastAppliedPly === 'number'
                ? workerState.lastAppliedPly
                : typeof snapshot.current_ply === 'number'
                  ? snapshot.current_ply
                  : 0;

        snapshot = reconcileGameChange(workerIdx, snapshot, workerState, update);
        updateTimeControlState(
            workerState,
            update.time_control_black ?? snapshot.time_control_black,
            update.time_control_white ?? snapshot.time_control_white,
        );
        const tcBlackRaw = update.time_control_black ?? snapshot.time_control_black;
        const tcWhiteRaw = update.time_control_white ?? snapshot.time_control_white;
        if (typeof tcBlackRaw === 'string' && tcBlackRaw.trim()) workerState.timeControlBlack = tcBlackRaw.trim();
        if (typeof tcWhiteRaw === 'string' && tcWhiteRaw.trim()) workerState.timeControlWhite = tcWhiteRaw.trim();
        applyCollections(snapshot, update);
        if (update.engine_status && typeof update.engine_status === 'object') {
            snapshot.engine_status = update.engine_status as EngineStatusSnapshot;
        }

        setWorkerState(state, workerIdx, workerState);
        ctx.cards.updateWorkerOptionLabels(workerIdx ?? null);

        const incomingPly = typeof update.current_ply === 'number' ? update.current_ply : null;
        handleCatchupIfNeeded(workerIdx, incomingPly, previousAppliedPly);

        const hasClockUpdate = applyClockUpdate(workerIdx, update);

        const nowWall = Date.now();
        updateWallClockMaps(workerIdx, nowWall, update);

        setWorkerSnapshotRecord(state, workerIdx, snapshot);
        const latestSnapshot = getWorkerSnapshotRecord(state, workerIdx);
        const workerStateLatest = getWorkerState(state, workerIdx);
        workerStateLatest.lastAppliedPly = latestSnapshot.current_ply ?? incomingPly ?? previousAppliedPly;
        workerStateLatest.lastAppliedAtMs = nowWall;
        syncClockToTurnBoundary({
            ws: workerStateLatest,
            current_ply: workerStateLatest.lastAppliedPly,
            initialSfen: latestSnapshot.initial_sfen ?? null,
            normalizeSFEN,
            nowMs: nowWall,
        });
        const engineStatusCandidate = (update.engine_status ?? latestSnapshot.engine_status) as
            | EngineStatusSnapshot
            | undefined;
        if (engineStatusCandidate && typeof engineStatusCandidate === 'object') {
            workerStateLatest.engineStatus = engineStatusCandidate;
        }
        setWorkerState(state, workerIdx, workerStateLatest);

        if (hasClockUpdate) {
            emitClockEvent(workerIdx, update);
        }

        // Schedule card update using requestAnimationFrame to avoid burst rendering
        cardSync.scheduleCardUpdate(workerIdx);

        emitSnapshot(workerIdx, latestSnapshot);
    }

    return { applyWorkerUpdate } as const;
}
