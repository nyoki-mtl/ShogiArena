import { DEFAULT_INITIAL_SFEN, createEmptyWorkerSnapshot } from '@/modules/live/utils';
import type { WorkerSnapshotRecord, WorkerSnapshotUpdate } from '@/modules/live/types/updates';
import { updateEvalCollections, updateMoveCollections, updateSearchStatistics } from '../snapshots';

type NormalizeFn = (sfen: string | null | undefined) => string;

const defaultNormalize: NormalizeFn = (sfen) => {
    if (typeof sfen !== 'string') return DEFAULT_INITIAL_SFEN;
    const trimmed = sfen.trim();
    return trimmed || DEFAULT_INITIAL_SFEN;
};

function isAnalysisOnlyUpdate(update: WorkerSnapshotUpdate): boolean {
    const type = (update as { type?: unknown }).type;
    if (type !== 'analysis') return false;
    const move = (update as { move?: unknown }).move;
    const ki2Move = (update as { ki2_move?: unknown }).ki2_move;
    return !(typeof move === 'string' && move) && !(typeof ki2Move === 'string' && ki2Move);
}

function shouldApplyCurrentPly(
    update: WorkerSnapshotUpdate,
    snapshot: WorkerSnapshotRecord,
    baseCurrentPly: number,
): boolean {
    const raw = (update as { current_ply?: unknown }).current_ply;
    const incoming = typeof raw === 'number' && Number.isFinite(raw) ? Math.max(0, Math.trunc(raw)) : null;
    if (incoming == null) return false;

    // Always allow regression: it cannot create a "move hole", and Undo/"待った" is legitimate.
    if (incoming <= baseCurrentPly) return true;

    const plyFlag = (update as unknown as { __ply_from_patch?: unknown }).__ply_from_patch;
    const plyFromPatch = plyFlag !== false;
    const move = (update as { move?: unknown }).move;
    const ki2Move = (update as { ki2_move?: unknown }).ki2_move;
    const hasMove = (typeof move === 'string' && move.trim()) || (typeof ki2Move === 'string' && ki2Move.trim());
    // When current_ply was inherited from base (diff patch had no current_ply), it is not safe to advance.
    if (hasMove && !plyFromPatch) return false;

    // When we receive a move for a future ply while earlier moves are missing (drops/backpressure),
    // accepting current_ply would make legality checks attempt to replay non-existent moves.
    // Keep current_ply aligned with contiguous history; the out-of-order move will be deferred.
    if (hasMove && plyFromPatch) {
        const contiguous = countContiguousMoves(snapshot.moves);
        if (incoming > contiguous + 1) return false;
    }

    if (typeof move === 'string' && move.trim()) return true;
    if (typeof ki2Move === 'string' && ki2Move.trim()) return true;
    if (Array.isArray((update as { moves?: unknown }).moves)) return true;
    if (Array.isArray((update as { ki2_moves?: unknown }).ki2_moves)) return true;

    // For non-move updates that still advance ply (mate/analysis bursts), only accept if the move at that ply
    // is already present in the snapshot. This prevents "current_ply runs ahead of moves[]".
    const moves = Array.isArray(snapshot.moves) ? snapshot.moves : [];
    const idx = Math.max(0, incoming - 1);
    const existing = idx < moves.length ? String((moves as unknown[])[idx] ?? '').trim() : '';
    return Boolean(existing);
}

function cloneArray<T>(value: readonly T[] | null | undefined): T[] {
    return Array.isArray(value) ? [...value] : [];
}

function cloneSnapshot(base: WorkerSnapshotRecord | undefined): WorkerSnapshotRecord {
    const source = base ?? (createEmptyWorkerSnapshot() as unknown as WorkerSnapshotRecord);
    return {
        ...source,
        moves: cloneArray(source.moves),
        ki2_moves: cloneArray(source.ki2_moves),
        eval_black: cloneArray(source.eval_black),
        eval_white: cloneArray(source.eval_white),
        nodes_values: cloneArray(source.nodes_values),
        depth_values: cloneArray(source.depth_values),
        seldepth_values: cloneArray(source.seldepth_values),
        move_times_ms: cloneArray(source.move_times_ms),
        wall_times_ms: cloneArray(source.wall_times_ms),
        engine_wall_times_ms: cloneArray(source.engine_wall_times_ms),
        latency_deltas_ms: cloneArray(source.latency_deltas_ms),
        latency_alerts: Array.isArray(source.latency_alerts)
            ? source.latency_alerts.map((v) => (typeof v === 'boolean' ? v : Boolean(v)))
            : [],
    };
}

function countContiguousMoves(values: unknown): number {
    if (!Array.isArray(values)) return 0;
    let count = 0;
    for (let i = 0; i < values.length; i += 1) {
        const mv = String((values as unknown[])[i] ?? '').trim();
        if (!mv) break;
        count += 1;
    }
    return count;
}

const HISTORY_ARRAY_KEYS: Array<keyof WorkerSnapshotRecord> = [
    'moves',
    'ki2_moves',
    'eval_black',
    'eval_white',
    'nodes_values',
    'depth_values',
    'seldepth_values',
    'move_times_ms',
    'wall_times_ms',
    'engine_wall_times_ms',
    'latency_deltas_ms',
    'latency_alerts',
];

function truncateHistoryOnRewind(snapshot: WorkerSnapshotRecord, nextPly: number): void {
    const length = Math.max(0, Math.trunc(nextPly));
    let changed = false;
    for (const key of HISTORY_ARRAY_KEYS) {
        const value = snapshot[key];
        if (!Array.isArray(value)) continue;
        const nextLen = Math.min(value.length, length);
        if (value.length !== nextLen) {
            changed = true;
            (value as unknown[]).length = nextLen;
        }
    }
    if (changed) {
        const meta = snapshot as unknown as { __history_rev?: number };
        meta.__history_rev = (typeof meta.__history_rev === 'number' ? meta.__history_rev : 0) + 1;
    }
}

function coerceEval(update: WorkerSnapshotUpdate): number | null | undefined {
    if (typeof update.eval === 'number' && Number.isFinite(update.eval)) return update.eval;
    if (typeof (update as { eval_cp?: unknown }).eval_cp === 'number') {
        const evalCp = (update as { eval_cp?: number }).eval_cp;
        if (typeof evalCp === 'number' && Number.isFinite(evalCp)) return evalCp;
    }
    return undefined;
}

function extractClockPatch(update: WorkerSnapshotUpdate): Record<string, unknown> | null {
    const clock =
        update.clock && typeof update.clock === 'object' ? (update.clock as Record<string, unknown>) : undefined;
    const patch: Record<string, unknown> = {};
    const mergeField = (key: string, value: unknown) => {
        if (value !== undefined) {
            patch[key] = value;
        }
    };

    mergeField('active', update.active ?? clock?.active);
    mergeField('black_remain_ms', update.black_remain_ms ?? clock?.black_remain_ms);
    mergeField('white_remain_ms', update.white_remain_ms ?? clock?.white_remain_ms);
    mergeField(
        'applied_increment_ms',
        (update as { applied_increment_ms?: unknown }).applied_increment_ms ?? clock?.applied_increment_ms,
    );
    mergeField('occurred_at_ms', update.occurred_at_ms ?? clock?.occurred_at_ms);
    mergeField('started_at_ms', update.started_at_ms ?? clock?.started_at_ms);
    mergeField('pre_black_remain_ms', update.pre_black_remain_ms ?? clock?.pre_black_remain_ms);
    mergeField('pre_white_remain_ms', update.pre_white_remain_ms ?? clock?.pre_white_remain_ms);
    mergeField('byoyomi_ms_black', update.byoyomi_ms_black ?? clock?.byoyomi_ms_black);
    mergeField('byoyomi_ms_white', update.byoyomi_ms_white ?? clock?.byoyomi_ms_white);
    mergeField(
        'increment_ms_black',
        (update as { increment_ms_black?: unknown }).increment_ms_black ?? clock?.increment_ms_black,
    );
    mergeField(
        'increment_ms_white',
        (update as { increment_ms_white?: unknown }).increment_ms_white ?? clock?.increment_ms_white,
    );
    mergeField('time_control_black', update.time_control_black ?? clock?.time_control_black);
    mergeField('time_control_white', update.time_control_white ?? clock?.time_control_white);

    return Object.keys(patch).length ? patch : null;
}

/**
 * Merge a WorkerSnapshotUpdate into an existing snapshot, cloning arrays to keep immutability.
 * If the incoming game_id differs, a fresh snapshot is created.
 */
export function mergeWorkerSnapshot(
    base: WorkerSnapshotRecord | undefined,
    update: WorkerSnapshotUpdate,
    normalizeSFEN: NormalizeFn = defaultNormalize,
): WorkerSnapshotRecord {
    const incomingGid = update.game_id ?? null;
    const baseGid = base?.game_id ?? null;
    const resetForNewGame = incomingGid && baseGid && incomingGid !== baseGid;
    const snapshot = cloneSnapshot(resetForNewGame ? undefined : base);
    const baseCurrentPly =
        typeof snapshot.current_ply === 'number' && Number.isFinite(snapshot.current_ply)
            ? Math.trunc(snapshot.current_ply)
            : 0;

    if (incomingGid !== undefined) {
        snapshot.game_id = incomingGid;
    }
    const incomingCurrentPlyRaw =
        !isAnalysisOnlyUpdate(update) && typeof update.current_ply === 'number' && Number.isFinite(update.current_ply)
            ? Math.trunc(update.current_ply)
            : null;
    const acceptCurrentPly =
        typeof incomingCurrentPlyRaw === 'number' && shouldApplyCurrentPly(update, snapshot, baseCurrentPly)
            ? incomingCurrentPlyRaw
            : null;
    if (typeof acceptCurrentPly === 'number') {
        snapshot.current_ply = acceptCurrentPly;
        if (baseCurrentPly > acceptCurrentPly) truncateHistoryOnRewind(snapshot, acceptCurrentPly);
    }
    if (typeof update.initial_sfen === 'string' && update.initial_sfen.trim()) {
        snapshot.initial_sfen = update.initial_sfen;
    }
    if (typeof update.sfen === 'string' && update.sfen.trim()) {
        snapshot.sfen = update.sfen;
    }
    if (typeof update.black_name === 'string') snapshot.black_name = update.black_name;
    if (typeof update.white_name === 'string') snapshot.white_name = update.white_name;
    if (update.time_control_black !== undefined)
        snapshot.time_control_black = update.time_control_black as string | null;
    if (update.time_control_white !== undefined)
        snapshot.time_control_white = update.time_control_white as string | null;
    if (update.engine_status && typeof update.engine_status === 'object') {
        snapshot.engine_status = update.engine_status as typeof snapshot.engine_status;
    }

    const move = (update as { move?: unknown }).move;
    const ki2Move = (update as { ki2_move?: unknown }).ki2_move;
    const hasMove = (typeof move === 'string' && move.trim()) || (typeof ki2Move === 'string' && ki2Move.trim());
    const contiguousBefore = countContiguousMoves(snapshot.moves);
    const plyFlag = (update as unknown as { __ply_from_patch?: unknown }).__ply_from_patch;
    const plyFromPatch = plyFlag !== false;
    const desiredIndex =
        hasMove && plyFromPatch && typeof incomingCurrentPlyRaw === 'number' && incomingCurrentPlyRaw > 0
            ? Math.max(0, incomingCurrentPlyRaw - 1)
            : typeof acceptCurrentPly === 'number' && acceptCurrentPly > 0
              ? Math.max(0, acceptCurrentPly - 1)
              : null;
    // IMPORTANT: never "compress" moves by appending a move that is explicitly for a future ply.
    // If we are missing earlier moves (drops/backpressure), appending would shift the entire sequence and
    // permanently desync move<->position. Instead, write at the server-indicated index and allow holes.
    const plyIndex = hasMove
        ? desiredIndex != null
            ? desiredIndex
            : contiguousBefore
        : Math.max(0, ((typeof acceptCurrentPly === 'number' ? acceptCurrentPly : baseCurrentPly) || 1) - 1);
    const evalValue = coerceEval(update);
    const evalUpdate = evalValue === undefined ? update : { ...update, eval: evalValue };

    updateMoveCollections(snapshot, update, plyIndex, normalizeSFEN);
    updateEvalCollections(snapshot, evalUpdate, plyIndex, normalizeSFEN);
    updateSearchStatistics(snapshot, update);
    if (hasMove && (update as unknown as { __ply_from_patch?: unknown }).__ply_from_patch === false) {
        snapshot.current_ply = countContiguousMoves(snapshot.moves);
    }
    const contiguousAfter = countContiguousMoves(snapshot.moves);
    if (typeof snapshot.current_ply === 'number' && snapshot.current_ply > contiguousAfter) {
        snapshot.current_ply = contiguousAfter;
    }

    const clockPatch = extractClockPatch(update);
    if (clockPatch) {
        const existing =
            snapshot.clock && typeof snapshot.clock === 'object' ? (snapshot.clock as Record<string, unknown>) : {};
        snapshot.clock = { ...existing, ...clockPatch };
    }

    return snapshot;
}

/**
 * Merge a WorkerSnapshotUpdate into an existing snapshot in-place.
 *
 * The Merge Worker runs at high update rates; cloning full history arrays on every diff
 * leads to O(ply) growth and eventual backlog. This function keeps the existing snapshot
 * object and mutates its arrays directly.
 */
export function mergeWorkerSnapshotMutable(
    base: WorkerSnapshotRecord | undefined,
    update: WorkerSnapshotUpdate,
    normalizeSFEN: NormalizeFn = defaultNormalize,
): WorkerSnapshotRecord {
    const incomingGid = update.game_id ?? null;
    const baseGid = base?.game_id ?? null;
    const resetForNewGame = incomingGid && baseGid && incomingGid !== baseGid;

    const snapshot: WorkerSnapshotRecord = resetForNewGame
        ? (createEmptyWorkerSnapshot() as unknown as WorkerSnapshotRecord)
        : (base ?? (createEmptyWorkerSnapshot() as unknown as WorkerSnapshotRecord));
    const baseCurrentPly =
        typeof snapshot.current_ply === 'number' && Number.isFinite(snapshot.current_ply)
            ? Math.trunc(snapshot.current_ply)
            : 0;

    if (incomingGid !== undefined) {
        snapshot.game_id = incomingGid;
    }
    const incomingCurrentPlyRaw =
        !isAnalysisOnlyUpdate(update) && typeof update.current_ply === 'number' && Number.isFinite(update.current_ply)
            ? Math.trunc(update.current_ply)
            : null;
    const acceptCurrentPly =
        typeof incomingCurrentPlyRaw === 'number' && shouldApplyCurrentPly(update, snapshot, baseCurrentPly)
            ? incomingCurrentPlyRaw
            : null;
    if (typeof acceptCurrentPly === 'number') {
        snapshot.current_ply = acceptCurrentPly;
        if (baseCurrentPly > acceptCurrentPly) truncateHistoryOnRewind(snapshot, acceptCurrentPly);
    }
    if (typeof update.initial_sfen === 'string' && update.initial_sfen.trim()) {
        snapshot.initial_sfen = update.initial_sfen;
    }
    if (typeof update.sfen === 'string' && update.sfen.trim()) {
        snapshot.sfen = update.sfen;
    }
    if (typeof update.black_name === 'string') snapshot.black_name = update.black_name;
    if (typeof update.white_name === 'string') snapshot.white_name = update.white_name;
    if (update.time_control_black !== undefined)
        snapshot.time_control_black = update.time_control_black as string | null;
    if (update.time_control_white !== undefined)
        snapshot.time_control_white = update.time_control_white as string | null;
    if (update.engine_status && typeof update.engine_status === 'object') {
        snapshot.engine_status = update.engine_status as typeof snapshot.engine_status;
    }

    const move = (update as { move?: unknown }).move;
    const ki2Move = (update as { ki2_move?: unknown }).ki2_move;
    const hasMove = (typeof move === 'string' && move.trim()) || (typeof ki2Move === 'string' && ki2Move.trim());
    const contiguousBefore = countContiguousMoves(snapshot.moves);
    const plyFlag = (update as unknown as { __ply_from_patch?: unknown }).__ply_from_patch;
    const plyFromPatch = plyFlag !== false;
    const desiredIndex =
        hasMove && plyFromPatch && typeof incomingCurrentPlyRaw === 'number' && incomingCurrentPlyRaw > 0
            ? Math.max(0, incomingCurrentPlyRaw - 1)
            : typeof acceptCurrentPly === 'number' && acceptCurrentPly > 0
              ? Math.max(0, acceptCurrentPly - 1)
              : null;
    const plyIndex = hasMove
        ? desiredIndex != null
            ? desiredIndex
            : contiguousBefore
        : Math.max(0, ((typeof acceptCurrentPly === 'number' ? acceptCurrentPly : baseCurrentPly) || 1) - 1);
    const evalValue = coerceEval(update);
    const evalUpdate = evalValue === undefined ? update : { ...update, eval: evalValue };

    updateMoveCollections(snapshot, update, plyIndex, normalizeSFEN);
    updateEvalCollections(snapshot, evalUpdate, plyIndex, normalizeSFEN);
    updateSearchStatistics(snapshot, update);
    if (hasMove && (update as unknown as { __ply_from_patch?: unknown }).__ply_from_patch === false) {
        snapshot.current_ply = countContiguousMoves(snapshot.moves);
    }
    const contiguousAfter = countContiguousMoves(snapshot.moves);
    if (typeof snapshot.current_ply === 'number' && snapshot.current_ply > contiguousAfter) {
        snapshot.current_ply = contiguousAfter;
    }

    const clockPatch = extractClockPatch(update);
    if (clockPatch) {
        const existing =
            snapshot.clock && typeof snapshot.clock === 'object' ? (snapshot.clock as Record<string, unknown>) : {};
        snapshot.clock = { ...existing, ...clockPatch };
    }

    return snapshot;
}
