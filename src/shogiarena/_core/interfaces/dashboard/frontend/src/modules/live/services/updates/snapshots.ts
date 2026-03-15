import { createEmptyWorkerSnapshot, DEFAULT_INITIAL_SFEN } from '@/modules/live/utils';
import type { WorkerSnapshot } from '@/modules/live/types/public';
import type { WorkerSnapshotRecord, WorkerSnapshotUpdate } from '@/modules/live/types/updates';

type NormalizeFn = (sfen: string | null | undefined) => string;

let _evalDebugEnabled: boolean | null = null;
function isEvalDebugEnabled(): boolean {
    if (_evalDebugEnabled != null) return _evalDebugEnabled;
    try {
        const params = new URLSearchParams(window?.location?.search ?? '');
        _evalDebugEnabled = params.get('evalDebug') === '1';
    } catch {
        _evalDebugEnabled = false;
    }
    return _evalDebugEnabled;
}

export function hasSnapshotArrayPayload(update: WorkerSnapshotUpdate): boolean {
    return (
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
        Array.isArray(update.latency_alerts)
    );
}

function cloneArray<T>(values: readonly T[] | undefined | null): T[] {
    return Array.isArray(values) ? values.slice() : [];
}

function ensureLength(array: unknown[], length: number): void {
    for (let index = array.length; index < length; index += 1) {
        array.push(null);
    }
}

function emitMoveGapEvent(snapshot: WorkerSnapshotRecord, plyIndex: number): void {
    const gid = snapshot.game_id ?? null;
    if (!gid || typeof gid !== 'string') return;
    // Only the main thread can trigger WS recovery; Web Workers don't have access to the WS connection.
    if (typeof window === 'undefined') return;
    try {
        const event = new CustomEvent('live:move-gap', {
            detail: {
                gid,
                gapPly: plyIndex + 1,
                topic: `live.game.${gid}.moves.diff`,
            },
        });
        window.dispatchEvent(event);
    } catch {
        // ignore
    }
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
    'latency_deltas_ms',
    'latency_alerts',
];

function truncateHistory(snapshot: WorkerSnapshotRecord, length: number): boolean {
    const nextLen = Math.max(0, Math.trunc(length));
    let changed = false;
    for (const key of HISTORY_ARRAY_KEYS) {
        const value = snapshot[key];
        if (!Array.isArray(value)) continue;
        if (value.length > nextLen) {
            (value as unknown[]).length = nextLen;
            changed = true;
        }
    }
    if (changed) {
        const ply =
            typeof snapshot.current_ply === 'number' && Number.isFinite(snapshot.current_ply)
                ? snapshot.current_ply
                : 0;
        if (ply > nextLen) snapshot.current_ply = nextLen;
    }
    return changed;
}

function getPendingEvalRaw(snapshot: WorkerSnapshotRecord): Array<number | null> {
    const key = '__pending_eval_raw';
    const existing = (snapshot as unknown as Record<string, unknown>)[key];
    if (Array.isArray(existing)) {
        return existing as Array<number | null>;
    }
    const next: Array<number | null> = [];
    (snapshot as unknown as Record<string, unknown>)[key] = next;
    return next;
}

function applyEvalAtIndex(
    snapshot: WorkerSnapshotRecord,
    rawEval: number,
    plyIndex: number,
    normalizeSFEN: NormalizeFn,
): void {
    if (!Array.isArray(snapshot.eval_black)) snapshot.eval_black = [];
    if (!Array.isArray(snapshot.eval_white)) snapshot.eval_white = [];

    ensureLength(snapshot.eval_black, plyIndex + 1);
    ensureLength(snapshot.eval_white, plyIndex + 1);

    const moverIsBlack = computeMoverIsBlack(snapshot.initial_sfen, plyIndex, normalizeSFEN);
    const value = Number(rawEval);
    snapshot.eval_black[plyIndex] = moverIsBlack ? value : -value;
    snapshot.eval_white[plyIndex] = moverIsBlack ? -value : value;
}

function flushPendingEval(snapshot: WorkerSnapshotRecord, plyIndex: number, normalizeSFEN: NormalizeFn): void {
    const pending = getPendingEvalRaw(snapshot);
    const raw = pending[plyIndex];
    if (typeof raw !== 'number' || !Number.isFinite(raw)) {
        return;
    }
    pending[plyIndex] = null;
    applyEvalAtIndex(snapshot, raw, plyIndex, normalizeSFEN);
}

export function buildSnapshotFromArrays(
    update: WorkerSnapshotUpdate,
    previous: WorkerSnapshotRecord,
): WorkerSnapshotRecord {
    return {
        ...previous,
        game_id: update.game_id ?? previous.game_id ?? null,
        initial_sfen: update.initial_sfen ?? previous.initial_sfen ?? DEFAULT_INITIAL_SFEN,
        black_name: update.black_name ?? previous.black_name ?? null,
        white_name: update.white_name ?? previous.white_name ?? null,
        moves: Array.isArray(update.moves) ? cloneArray(update.moves) : cloneArray(previous.moves),
        ki2_moves: Array.isArray(update.ki2_moves) ? cloneArray(update.ki2_moves) : cloneArray(previous.ki2_moves),
        eval_black: Array.isArray(update.eval_black) ? cloneArray(update.eval_black) : cloneArray(previous.eval_black),
        eval_white: Array.isArray(update.eval_white) ? cloneArray(update.eval_white) : cloneArray(previous.eval_white),
        nodes_values: Array.isArray(update.nodes_values)
            ? cloneArray(update.nodes_values)
            : cloneArray(previous.nodes_values),
        depth_values: Array.isArray(update.depth_values)
            ? cloneArray(update.depth_values)
            : cloneArray(previous.depth_values),
        seldepth_values: Array.isArray(update.seldepth_values)
            ? cloneArray(update.seldepth_values)
            : cloneArray(previous.seldepth_values),
        move_times_ms: Array.isArray(update.move_times_ms)
            ? cloneArray(update.move_times_ms)
            : cloneArray(previous.move_times_ms),
        wall_times_ms: Array.isArray(update.wall_times_ms)
            ? cloneArray(update.wall_times_ms)
            : cloneArray(previous.wall_times_ms),
        latency_deltas_ms: Array.isArray(update.latency_deltas_ms)
            ? cloneArray(update.latency_deltas_ms)
            : cloneArray(previous.latency_deltas_ms),
        latency_alerts: Array.isArray(update.latency_alerts)
            ? cloneArray(update.latency_alerts)
            : cloneArray(previous.latency_alerts),
        sfen: update.sfen ?? previous.sfen ?? null,
        current_ply:
            typeof update.current_ply === 'number'
                ? update.current_ply
                : typeof previous.current_ply === 'number'
                  ? previous.current_ply
                  : 0,
    };
}

export function createEmptySnapshotForGame(
    update: WorkerSnapshotUpdate,
    previous: WorkerSnapshotRecord,
): WorkerSnapshotRecord {
    const blank = createEmptyWorkerSnapshot() as WorkerSnapshotRecord;
    const next: WorkerSnapshotRecord = {
        ...blank,
        game_id: update.game_id ?? null,
        initial_sfen: update.initial_sfen ?? previous.initial_sfen ?? DEFAULT_INITIAL_SFEN,
        black_name: update.black_name ?? previous.black_name ?? null,
        white_name: update.white_name ?? previous.white_name ?? null,
    };
    if (update.time_control_black ?? previous.time_control_black) {
        next.time_control_black = update.time_control_black ?? previous.time_control_black ?? null;
    }
    if (update.time_control_white ?? previous.time_control_white) {
        next.time_control_white = update.time_control_white ?? previous.time_control_white ?? null;
    }
    return next;
}

function computeMoverIsBlack(
    initialSfen: string | null | undefined,
    plyIndex: number,
    normalizeSFEN: NormalizeFn,
): boolean {
    const normalized = normalizeSFEN(initialSfen ?? DEFAULT_INITIAL_SFEN);
    const parts = normalized.split(' ');
    const startIsBlack = parts.length > 1 ? parts[1] === 'b' : true;
    const moveNumber = plyIndex + 1;
    return startIsBlack ? moveNumber % 2 === 1 : moveNumber % 2 === 0;
}

export function updateMoveCollections(
    snapshot: WorkerSnapshotRecord,
    update: WorkerSnapshotUpdate,
    plyIndex: number,
    normalizeSFEN: NormalizeFn,
): void {
    if (!Array.isArray(snapshot.moves)) snapshot.moves = [];
    if (!Array.isArray(snapshot.ki2_moves)) snapshot.ki2_moves = [];

    if (update.move) {
        const prevLen = Array.isArray(snapshot.moves) ? snapshot.moves.length : 0;
        // Prevent creating "holes" (null padding) in move history. Under backpressure or
        // stream reorder, we may receive a move for a future ply while missing earlier moves.
        // Padding would permanently desync the move sequence and triggers `[LiveLegal] missing move`.
        if (plyIndex > prevLen) {
            const meta = snapshot as unknown as Record<string, unknown>;
            const gapPly = plyIndex + 1;
            const existingGapRaw = meta.__move_gap_detected_ply;
            const existingGap =
                typeof existingGapRaw === 'number' && Number.isFinite(existingGapRaw) ? Math.max(0, existingGapRaw) : 0;
            if (existingGap <= 0 || gapPly < existingGap) {
                meta.__move_gap_detected_ply = gapPly;
            }
            emitMoveGapEvent(snapshot, plyIndex);
            return;
        }
        ensureLength(snapshot.moves, plyIndex + 1);
        // Overwrite is allowed: the server may legitimately revise a move at the same ply
        // (e.g. Undo/"待った" + re-play, or correction diffs). Ordering is guaranteed upstream.
        const next = String(update.move);
        const prev = snapshot.moves[plyIndex];
        snapshot.moves[plyIndex] = next;
        if (prev !== next) {
            // If we revised a move in the middle of the game, future history becomes invalid.
            // Truncate to avoid temporarily constructing illegal sequences until corrected moves arrive.
            if (plyIndex < prevLen - 1) {
                truncateHistory(snapshot, plyIndex + 1);
            }
            const meta = snapshot as unknown as { __history_rev?: number };
            meta.__history_rev = (typeof meta.__history_rev === 'number' ? meta.__history_rev : 0) + 1;
        }
        flushPendingEval(snapshot, plyIndex, normalizeSFEN);
    }

    if (update.ki2_move) {
        const prevLen = Array.isArray(snapshot.ki2_moves) ? snapshot.ki2_moves.length : 0;
        if (plyIndex > prevLen) {
            const meta = snapshot as unknown as Record<string, unknown>;
            const gapPly = plyIndex + 1;
            const existingGapRaw = meta.__move_gap_detected_ply;
            const existingGap =
                typeof existingGapRaw === 'number' && Number.isFinite(existingGapRaw) ? Math.max(0, existingGapRaw) : 0;
            if (existingGap <= 0 || gapPly < existingGap) {
                meta.__move_gap_detected_ply = gapPly;
            }

            emitMoveGapEvent(snapshot, plyIndex);
            return;
        }
        ensureLength(snapshot.ki2_moves, plyIndex + 1);
        const next = String(update.ki2_move);
        const prev = snapshot.ki2_moves[plyIndex];
        snapshot.ki2_moves[plyIndex] = next;
        if (prev !== next) {
            if (plyIndex < prevLen - 1) {
                truncateHistory(snapshot, plyIndex + 1);
            }
            const meta = snapshot as unknown as { __history_rev?: number };
            meta.__history_rev = (typeof meta.__history_rev === 'number' ? meta.__history_rev : 0) + 1;
        }
    }

    if (Object.hasOwn(update, 'game_result')) {
        const next = (update as { game_result?: unknown }).game_result;
        snapshot.game_result = (typeof next === 'string' ? next : null) as WorkerSnapshot['game_result'];
    }
}

export function updateEvalCollections(
    snapshot: WorkerSnapshotRecord,
    update: WorkerSnapshotUpdate,
    plyIndex: number,
    normalizeSFEN: NormalizeFn,
): void {
    const debug = isEvalDebugEnabled();
    // Analysis-only updates (no move attached) report evaluation for the *current side to move*,
    // not for the last committed ply. Recording them into per-ply history makes the chart look
    // like it "flipped" (especially for mate scores) and diverges from DB-backed history.
    // Only commit eval history when it is associated with a move.
    if (
        update.type === 'analysis' &&
        (update.move == null || update.move === '') &&
        (update.ki2_move == null || update.ki2_move === '')
    ) {
        if (
            debug &&
            typeof update.eval === 'number' &&
            Number.isFinite(update.eval) &&
            Math.abs(update.eval) >= 30000
        ) {
            console.debug('[LiveEval] skip analysis-only eval (mate-like)', {
                initial_sfen: snapshot.initial_sfen ?? null,
                current_ply: snapshot.current_ply ?? null,
                plyIndex,
                eval: update.eval,
            });
        }
        return;
    }
    if (typeof update.eval !== 'number') {
        return;
    }
    // current_ply=0 は「まだ着手が無い局面」。評価値を ply 配列に載せると
    // "最初に変な点が出て、後から訂正される" ように見えやすいのでスキップする。
    const current_ply = typeof snapshot.current_ply === 'number' ? snapshot.current_ply : 0;
    if (current_ply <= 0) {
        return;
    }
    const value = Number(update.eval);
    if (!Number.isFinite(value)) {
        return;
    }
    const mateLike = Math.abs(value) >= 30000;
    // Make eval deterministic: only commit to history once the corresponding move exists.
    // Otherwise (e.g. analysis arrives before move, or move diff was dropped), it looks like "wrong then corrected".
    const moveExists =
        Array.isArray(snapshot.moves) &&
        snapshot.moves.length > plyIndex &&
        typeof snapshot.moves[plyIndex] === 'string' &&
        Boolean(snapshot.moves[plyIndex]);
    if (!moveExists) {
        const pending = getPendingEvalRaw(snapshot);
        ensureLength(pending, plyIndex + 1);
        pending[plyIndex] = value;
        if (debug && mateLike) {
            console.debug('[LiveEval] queued pending eval (mate-like)', {
                initial_sfen: snapshot.initial_sfen ?? null,
                current_ply: snapshot.current_ply ?? null,
                plyIndex,
                eval: value,
                update_type: update.type ?? null,
            });
        }
        return;
    }
    if (debug && mateLike) {
        const moverIsBlack = computeMoverIsBlack(snapshot.initial_sfen, plyIndex, normalizeSFEN);
        console.debug('[LiveEval] commit eval to history (mate-like)', {
            initial_sfen: snapshot.initial_sfen ?? null,
            current_ply: snapshot.current_ply ?? null,
            plyIndex,
            eval: value,
            moverIsBlack,
            move: Array.isArray(snapshot.moves) ? (snapshot.moves[plyIndex] ?? null) : null,
        });
    }
    applyEvalAtIndex(snapshot, value, plyIndex, normalizeSFEN);
}

export function updateSearchStatistics(snapshot: WorkerSnapshotRecord, update: WorkerSnapshotUpdate): void {
    const hasSearchUpdate =
        typeof update.depth === 'number' ||
        typeof update.seldepth === 'number' ||
        typeof update.nodes === 'number' ||
        typeof update.time_ms === 'number';

    if (!hasSearchUpdate) {
        return;
    }
    const current_ply = typeof snapshot.current_ply === 'number' ? snapshot.current_ply : 0;
    if (current_ply <= 0) {
        return;
    }

    if (!Array.isArray(snapshot.depth_values)) snapshot.depth_values = [];
    if (!Array.isArray(snapshot.seldepth_values)) snapshot.seldepth_values = [];
    if (!Array.isArray(snapshot.nodes_values)) snapshot.nodes_values = [];
    if (!Array.isArray(snapshot.move_times_ms)) snapshot.move_times_ms = [];
    if (!Array.isArray(snapshot.wall_times_ms)) snapshot.wall_times_ms = [];
    if (!Array.isArray(snapshot.latency_deltas_ms)) snapshot.latency_deltas_ms = [];
    if (!Array.isArray(snapshot.latency_alerts)) snapshot.latency_alerts = [];

    const targetIndex = Math.max(
        0,
        (typeof update.current_ply === 'number' ? update.current_ply : (snapshot.current_ply ?? 1)) - 1,
    );
    ensureLength(snapshot.depth_values, targetIndex + 1);
    ensureLength(snapshot.seldepth_values, targetIndex + 1);
    ensureLength(snapshot.nodes_values, targetIndex + 1);
    ensureLength(snapshot.move_times_ms, targetIndex + 1);
    ensureLength(snapshot.wall_times_ms, targetIndex + 1);
    ensureLength(snapshot.latency_deltas_ms, targetIndex + 1);
    ensureLength(snapshot.latency_alerts, targetIndex + 1);

    if (typeof update.depth === 'number') snapshot.depth_values[targetIndex] = update.depth;
    if (typeof update.seldepth === 'number') snapshot.seldepth_values[targetIndex] = update.seldepth;
    if (typeof update.nodes === 'number') snapshot.nodes_values[targetIndex] = update.nodes;
    if (typeof update.time_ms === 'number') snapshot.move_times_ms[targetIndex] = update.time_ms;
    if (typeof update.wall_time_ms === 'number') snapshot.wall_times_ms[targetIndex] = update.wall_time_ms;
    if (typeof update.latency_ms === 'number') snapshot.latency_deltas_ms[targetIndex] = update.latency_ms;
    if (typeof update.latency_alert === 'boolean') snapshot.latency_alerts[targetIndex] = update.latency_alert;
}
