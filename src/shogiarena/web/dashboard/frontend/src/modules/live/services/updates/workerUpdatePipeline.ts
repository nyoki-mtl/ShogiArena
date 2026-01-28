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
import type { LiveUpdatesContext, WorkerSnapshotUpdate } from '@/modules/live/types/updates';
import {
    getWorkerSnapshotRecord,
    getWorkerState,
    setWorkerSnapshotRecord,
    setWorkerState,
} from '@/modules/live/state/updates';
import { syncClockToTurnBoundary, updateTimeControlState } from '@/modules/live/utils/clockSync';

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
            workerState.lastSeenPly = undefined;
            workerState.lastSideToMove = undefined;
            workerState.pendingClockBySide = undefined;
            workerState.clockActive = null;
            workerState.startedAtMs = undefined;
            workerState.blackFrozenByoText = null;
            workerState.whiteFrozenByoText = null;
            workerState.tcBlackHasTimePool = undefined;
            workerState.tcWhiteHasTimePool = undefined;
            workerState.tcBlackHasSearchLimit = undefined;
            workerState.tcWhiteHasSearchLimit = undefined;
            workerState.clockDisplayMode = undefined;
            snapshot = createEmptySnapshotForGame(update, snapshot);
        }

        if (incomingGameId) {
            snapshot.game_id = incomingGameId;
        }

        if (typeof update.currentPly === 'number') {
            snapshot.currentPly = update.currentPly;
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
        const plyIndex = Math.max(0, (snapshot.currentPly ?? 0) - 1);
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
            Array.isArray(update.latency_deltas_ms) ||
            Array.isArray(update.latency_alerts);

        const incomingPly = typeof update.currentPly === 'number' ? update.currentPly : null;
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

    function dispatchClockEvents(workerIdx: number, update: WorkerSnapshotUpdate) {
        if (update.type === 'clock_start') {
            applyClockSnapshot(workerIdx, update);
            emitEvent('worker:clock', {
                workerIdx,
                kind: 'clock_start',
                clock: safeClone(update),
            });
        }
        if (update.type === 'clock_increment') {
            applyClockIncrement(workerIdx, update);
            emitEvent('worker:clock', {
                workerIdx,
                kind: 'clock_increment',
                clock: safeClone(update),
            });
        }
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
                : typeof snapshot.currentPly === 'number'
                  ? snapshot.currentPly
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

        setWorkerState(state, workerIdx, workerState);
        ctx.cards.updateWorkerOptionLabels(workerIdx ?? null);

        const incomingPly = typeof update.currentPly === 'number' ? update.currentPly : null;
        handleCatchupIfNeeded(workerIdx, incomingPly, previousAppliedPly);

        // Schedule card update using requestAnimationFrame to avoid burst rendering
        cardSync.scheduleCardUpdate(workerIdx);

        dispatchClockEvents(workerIdx, update);

        const nowWall = Date.now();
        updateWallClockMaps(workerIdx, nowWall, update);

        setWorkerSnapshotRecord(state, workerIdx, snapshot);
        const latestSnapshot = getWorkerSnapshotRecord(state, workerIdx);
        const workerStateLatest = getWorkerState(state, workerIdx);
        workerStateLatest.lastAppliedPly = latestSnapshot.currentPly ?? incomingPly ?? previousAppliedPly;
        workerStateLatest.lastAppliedAtMs = nowWall;
        syncClockToTurnBoundary({
            ws: workerStateLatest,
            currentPly: workerStateLatest.lastAppliedPly,
            initialSfen: latestSnapshot.initial_sfen ?? null,
            normalizeSFEN,
            nowMs: nowWall,
        });
        setWorkerState(state, workerIdx, workerStateLatest);

        emitSnapshot(workerIdx, latestSnapshot);
    }

    return { applyWorkerUpdate } as const;
}
