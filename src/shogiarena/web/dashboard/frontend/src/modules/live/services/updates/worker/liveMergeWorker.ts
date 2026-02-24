/* Web Worker for Live updates: merges gid-stream diffs into per-worker view models.
 *
 * Responsibilities:
 * - Track seq per topic; request snapshot when a gap is detected.
 * - Maintain `worker_idx -> gid` assignments from `live.assignment.*`.
 * - Maintain `gid -> snapshot` state from:
 *   - `live.game.{gid}.moves.diff` (append-only move log)
 *   - `live.game.{gid}.state.diff` (state-like updates)
 *   - `live.game.{gid}.snapshot` (authoritative recovery)
 * - Emit compact per-worker view models to the main thread to minimize postMessage cost.
 */

import { mergeWorkerSnapshotMutable } from './snapshotMerge';
import { createEmptyWorkerSnapshot } from '@/modules/live/utils';
import type { WorkerSnapshotRecord, WorkerSnapshotUpdate } from '@/modules/live/types/updates';

type LiveEnvelope = {
    topic: string;
    seq?: number;
    payload?: unknown;
};

type WorkerDiffPatch = {
    currentPly?: number;
    move?: string;
    ki2_move?: string;
    eval?: number | null;
    eval_cp?: number | null;
    depth?: number | null;
    seldepth?: number | null;
    nodes?: number | null;
    time_ms?: number | null;
    wall_time_ms?: number | null;
    latency_ms?: number | null;
    latency_alert?: boolean | null;
    clock?: Record<string, unknown> | null;
    active?: string | null;
    side?: string | null;
    black_remain_ms?: number | null;
    white_remain_ms?: number | null;
    applied_increment_ms?: number | null;
    occurred_at_ms?: number | null;
    started_at_ms?: number | null;
    pre_black_remain_ms?: number | null;
    pre_white_remain_ms?: number | null;
    byoyomi_ms_black?: number | null;
    byoyomi_ms_white?: number | null;
    increment_ms_black?: number | null;
    increment_ms_white?: number | null;
    black_name?: string | null;
    white_name?: string | null;
    initial_sfen?: string | null;
    sfen?: string | null;
    time_control_black?: unknown;
    time_control_white?: unknown;
    [key: string]: unknown;
};

type AssignmentPayload = {
    assignments?: Record<string, string | null | undefined>;
    gids?: unknown;
};

type GameSnapshotEnvelopePayload = {
    gid?: unknown;
    snapshot?: unknown;
};

type GameDiffPayload = {
    gid?: unknown;
    kind?: unknown;
    type?: unknown;
    patch?: unknown;
};

type ParsedGameDiff = {
    gid: string;
    kind?: string;
    type?: string;
    patch: WorkerDiffPatch;
    historyRev?: number;
    assignmentRev?: number;
};

type WorkerViewModel = {
    workerIdx: number;
    gid: string | null;
    currentPly: number | null;
    sfen: string | null;
    lastMove: string | null;
    ki2Move: string | null;
    evalCp: number | null;
    clock?: Record<string, unknown>;
    snapshot?: WorkerSnapshotRecord;
    snapshotDelta?: WorkerSnapshotUpdate;
};

type SnapshotRequest = { type: 'request_snapshot'; topic: string; fromSeq?: number; reason_code?: number };
type VmMessage = { type: 'vm'; payload: WorkerViewModel };
type AckMessage = SnapshotRequest | VmMessage;

const snapshotsByGid = new Map<string, WorkerSnapshotRecord>();
const assignmentsByWorker = new Map<number, string | null>();
const workersByGid = new Map<string, Set<number>>();
const seqByTopic = new Map<string, number>();
const pendingSnapshotTopics = new Set<string>();
const lastSnapshotRequestFromSeqByTopic = new Map<string, number | null>();
const deferredMovesByGid = new Map<string, Map<number, WorkerSnapshotUpdate>>();
let assignmentReady = false;
let assignmentRev: number | null = null;
// After a reset (e.g., visibility resume), ignore assignment diffs until a snapshot arrives.
// This prevents processing stale replayed diffs that would trigger snapshot requests for old gids.
let awaitingAssignmentSnapshot = false;
const emptySnapshotAtByGid = new Map<string, number>();
const lastReplayFromStartAtByGid = new Map<string, number>();
const assignmentSnapshotTimers = new Map<number, number>();
const pendingAssignmentSnapshotGid = new Map<number, string>();
const assignmentSnapshotDebounceMs = 200;
// Cooldown for FUTURE_MOVE (104) snapshot requests to reduce request storms.
// Note: This does NOT affect MOVE_GAP (106) or MOVE_HOLE (101) detection, which serve
// different purposes (detecting actual array holes vs out-of-order arrivals).
// With server-side move buffering, these should occur less frequently anyway.
const lastFutureMoveSnapshotAtByGid = new Map<string, number>();
const FUTURE_MOVE_SNAPSHOT_COOLDOWN_MS = 400;
const debugEnabled = (() => {
    try {
        const params = new URLSearchParams(self?.location?.search ?? '');
        const flag = params.get('liveDebug');
        if (flag === '1') return true;
    } catch {
        // ignore
    }
    return false;
})();
function debugLog(message: string, details?: Record<string, unknown>): void {
    if (!debugEnabled) return;
    if (details) {
        // eslint-disable-next-line no-console
        console.debug(`[LiveMergeWorker] ${message}`, details);
    } else {
        // eslint-disable-next-line no-console
        console.debug(`[LiveMergeWorker] ${message}`);
    }
}

function summarizeEngineStatus(status: unknown): Record<string, unknown> | null {
    if (!status || typeof status !== 'object') return null;
    const raw = status as Record<string, unknown>;
    const summarizeRole = (role: 'black' | 'white') => {
        const entry = raw[role] as Record<string, unknown> | undefined;
        if (!entry || typeof entry !== 'object') return { count: 0, last: null };
        const tail = Array.isArray(entry.io_tail) ? entry.io_tail : [];
        const last = tail.length ? tail[tail.length - 1] : null;
        const lastLine =
            last && typeof last === 'object' && 'line' in (last as Record<string, unknown>)
                ? (last as Record<string, unknown>).line
                : null;
        return { count: tail.length, last: lastLine ?? null };
    };
    return {
        black: summarizeRole('black'),
        white: summarizeRole('white'),
    };
}

const SNAPSHOT_REASON_WORKER_MOVE_HOLE = 101;
const SNAPSHOT_REASON_WORKER_SEQ_GAP = 102;
const SNAPSHOT_REASON_WORKER_ASSIGNMENT = 103;
const SNAPSHOT_REASON_WORKER_FUTURE_MOVE = 104;
const SNAPSHOT_REASON_WORKER_HISTORY_REV = 105;
const SNAPSHOT_REASON_WORKER_MOVE_GAP = 106;
let lastAssignmentSnapshotRequestAt: number | null = null;

type ParsedGameTopic = {
    gid: string;
    kind: 'diff' | 'snapshot';
    stream?: 'moves' | 'analysis' | 'state' | 'legacy';
};

function applyWsSeqMeta(snapshot: WorkerSnapshotRecord, topic: string, seq: number | undefined): void {
    const rec = snapshot as unknown as Record<string, unknown>;
    rec.__ws_last_topic = topic;
    if (typeof seq !== 'number' || !Number.isFinite(seq)) return;

    const n = Math.trunc(seq);
    if (topic.endsWith('.moves.diff')) {
        rec.__ws_seq_moves = n;
        return;
    }
    if (topic.endsWith('.analysis.diff')) {
        rec.__ws_seq_analysis = n;
        return;
    }
    if (topic.endsWith('.state.diff')) {
        rec.__ws_seq_state = n;
        return;
    }
    if (topic.endsWith('.snapshot')) {
        rec.__ws_seq_snapshot = n;
        return;
    }
    if (topic.endsWith('.diff')) {
        // Legacy stream: can contain both moves and state-like updates.
        rec.__ws_seq_moves = n;
        rec.__ws_seq_state = n;
    }
}

function gameMovesDiffTopic(gid: string): string {
    return `live.game.${gid}.moves.diff`;
}

function gameAnalysisDiffTopic(gid: string): string {
    return `live.game.${gid}.analysis.diff`;
}

function gameStateDiffTopic(gid: string): string {
    return `live.game.${gid}.state.diff`;
}

function gameSnapshotTopic(gid: string): string {
    return `live.game.${gid}.snapshot`;
}

function isMovesDiffTopic(topic: string): boolean {
    return topic.endsWith('.moves.diff');
}

function isAnalysisDiffTopic(topic: string): boolean {
    return topic.endsWith('.analysis.diff');
}

function isStateDiffTopic(topic: string): boolean {
    return topic.endsWith('.state.diff');
}

function isLegacyDiffTopic(topic: string): boolean {
    return (
        topic.endsWith('.diff') && !isMovesDiffTopic(topic) && !isAnalysisDiffTopic(topic) && !isStateDiffTopic(topic)
    );
}

function isStrictTopic(topic: string): boolean {
    return isMovesDiffTopic(topic) || isLegacyDiffTopic(topic);
}

function countContiguousMoves(snapshot: WorkerSnapshotRecord | null | undefined): number;
function countContiguousMoves(values: unknown): number;
function countContiguousMoves(value: unknown): number {
    const maybeSnapshot = value as WorkerSnapshotRecord | null | undefined;
    const moves =
        maybeSnapshot && typeof maybeSnapshot === 'object' && Array.isArray(maybeSnapshot.moves)
            ? maybeSnapshot.moves
            : Array.isArray(value)
              ? (value as unknown[])
              : [];
    let count = 0;
    for (let i = 0; i < moves.length; i += 1) {
        const mv = String((moves as unknown[])[i] ?? '').trim();
        if (!mv) break;
        count += 1;
    }
    return count;
}

function maybeRequestSnapshotOnMoveHoles(topic: string, snapshot: WorkerSnapshotRecord): void {
    // Only the move stream is "hole-sensitive".
    if (!isMovesDiffTopic(topic)) return;
    if (pendingSnapshotTopics.has(topic)) return;
    const contiguous = countContiguousMoves(snapshot);
    const totalLen = Array.isArray(snapshot.moves) ? snapshot.moves.length : 0;

    const cur = (snapshot.currentPly ?? snapshot.ply) as number | undefined;
    const currentPly = typeof cur === 'number' && Number.isFinite(cur) ? Math.max(0, Math.trunc(cur)) : 0;

    // We only want to trigger when there is evidence of a hole (missing move) while later moves exist.
    // `totalLen > contiguous + 1` implies there is at least one non-empty move beyond the first hole.
    const holeDetected = totalLen > contiguous + 1;
    const plyRunsAhead = currentPly > contiguous + 1;
    if (!holeDetected && !plyRunsAhead) return;

    pendingSnapshotTopics.add(topic);
    postAck({
        type: 'request_snapshot',
        topic,
        fromSeq: seqByTopic.get(topic) ?? 0,
        reason_code: SNAPSHOT_REASON_WORKER_MOVE_GAP,
    });
}

function maybeRequestSnapshotOnMoveGap(topic: string, snapshot: WorkerSnapshotRecord): void {
    if (!isMovesDiffTopic(topic)) return;
    if (pendingSnapshotTopics.has(topic)) return;
    const meta = snapshot as unknown as { __move_gap_detected_ply?: unknown };
    const gapPlyRaw = meta.__move_gap_detected_ply;
    const gapPly = typeof gapPlyRaw === 'number' && Number.isFinite(gapPlyRaw) ? Math.max(0, Math.trunc(gapPlyRaw)) : 0;
    if (gapPly <= 0) return;
    const contiguous = countContiguousMoves(snapshot);
    // If we already caught up past the gap, clear the marker.
    if (contiguous >= gapPly) {
        delete (snapshot as unknown as Record<string, unknown>).__move_gap_detected_ply;
        return;
    }
    pendingSnapshotTopics.add(topic);
    postAck({
        type: 'request_snapshot',
        topic,
        fromSeq: seqByTopic.get(topic) ?? 0,
        reason_code: SNAPSHOT_REASON_WORKER_MOVE_HOLE,
    });
}

function hasMoveDelta(update: WorkerSnapshotUpdate | null | undefined): boolean {
    if (!update) return false;
    const move = (update as { move?: unknown }).move;
    const ki2 = (update as { ki2_move?: unknown }).ki2_move;
    return (typeof move === 'string' && move.trim().length > 0) || (typeof ki2 === 'string' && ki2.trim().length > 0);
}

function shouldDeferFutureMove(update: WorkerSnapshotUpdate, snapshot: WorkerSnapshotRecord): boolean {
    if (!hasMoveDelta(update)) return false;
    const flag = (update as unknown as { __ply_from_patch?: unknown }).__ply_from_patch;
    const plyFromPatch = flag !== false;
    // If we don't know which ply this move belongs to, applying it "at the end" can permanently
    // corrupt history under backpressure or when gaps exist. Force a snapshot resync instead.
    if (!plyFromPatch) return true;
    const plyRaw = (update as { currentPly?: unknown }).currentPly;
    const ply = typeof plyRaw === 'number' && Number.isFinite(plyRaw) ? Math.max(0, Math.trunc(plyRaw)) : 0;
    if (ply <= 0) return false;
    const movesLen = Array.isArray(snapshot.moves) ? snapshot.moves.length : 0;
    // If the move claims to be for ply N, we must already have at least N-1 moves in the array.
    // Otherwise we'd create a hole (or be forced to skip), so defer and request a snapshot.
    if (ply - 1 > movesLen) return true;
    const contiguous = countContiguousMoves(snapshot);
    // If the server tells us "this move is for ply N" but we don't have ply N-1 yet, treat it as out-of-order.
    return ply > contiguous + 1;
}

function stripMoveFields(update: WorkerSnapshotUpdate): WorkerSnapshotUpdate {
    const next: WorkerSnapshotUpdate = { ...update };
    delete (next as Record<string, unknown>).move;
    delete (next as Record<string, unknown>).ki2_move;
    delete (next as Record<string, unknown>).currentPly;
    return next;
}

function hasAssignedWorkers(gid: string): boolean {
    const workers = workersByGid.get(gid);
    return Boolean(workers && workers.size > 0);
}

function cleanupUnreferencedGid(gid: string): void {
    snapshotsByGid.delete(gid);
    deferredMovesByGid.delete(gid);
    emptySnapshotAtByGid.delete(gid);
    lastReplayFromStartAtByGid.delete(gid);
    lastFutureMoveSnapshotAtByGid.delete(gid);
    const topics = [
        gameMovesDiffTopic(gid),
        gameAnalysisDiffTopic(gid),
        gameStateDiffTopic(gid),
        gameSnapshotTopic(gid),
        // Legacy
        `live.game.${gid}.diff`,
    ];
    for (const topic of topics) {
        seqByTopic.delete(topic);
        pendingSnapshotTopics.delete(topic);
        lastSnapshotRequestFromSeqByTopic.delete(topic);
    }
}

function clearPending(topic: string): void {
    pendingSnapshotTopics.delete(topic);
    const parsed = parseGameTopic(topic);
    if (parsed?.kind === 'snapshot') {
        pendingSnapshotTopics.delete(gameMovesDiffTopic(parsed.gid));
        pendingSnapshotTopics.delete(gameAnalysisDiffTopic(parsed.gid));
        pendingSnapshotTopics.delete(gameStateDiffTopic(parsed.gid));
        pendingSnapshotTopics.delete(`live.game.${parsed.gid}.diff`); // legacy
        return;
    }
    if (parsed?.kind === 'diff') {
        pendingSnapshotTopics.delete(gameSnapshotTopic(parsed.gid));
    }
}

function postAck(message: AckMessage): void {
    postMessage(message);
}

function maybeRequestAssignmentSnapshot(): void {
    const now = Date.now();
    if (lastAssignmentSnapshotRequestAt != null && now - lastAssignmentSnapshotRequestAt < 1000) {
        return;
    }
    lastAssignmentSnapshotRequestAt = now;
    debugLog('request assignment snapshot');
    postAck({
        type: 'request_snapshot',
        topic: 'live.assignment.snapshot',
        reason_code: SNAPSHOT_REASON_WORKER_ASSIGNMENT,
    });
}

function processEnvelopeImmediately(envelope: LiveEnvelope): void {
    const { topic = '', seq, payload } = envelope;

    // Strict Sequence Update: Only update when we actually process it.
    if (typeof seq === 'number') {
        seqByTopic.set(topic, seq);
    }

    if (topic.startsWith('live.assignment.')) {
        if (topic.endsWith('.snapshot') || topic.endsWith('.diff')) {
            handleAssignmentSnapshot(topic, payload);
        }
        return;
    }

    const parsedGame = parseGameTopic(topic);
    if (!parsedGame) return;

    if (!hasAssignedWorkers(parsedGame.gid)) {
        seqByTopic.delete(topic);
        pendingSnapshotTopics.delete(topic);
        clearPending(topic);
        return;
    }

    if (parsedGame.kind === 'snapshot') {
        handleGameSnapshot(topic, parsedGame.gid, payload);
    } else if (parsedGame.kind === 'diff') {
        handleGameDiff(topic, parsedGame.gid, payload);
    }
}

function shouldIgnoreIncomingSnapshot(
    existing: WorkerSnapshotRecord | undefined,
    incoming: WorkerSnapshotRecord,
): boolean {
    if (!existing) return false;
    const existingGid = existing.game_id ?? null;
    const incomingGid = incoming.game_id ?? null;
    if (!existingGid || !incomingGid || existingGid !== incomingGid) {
        return false;
    }
    // NOTE:
    // `live.game.{gid}.snapshot` is backed by the dashboard server's stored snapshot cache, which may lag
    // behind the diff stream (especially under load). Also, WS request_snapshot replies replay diffs
    // first, then send this cached snapshot.
    //
    // If we unconditionally replace the merged state with an older snapshot, we create "missing move"
    // holes, desync side-to-move, and eventually surface as illegal USI sequences on the client.
    //
    // Strategy:
    // - If the incoming snapshot is clearly a strict prefix of what we already have, treat it as stale
    //   and ignore it (we'll still merge missing metadata/prefix holes elsewhere).
    // - If history diverges, allow it (it could be an actual correction/Undo reflected in snapshot).

    const existingMoves = Array.isArray(existing.moves) ? existing.moves : [];
    const incomingMoves = Array.isArray(incoming.moves) ? incoming.moves : [];
    const existingContiguous = countContiguousMoves(existingMoves);
    const incomingContiguous = countContiguousMoves(incomingMoves);

    const existingPlyRaw = existing.currentPly ?? (existing as unknown as { ply?: unknown }).ply;
    const incomingPlyRaw = incoming.currentPly ?? (incoming as unknown as { ply?: unknown }).ply;
    const existingPly =
        typeof existingPlyRaw === 'number' && Number.isFinite(existingPlyRaw)
            ? Math.max(0, Math.trunc(existingPlyRaw))
            : 0;
    const incomingPly =
        typeof incomingPlyRaw === 'number' && Number.isFinite(incomingPlyRaw)
            ? Math.max(0, Math.trunc(incomingPlyRaw))
            : 0;

    const behind =
        incomingPly < existingPly ||
        incomingContiguous < existingContiguous ||
        incomingMoves.length < existingMoves.length;
    if (!behind) return false;

    const upto = Math.max(0, incomingContiguous);
    for (let i = 0; i < upto; i += 1) {
        const a = String((existingMoves as unknown[])[i] ?? '').trim();
        const b = String((incomingMoves as unknown[])[i] ?? '').trim();
        if (a !== b) {
            // Divergence: treat snapshot as potentially corrective.
            return false;
        }
    }
    // Strict prefix (or empty): stale snapshot; ignore.
    return true;
}

function fillArrayPrefix<T>(
    target: Array<T | null | undefined>,
    source: Array<T | null | undefined>,
    uptoExclusive: number,
    isMissing: (value: T | null | undefined) => boolean,
): void {
    const upto = Math.max(0, Math.min(uptoExclusive, target.length, source.length));
    for (let i = 0; i < upto; i += 1) {
        const cur = target[i];
        if (!isMissing(cur)) continue;
        const next = source[i];
        if (isMissing(next)) continue;
        target[i] = next as T;
    }
}

function mergeStaleSnapshotIntoExisting(existing: WorkerSnapshotRecord, incoming: WorkerSnapshotRecord): void {
    // Only fill missing metadata / prefix holes. Never regress ply or truncate arrays.
    if (!existing.initial_sfen && incoming.initial_sfen) existing.initial_sfen = incoming.initial_sfen;
    if (!existing.sfen && incoming.sfen) existing.sfen = incoming.sfen;
    if (!existing.black_name && incoming.black_name) existing.black_name = incoming.black_name;
    if (!existing.white_name && incoming.white_name) existing.white_name = incoming.white_name;
    if (!existing.time_control_black && incoming.time_control_black)
        existing.time_control_black = incoming.time_control_black;
    if (!existing.time_control_white && incoming.time_control_white)
        existing.time_control_white = incoming.time_control_white;

    const missingString = (v: string | null | undefined) => v == null || v === '';
    const missingNum = (v: number | null | undefined) => v == null || Number.isNaN(v);
    const missingBool = (v: boolean | null | undefined) => v == null;

    if (Array.isArray(existing.moves) && Array.isArray(incoming.moves)) {
        fillArrayPrefix(
            existing.moves as Array<string | null | undefined>,
            incoming.moves as Array<string | null | undefined>,
            incoming.moves.length,
            missingString,
        );
    }
    if (Array.isArray(existing.ki2_moves) && Array.isArray(incoming.ki2_moves)) {
        fillArrayPrefix(
            existing.ki2_moves as Array<string | null | undefined>,
            incoming.ki2_moves as Array<string | null | undefined>,
            incoming.ki2_moves.length,
            missingString,
        );
    }
    if (Array.isArray(existing.eval_black) && Array.isArray(incoming.eval_black)) {
        fillArrayPrefix(
            existing.eval_black as Array<number | null | undefined>,
            incoming.eval_black as Array<number | null | undefined>,
            incoming.eval_black.length,
            missingNum,
        );
    }
    if (Array.isArray(existing.eval_white) && Array.isArray(incoming.eval_white)) {
        fillArrayPrefix(
            existing.eval_white as Array<number | null | undefined>,
            incoming.eval_white as Array<number | null | undefined>,
            incoming.eval_white.length,
            missingNum,
        );
    }
    if (Array.isArray(existing.nodes_values) && Array.isArray(incoming.nodes_values)) {
        fillArrayPrefix(
            existing.nodes_values as Array<number | null | undefined>,
            incoming.nodes_values as Array<number | null | undefined>,
            incoming.nodes_values.length,
            missingNum,
        );
    }
    if (Array.isArray(existing.depth_values) && Array.isArray(incoming.depth_values)) {
        fillArrayPrefix(
            existing.depth_values as Array<number | null | undefined>,
            incoming.depth_values as Array<number | null | undefined>,
            incoming.depth_values.length,
            missingNum,
        );
    }
    if (Array.isArray(existing.seldepth_values) && Array.isArray(incoming.seldepth_values)) {
        fillArrayPrefix(
            existing.seldepth_values as Array<number | null | undefined>,
            incoming.seldepth_values as Array<number | null | undefined>,
            incoming.seldepth_values.length,
            missingNum,
        );
    }
    if (Array.isArray(existing.move_times_ms) && Array.isArray(incoming.move_times_ms)) {
        fillArrayPrefix(
            existing.move_times_ms as Array<number | null | undefined>,
            incoming.move_times_ms as Array<number | null | undefined>,
            incoming.move_times_ms.length,
            missingNum,
        );
    }
    if (Array.isArray(existing.wall_times_ms) && Array.isArray(incoming.wall_times_ms)) {
        fillArrayPrefix(
            existing.wall_times_ms as Array<number | null | undefined>,
            incoming.wall_times_ms as Array<number | null | undefined>,
            incoming.wall_times_ms.length,
            missingNum,
        );
    }
    if (Array.isArray(existing.latency_deltas_ms) && Array.isArray(incoming.latency_deltas_ms)) {
        fillArrayPrefix(
            existing.latency_deltas_ms as Array<number | null | undefined>,
            incoming.latency_deltas_ms as Array<number | null | undefined>,
            incoming.latency_deltas_ms.length,
            missingNum,
        );
    }
    if (Array.isArray(existing.latency_alerts) && Array.isArray(incoming.latency_alerts)) {
        fillArrayPrefix(
            existing.latency_alerts as Array<boolean | null | undefined>,
            incoming.latency_alerts as Array<boolean | null | undefined>,
            incoming.latency_alerts.length,
            missingBool,
        );
    }
}

function drainDeferredMovesForGid(gid: string, snapshot: WorkerSnapshotRecord): WorkerSnapshotRecord {
    const deferred = deferredMovesByGid.get(gid);
    if (!deferred || deferred.size === 0) return snapshot;

    let merged = snapshot;
    while (true) {
        const contiguous = countContiguousMoves(merged);
        const nextPly = contiguous + 1;
        const update = deferred.get(nextPly);
        if (!update) break;
        deferred.delete(nextPly);
        merged = mergeWorkerSnapshotMutable(merged, update);
    }
    if (deferred.size === 0) {
        deferredMovesByGid.delete(gid);
    }
    return merged;
}

function parseGameTopic(topic: string): ParsedGameTopic | null {
    if (!topic.startsWith('live.game.')) return null;
    if (topic.endsWith('.moves.diff')) {
        const gid = topic.slice('live.game.'.length, -'.moves.diff'.length).trim();
        return gid ? { gid, kind: 'diff', stream: 'moves' } : null;
    }
    if (topic.endsWith('.analysis.diff')) {
        const gid = topic.slice('live.game.'.length, -'.analysis.diff'.length).trim();
        return gid ? { gid, kind: 'diff', stream: 'analysis' } : null;
    }
    if (topic.endsWith('.state.diff')) {
        const gid = topic.slice('live.game.'.length, -'.state.diff'.length).trim();
        return gid ? { gid, kind: 'diff', stream: 'state' } : null;
    }
    if (topic.endsWith('.snapshot')) {
        const gid = topic.slice('live.game.'.length, -'.snapshot'.length).trim();
        return gid ? { gid, kind: 'snapshot' } : null;
    }
    if (topic.endsWith('.diff')) {
        // Legacy compatibility (should be phased out once all producers use moves/state split).
        const gid = topic.slice('live.game.'.length, -'.diff'.length).trim();
        return gid ? { gid, kind: 'diff', stream: 'legacy' } : null;
    }
    return null;
}

function applyPatch(target: WorkerSnapshotRecord, patch: Record<string, unknown>): WorkerSnapshotRecord {
    const next: WorkerSnapshotRecord = { ...target };

    const applyOp = (root: WorkerSnapshotRecord, path: string[], op: 'set' | 'delete', value: unknown): void => {
        if (path.length === 0) return;
        const [head, ...rest] = path;
        if (rest.length === 0) {
            if (op === 'delete') {
                delete root[head];
            } else {
                root[head] = value;
            }
            return;
        }
        const existing = root[head];
        const child: Record<string, unknown> =
            existing && typeof existing === 'object' && !Array.isArray(existing) ? { ...(existing as object) } : {};
        root[head] = child;
        const tail = rest[0];
        if (!tail) return;
        if (rest.length === 1) {
            if (op === 'delete') {
                delete child[tail];
            } else {
                child[tail] = value;
            }
        }
    };

    for (const [key, value] of Object.entries(patch)) {
        if (key === '__apply__' && typeof value === 'object' && value) {
            const op = (value as { op?: string }).op;
            const path = Array.isArray((value as { path?: unknown }).path) ? (value as { path: string[] }).path : null;
            const val = (value as { value?: unknown }).value;
            if (!op || !path || path.length === 0) {
                continue;
            }
            if (op === 'delete' || op === 'set') {
                applyOp(next, path, op, val);
            }
            continue;
        }
        next[key] = value;
    }
    return next;
}

// exposed for unit tests
export const __testApplyPatch = applyPatch;

function sanitizeSnapshot(raw: unknown): WorkerSnapshotRecord | null {
    if (!raw || typeof raw !== 'object') return null;
    const snapshot = raw as WorkerSnapshotRecord;
    const ensureArray = <T>(value: unknown): T[] => (Array.isArray(value) ? [...(value as T[])] : []);
    const takeContiguousStringPrefix = (value: unknown): string[] => {
        if (!Array.isArray(value)) return [];
        const out: string[] = [];
        for (const item of value) {
            if (typeof item !== 'string') break;
            const trimmed = item.trim();
            if (!trimmed) break;
            out.push(trimmed);
        }
        return out;
    };
    const takeContiguousMoveObjects = (
        value: unknown,
    ): { moves: string[]; analysis: Array<Record<string, unknown> | null> } => {
        if (!Array.isArray(value)) return { moves: [], analysis: [] };
        const moves: string[] = [];
        const analysis: Array<Record<string, unknown> | null> = [];
        for (const item of value) {
            if (!item || typeof item !== 'object' || Array.isArray(item)) break;
            const obj = item as Record<string, unknown>;
            const usiRaw = obj.usi ?? obj.move;
            if (typeof usiRaw !== 'string' || !usiRaw.trim()) break;
            moves.push(usiRaw.trim());
            const finalRaw = obj.analysis_final ?? obj.analysis;
            analysis.push(
                finalRaw && typeof finalRaw === 'object' && !Array.isArray(finalRaw)
                    ? (finalRaw as Record<string, unknown>)
                    : null,
            );
        }
        return { moves, analysis };
    };
    const truncate = <T>(value: T[], length: number): T[] => {
        const n = Math.max(0, Math.trunc(length));
        return value.length > n ? value.slice(0, n) : value;
    };
    const base = createEmptyWorkerSnapshot() as WorkerSnapshotRecord;
    const rawMoves = (snapshot as unknown as { moves?: unknown }).moves;
    const objectMoves = takeContiguousMoveObjects(rawMoves);
    const moves =
        objectMoves.moves.length > 0
            ? objectMoves.moves
            : takeContiguousStringPrefix((snapshot as { moves?: unknown }).moves);
    const ki2Moves = takeContiguousStringPrefix((snapshot as unknown as { ki2_moves?: unknown }).ki2_moves);

    const currentPlyRaw = (snapshot.currentPly ?? (snapshot as unknown as { ply?: unknown }).ply) as unknown;
    const currentPly =
        typeof currentPlyRaw === 'number' && Number.isFinite(currentPlyRaw)
            ? Math.max(0, Math.trunc(currentPlyRaw))
            : 0;
    const clampedPly = Math.min(currentPly, moves.length);

    const evalBlack = truncate(ensureArray<number | null>(snapshot.eval_black), moves.length);
    const evalWhite = truncate(ensureArray<number | null>(snapshot.eval_white), moves.length);
    const nodesValues = truncate(ensureArray<number | null>(snapshot.nodes_values), moves.length);
    const depthValues = truncate(ensureArray<number | null>(snapshot.depth_values), moves.length);
    const seldepthValues = truncate(ensureArray<number | null>(snapshot.seldepth_values), moves.length);
    const moveTimesMs = truncate(ensureArray<number | null>(snapshot.move_times_ms), moves.length);
    const stateRaw = (snapshot as unknown as { state?: unknown }).state;
    const state =
        stateRaw && typeof stateRaw === 'object' && !Array.isArray(stateRaw)
            ? (stateRaw as Record<string, unknown>)
            : null;

    if (objectMoves.moves.length > 0 && objectMoves.analysis.length > 0) {
        for (let i = 0; i < objectMoves.analysis.length; i += 1) {
            const info = objectMoves.analysis[i];
            if (!info) continue;
            if (typeof info.nodes === 'number' && Number.isFinite(info.nodes)) {
                nodesValues[i] = info.nodes;
            }
            if (typeof info.depth === 'number' && Number.isFinite(info.depth)) {
                depthValues[i] = info.depth;
            }
            if (typeof info.seldepth === 'number' && Number.isFinite(info.seldepth)) {
                seldepthValues[i] = info.seldepth;
            }
            if (typeof info.time_ms === 'number' && Number.isFinite(info.time_ms)) {
                moveTimesMs[i] = info.time_ms;
            }
            if (typeof info.eval === 'number' && Number.isFinite(info.eval)) {
                // analysis_final.eval is already from black's perspective (server normalizes it).
                // No further sign conversion needed - just use it directly.
                const value = Number(info.eval);
                evalBlack[i] = value;
                evalWhite[i] = -value;
            }
        }
    }

    const nextClock =
        state && typeof state.clock === 'object' && state.clock && !Array.isArray(state.clock)
            ? (state.clock as Record<string, unknown>)
            : snapshot.clock;

    const normalizeNumber = (value: unknown): number | null =>
        typeof value === 'number' && Number.isFinite(value) ? value : null;
    const endReasonRaw = state?.end_reason ?? snapshot.end_reason;
    const endReason = typeof endReasonRaw === 'string' ? endReasonRaw : undefined;
    const metaRaw = state?.meta ?? snapshot.meta;
    const meta =
        metaRaw && typeof metaRaw === 'object' && !Array.isArray(metaRaw)
            ? (metaRaw as Record<string, unknown>)
            : snapshot.meta;

    return {
        ...base,
        ...snapshot,
        moves,
        ki2_moves: ki2Moves,
        currentPly: clampedPly,
        eval_black: evalBlack,
        eval_white: evalWhite,
        nodes_values: nodesValues,
        depth_values: depthValues,
        seldepth_values: seldepthValues,
        move_times_ms: moveTimesMs,
        wall_times_ms: truncate(ensureArray<number | null>(snapshot.wall_times_ms), moves.length),
        latency_deltas_ms: truncate(ensureArray<number | null>(snapshot.latency_deltas_ms), moves.length),
        latency_alerts: truncate(
            Array.isArray(snapshot.latency_alerts)
                ? snapshot.latency_alerts.map((v) => (typeof v === 'boolean' ? v : Boolean(v)))
                : [],
            moves.length,
        ),
        clock: nextClock,
        result_code: normalizeNumber(state?.result_code ?? snapshot.result_code),
        end_reason: endReason,
        meta,
    };
}

function buildViewModel(
    workerIdx: number,
    snapshot: WorkerSnapshotRecord,
    options?: { includeSnapshot?: boolean; snapshotDelta?: WorkerSnapshotUpdate | null },
): WorkerViewModel {
    const gid = (snapshot.gid ?? snapshot.game_id ?? snapshot.gameId) as string | undefined;
    const currentPly = (snapshot.currentPly ?? snapshot.ply) as number | undefined;
    const sfen = (snapshot.sfen ?? snapshot.initial_sfen) as string | undefined;
    const moves = Array.isArray(snapshot.moves) ? (snapshot.moves as unknown[]) : [];
    const ki2Moves = Array.isArray(snapshot.ki2_moves) ? (snapshot.ki2_moves as unknown[]) : [];
    const lastMove = moves.length ? String(moves[moves.length - 1]) : null;
    const lastKi2 = ki2Moves.length ? String(ki2Moves[ki2Moves.length - 1]) : null;
    const evalArr = Array.isArray(snapshot.eval_black) ? snapshot.eval_black : [];
    const evalTail = evalArr.length ? (evalArr as Array<number | null>).slice(-1)[0] : null;
    const clock = typeof snapshot.clock === 'object' ? (snapshot.clock as Record<string, unknown>) : undefined;

    return {
        workerIdx,
        gid: gid ?? null,
        currentPly: Number.isFinite(currentPly) ? (currentPly as number) : null,
        sfen: typeof sfen === 'string' ? sfen : null,
        lastMove,
        ki2Move: lastKi2,
        evalCp: typeof evalTail === 'number' ? evalTail : null,
        clock,
        snapshot: options?.includeSnapshot ? snapshot : undefined,
        snapshotDelta: options?.snapshotDelta ?? undefined,
    };
}

function unwrapGameSnapshotPayload(payload: unknown): { gid: string; snapshot: WorkerSnapshotRecord } | null {
    if (!payload || typeof payload !== 'object') return null;
    const inner = (payload as { payload?: unknown }).payload ?? payload;
    if (!inner || typeof inner !== 'object') return null;
    const env = inner as GameSnapshotEnvelopePayload;
    const gidRaw = env.gid;
    const gid = typeof gidRaw === 'string' || typeof gidRaw === 'number' ? String(gidRaw).trim() : '';
    if (!gid) return null;
    const snapshotRaw = env.snapshot ?? inner;
    const normalized = sanitizeSnapshot(snapshotRaw);
    if (!normalized) return null;
    const revRaw =
        (env as Record<string, unknown>).assignment_rev ??
        (snapshotRaw as Record<string, unknown> | null)?.assignment_rev;
    if (typeof revRaw === 'number' && Number.isFinite(revRaw)) {
        (normalized as Record<string, unknown>).assignment_rev = Math.max(0, Math.trunc(revRaw));
    }
    normalized.game_id = gid;
    return { gid, snapshot: normalized };
}

function unwrapGameDiffPayload(payload: unknown, gidFromTopic: string, topic: string): ParsedGameDiff | null {
    if (!payload || typeof payload !== 'object') return null;
    const inner = (payload as { payload?: unknown }).payload ?? payload;
    if (!inner || typeof inner !== 'object') return null;
    const env = inner as GameDiffPayload & Record<string, unknown>;
    const gidRaw = env.gid;
    const gid = (typeof gidRaw === 'string' || typeof gidRaw === 'number' ? String(gidRaw).trim() : '') || gidFromTopic;
    if (!gid) return null;

    const historyRevRaw = (env as Record<string, unknown>).__history_rev;
    const historyRev =
        typeof historyRevRaw === 'number' && Number.isFinite(historyRevRaw) ? Math.trunc(historyRevRaw) : undefined;
    const assignmentRevRaw = (env as Record<string, unknown>).assignment_rev;
    const assignmentRev =
        typeof assignmentRevRaw === 'number' && Number.isFinite(assignmentRevRaw)
            ? Math.max(0, Math.trunc(assignmentRevRaw))
            : undefined;

    // New protocol: moves.diff
    if (isMovesDiffTopic(topic) && !('patch' in env)) {
        const plyRaw = env.ply ?? env.currentPly;
        const ply = typeof plyRaw === 'number' && Number.isFinite(plyRaw) ? Math.max(0, Math.trunc(plyRaw)) : null;
        const usiRaw = env.usi ?? env.move;
        const usi = typeof usiRaw === 'string' && usiRaw.trim() ? usiRaw.trim() : null;
        const resultCodeRaw = (env as Record<string, unknown>).result_code;
        const resultCode = typeof resultCodeRaw === 'number' && Number.isFinite(resultCodeRaw) ? resultCodeRaw : null;
        if (!usi && resultCode == null) return null;
        if (usi && !ply) return null;
        const analysisFinal =
            env.analysis_final && typeof env.analysis_final === 'object' && !Array.isArray(env.analysis_final)
                ? (env.analysis_final as Record<string, unknown>)
                : null;
        const patch: WorkerDiffPatch = {};
        if (ply != null) patch.currentPly = ply;
        if (usi) patch.move = usi;
        if (resultCode != null) patch.result_code = resultCode;
        if (usi && typeof env.ki2_move === 'string' && env.ki2_move.trim()) {
            patch.ki2_move = env.ki2_move.trim();
        }
        if (usi && typeof env.wall_time_ms === 'number' && Number.isFinite(env.wall_time_ms)) {
            patch.wall_time_ms = env.wall_time_ms;
        }
        if (usi && typeof env.latency_ms === 'number' && Number.isFinite(env.latency_ms)) {
            patch.latency_ms = env.latency_ms;
        }
        if (usi && typeof env.latency_alert === 'boolean') {
            patch.latency_alert = env.latency_alert;
        }
        const sfen = env.sfen;
        if (typeof sfen === 'string' && sfen.trim()) patch.sfen = sfen;
        if (analysisFinal) {
            if (typeof analysisFinal.eval === 'number' && Number.isFinite(analysisFinal.eval)) {
                patch.eval = analysisFinal.eval;
            }
            if (typeof analysisFinal.depth === 'number' && Number.isFinite(analysisFinal.depth)) {
                patch.depth = analysisFinal.depth;
            }
            if (typeof analysisFinal.seldepth === 'number' && Number.isFinite(analysisFinal.seldepth)) {
                patch.seldepth = analysisFinal.seldepth;
            }
            if (typeof analysisFinal.nodes === 'number' && Number.isFinite(analysisFinal.nodes)) {
                patch.nodes = analysisFinal.nodes;
            }
            if (typeof analysisFinal.time_ms === 'number' && Number.isFinite(analysisFinal.time_ms)) {
                patch.time_ms = analysisFinal.time_ms;
            }
            if (typeof analysisFinal.wall_time_ms === 'number' && Number.isFinite(analysisFinal.wall_time_ms)) {
                patch.wall_time_ms = analysisFinal.wall_time_ms;
            }
            if (typeof analysisFinal.latency_ms === 'number' && Number.isFinite(analysisFinal.latency_ms)) {
                patch.latency_ms = analysisFinal.latency_ms;
            }
            if (typeof analysisFinal.latency_alert === 'boolean') {
                patch.latency_alert = analysisFinal.latency_alert;
            }
        }
        return { gid, kind: 'move', patch, historyRev, assignmentRev };
    }

    // New protocol: analysis.diff
    if (isAnalysisDiffTopic(topic) && !('patch' in env)) {
        const plyRaw = env.ply ?? env.currentPly;
        const ply = typeof plyRaw === 'number' && Number.isFinite(plyRaw) ? Math.max(0, Math.trunc(plyRaw)) : null;
        const analysis =
            env.analysis && typeof env.analysis === 'object' && !Array.isArray(env.analysis)
                ? (env.analysis as Record<string, unknown>)
                : null;
        if (!ply || !analysis) return null;
        const patch: WorkerDiffPatch = { currentPly: ply };
        if (typeof analysis.eval === 'number' && Number.isFinite(analysis.eval)) {
            patch.eval = analysis.eval;
        }
        if (typeof analysis.depth === 'number' && Number.isFinite(analysis.depth)) {
            patch.depth = analysis.depth;
        }
        if (typeof analysis.seldepth === 'number' && Number.isFinite(analysis.seldepth)) {
            patch.seldepth = analysis.seldepth;
        }
        if (typeof analysis.nodes === 'number' && Number.isFinite(analysis.nodes)) {
            patch.nodes = analysis.nodes;
        }
        if (typeof analysis.time_ms === 'number' && Number.isFinite(analysis.time_ms)) {
            patch.time_ms = analysis.time_ms;
        }
        return { gid, kind: 'analysis', patch, historyRev, assignmentRev };
    }

    // New protocol: state.diff
    if (isStateDiffTopic(topic) && !('patch' in env)) {
        const patch: WorkerDiffPatch = {};
        const state = env.state && typeof env.state === 'object' && !Array.isArray(env.state) ? env.state : null;
        const clock = (state as Record<string, unknown> | null)?.clock ?? env.clock;
        if (clock && typeof clock === 'object' && !Array.isArray(clock)) {
            patch.clock = clock as Record<string, unknown>;
        }
        const fields = [
            'result_code',
            'end_reason',
            'meta',
            'initial_sfen',
            'sfen',
            'black_name',
            'white_name',
            'engine_status',
        ];
        for (const field of fields) {
            if (env[field] !== undefined) {
                (patch as Record<string, unknown>)[field] = env[field];
            } else if (state && (state as Record<string, unknown>)[field] !== undefined) {
                (patch as Record<string, unknown>)[field] = (state as Record<string, unknown>)[field];
            }
        }
        return { gid, kind: 'state', patch, historyRev, assignmentRev };
    }

    const kindRaw = env.kind;
    const kind = typeof kindRaw === 'string' ? kindRaw : undefined;
    const typeRaw = env.type;
    const type = typeof typeRaw === 'string' ? typeRaw : undefined;
    const patchRaw = env.patch ?? {};
    const patch =
        typeof patchRaw === 'object' && patchRaw && !Array.isArray(patchRaw) ? (patchRaw as WorkerDiffPatch) : {};
    return { gid, kind, type, patch, historyRev, assignmentRev };
}

function buildUpdateFromDiff(
    diff: { gid?: string; kind?: string; type?: string; patch: WorkerDiffPatch },
    base: WorkerSnapshotRecord,
): WorkerSnapshotUpdate | null {
    if (!diff || !diff.patch) return null;
    const patch = diff.patch;
    const numberOrUndefined = (value: unknown): number | undefined =>
        typeof value === 'number' && Number.isFinite(value) ? value : undefined;
    const stringOrUndefined = (value: unknown): string | undefined =>
        typeof value === 'string' && value.trim() ? value : undefined;

    const update: WorkerSnapshotUpdate = {};
    if (diff.gid) update.game_id = diff.gid;
    if (typeof diff.type === 'string' && diff.type) {
        (update as Record<string, unknown>).type = diff.type;
    } else if (diff.kind === 'analysis') {
        // Mark analysis-only diffs so the main thread can avoid full card refreshes.
        (update as Record<string, unknown>).type = 'analysis';
    }

    const currentPly = numberOrUndefined(patch.currentPly);
    (update as Record<string, unknown>).__ply_from_patch = currentPly !== undefined;
    if (currentPly !== undefined) {
        update.currentPly = currentPly;
    } else if (typeof base.currentPly === 'number') {
        update.currentPly = base.currentPly;
    }

    const move = stringOrUndefined(patch.move);
    if (move !== undefined) update.move = move;
    const ki2 = stringOrUndefined(patch.ki2_move);
    if (ki2 !== undefined) update.ki2_move = ki2;

    const evalCandidate = patch.eval_cp ?? patch.eval;
    if (typeof evalCandidate === 'number' && Number.isFinite(evalCandidate)) {
        update.eval = evalCandidate;
    }

    if (Object.hasOwn(patch, 'result_code')) {
        const raw = (patch as Record<string, unknown>).result_code;
        if (raw === null) {
            update.result_code = null;
        } else {
            const resultCode = numberOrUndefined(raw);
            if (resultCode !== undefined) {
                update.result_code = resultCode;
            }
        }
    }
    if (Object.hasOwn(patch, 'engine_status')) {
        const raw = (patch as Record<string, unknown>).engine_status;
        if (raw && typeof raw === 'object' && !Array.isArray(raw)) {
            (update as Record<string, unknown>).engine_status = raw;
        }
    }

    const fields: Array<keyof WorkerDiffPatch> = [
        'depth',
        'seldepth',
        'nodes',
        'time_ms',
        'wall_time_ms',
        'latency_ms',
    ];
    for (const field of fields) {
        const value = numberOrUndefined(patch[field]);
        if (value !== undefined) {
            (update as Record<string, number>)[field] = value;
        }
    }
    if (typeof patch.latency_alert === 'boolean') {
        update.latency_alert = patch.latency_alert;
    }

    const nameFields: Array<keyof WorkerDiffPatch> = ['black_name', 'white_name', 'initial_sfen', 'sfen'];
    for (const field of nameFields) {
        const value = stringOrUndefined(patch[field]);
        if (value !== undefined) {
            (update as Record<string, string>)[field] = value;
        }
    }

    if (typeof patch.time_control_black === 'string') {
        update.time_control_black = patch.time_control_black;
    }
    if (typeof patch.time_control_white === 'string') {
        update.time_control_white = patch.time_control_white;
    }

    const coerceClockField = (value: unknown): unknown => value;
    const clockFromNested =
        patch.clock && typeof patch.clock === 'object' ? (patch.clock as Record<string, unknown>) : null;
    const clockKeys: Array<[keyof WorkerSnapshotUpdate, string]> = [
        ['active', 'active'],
        ['side', 'side'],
        ['black_remain_ms', 'black_remain_ms'],
        ['white_remain_ms', 'white_remain_ms'],
        ['applied_increment_ms', 'applied_increment_ms'],
        ['occurred_at_ms', 'occurred_at_ms'],
        ['started_at_ms', 'started_at_ms'],
        ['pre_black_remain_ms', 'pre_black_remain_ms'],
        ['pre_white_remain_ms', 'pre_white_remain_ms'],
        ['byoyomi_ms_black', 'byoyomi_ms_black'],
        ['byoyomi_ms_white', 'byoyomi_ms_white'],
        ['increment_ms_black', 'increment_ms_black'],
        ['increment_ms_white', 'increment_ms_white'],
        ['time_control_black', 'time_control_black'],
        ['time_control_white', 'time_control_white'],
    ];
    for (const [dest, key] of clockKeys) {
        const direct = (patch as Record<string, unknown>)[key];
        const nested = clockFromNested ? clockFromNested[key] : undefined;
        const val = direct !== undefined ? direct : nested;
        if (val !== undefined) {
            (update as Record<string, unknown>)[dest as string] = coerceClockField(val);
        }
    }

    return update;
}

function handleGameSnapshot(topic: string, gid: string, payload: unknown): void {
    // After reset (e.g., visibility resume), ignore game snapshots until a fresh assignment snapshot
    // arrives. This prevents stale snapshot responses (requested before reset) from corrupting the view.
    if (awaitingAssignmentSnapshot) {
        debugLog('drop game snapshot (awaiting assignment)', { gid, topic });
        clearPending(topic);
        return;
    }
    if (!hasAssignedWorkers(gid)) {
        // Ignore snapshots for unreferenced gids to avoid unbounded growth over long sessions.
        debugLog('drop game snapshot (unassigned gid)', { gid, topic });
        clearPending(topic);
        return;
    }
    const unpacked = unwrapGameSnapshotPayload(payload);
    if (!unpacked || unpacked.gid !== gid) return;
    const incoming = unpacked.snapshot;
    if (assignmentRev != null) {
        const revRaw = (incoming as Record<string, unknown>).assignment_rev;
        const rev = typeof revRaw === 'number' && Number.isFinite(revRaw) ? Math.trunc(revRaw) : null;
        if (rev !== null && rev !== assignmentRev) {
            debugLog('drop game snapshot (assignment rev mismatch)', {
                gid,
                incoming: rev,
                current: assignmentRev,
            });
            maybeRequestAssignmentSnapshot();
            return;
        }
    }
    const existing = snapshotsByGid.get(gid);
    if (shouldIgnoreIncomingSnapshot(existing, incoming)) {
        if (existing) {
            mergeStaleSnapshotIntoExisting(existing, incoming);
            applyWsSeqMeta(existing, topic, seqByTopic.get(topic));
            clearPending(topic);
            const workers = workersByGid.get(gid);
            if (workers) {
                for (const workerIdx of workers) {
                    const vm = buildViewModel(workerIdx, existing, { includeSnapshot: true, snapshotDelta: null });
                    postAck({ type: 'vm', payload: vm });
                }
            }
            return;
        }
        clearPending(topic);
        return;
    }
    // Discard pending deferred moves for this gid - snapshot represents the authoritative state.
    // This prevents "stop-go" lag caused by applying stale diffs accumulated during snapshot fetch.
    deferredMovesByGid.delete(gid);
    applyWsSeqMeta(incoming, topic, seqByTopic.get(topic));
    snapshotsByGid.set(gid, incoming);
    const movesLen = Array.isArray(incoming.moves) ? countContiguousMoves(incoming.moves) : 0;
    if (movesLen <= 0) {
        emptySnapshotAtByGid.set(gid, Date.now());
    } else {
        emptySnapshotAtByGid.delete(gid);
        lastReplayFromStartAtByGid.delete(gid);
    }
    clearPending(topic);

    const workers = workersByGid.get(gid);
    if (!workers) return;
    for (const workerIdx of workers) {
        const vm = buildViewModel(workerIdx, incoming, { includeSnapshot: true, snapshotDelta: null });
        postAck({ type: 'vm', payload: vm });
    }
    const statusSummary = summarizeEngineStatus((incoming as { engine_status?: unknown }).engine_status);
    debugLog('apply game snapshot', {
        gid,
        workers: Array.from(workers),
        engine_status: statusSummary,
    });
}

function handleGameDiff(topic: string, gid: string, payload: unknown): void {
    // After reset (e.g., visibility resume), ignore game diffs until a fresh assignment snapshot
    // arrives. This prevents stale diff responses from corrupting the view.
    if (awaitingAssignmentSnapshot) {
        debugLog('drop game diff (awaiting assignment)', { gid, topic });
        clearPending(topic);
        return;
    }
    if (!hasAssignedWorkers(gid)) {
        // Ignore diffs for unreferenced gids to avoid retaining completed/rotated games indefinitely.
        debugLog('drop game diff (unassigned gid)', { gid, topic });
        clearPending(topic);
        return;
    }
    const diff = unwrapGameDiffPayload(payload, gid, topic);
    if (!diff) return;
    if (assignmentRev != null) {
        if (diff.assignmentRev == null || diff.assignmentRev !== assignmentRev) {
            debugLog('drop game diff (assignment rev mismatch)', {
                gid,
                incoming: diff.assignmentRev ?? null,
                current: assignmentRev,
            });
            maybeRequestAssignmentSnapshot();
            return;
        }
    }
    const existing = snapshotsByGid.get(gid) ?? (createEmptyWorkerSnapshot() as unknown as WorkerSnapshotRecord);
    const existingRevRaw = (existing as unknown as { __history_rev?: unknown }).__history_rev;
    const existingRev =
        typeof existingRevRaw === 'number' && Number.isFinite(existingRevRaw) ? Math.trunc(existingRevRaw) : null;
    if (typeof diff.historyRev === 'number' && existingRev != null && diff.historyRev !== existingRev) {
        if (diff.kind === 'analysis') {
            return;
        }
        const snapshotTopic = gameSnapshotTopic(gid);
        if (!pendingSnapshotTopics.has(snapshotTopic)) {
            pendingSnapshotTopics.add(snapshotTopic);
            postAck({
                type: 'request_snapshot',
                topic: snapshotTopic,
                reason_code: SNAPSHOT_REASON_WORKER_HISTORY_REV,
            });
        }
        return;
    }
    if (diff.kind === 'analysis') {
        // During catch-up, analysis-only diffs (eval/depth/nodes) can be misleading because moves may be missing.
        // Skip them while awaiting a snapshot response for the gid stream.
        if (
            pendingSnapshotTopics.has(gameMovesDiffTopic(gid)) ||
            pendingSnapshotTopics.has(gameSnapshotTopic(gid)) ||
            pendingSnapshotTopics.has(`live.game.${gid}.diff`)
        ) {
            return;
        }
        const movesLen = countContiguousMoves(existing);
        const plyRaw = diff.patch.currentPly;
        const ply = typeof plyRaw === 'number' && Number.isFinite(plyRaw) ? Math.max(0, Math.trunc(plyRaw)) : 0;
        if (ply <= 0 || (ply !== movesLen && ply !== movesLen + 1)) {
            return;
        }
    }
    const patch = diff.patch ?? {};
    const genericPatch: Record<string, unknown> = { ...patch };
    const domainKeys: Array<keyof WorkerDiffPatch> = [
        'currentPly',
        'move',
        'ki2_move',
        'eval',
        'eval_cp',
        'depth',
        'seldepth',
        'nodes',
        'time_ms',
        'wall_time_ms',
        'latency_ms',
        'latency_alert',
        'result_code',
        'clock',
        'active',
        'side',
        'black_remain_ms',
        'white_remain_ms',
        'applied_increment_ms',
        'occurred_at_ms',
        'started_at_ms',
        'pre_black_remain_ms',
        'pre_white_remain_ms',
        'byoyomi_ms_black',
        'byoyomi_ms_white',
        'increment_ms_black',
        'increment_ms_white',
        'black_name',
        'white_name',
        'initial_sfen',
        'sfen',
        'time_control_black',
        'time_control_white',
    ];

    // Arrays managed by updateMoveCollections/updateEvalCollections etc.
    // We must NOT allow applyPatch to overwrite them with partial/empty arrays from a diff.
    const unprotectedArrays: Array<keyof WorkerSnapshotRecord> = [
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

    for (const key of domainKeys) {
        delete genericPatch[key];
    }
    if (diff.kind !== 'finish') {
        for (const key of unprotectedArrays) {
            if (Object.hasOwn(genericPatch, key)) {
                delete genericPatch[key];
            }
        }
    }
    const patched = applyPatch(existing, genericPatch);
    let update = buildUpdateFromDiff(diff, patched);
    if (update && Object.hasOwn(update, 'engine_status')) {
        const statusSummary = summarizeEngineStatus((update as Record<string, unknown>).engine_status);
        debugLog('apply engine_status diff', {
            gid,
            kind: diff.kind ?? null,
            type: diff.type ?? null,
            engine_status: statusSummary,
        });
    }
    if (!update) {
        applyWsSeqMeta(patched, topic, seqByTopic.get(topic));
        snapshotsByGid.set(gid, patched);
        const workers = workersByGid.get(gid);
        if (workers) {
            for (const workerIdx of workers) {
                const vm = buildViewModel(workerIdx, patched, { includeSnapshot: false, snapshotDelta: null });
                postAck({ type: 'vm', payload: vm });
            }
        }
        return;
    }
    if (diff.kind !== 'analysis') {
        const emptyAt = emptySnapshotAtByGid.get(gid);
        if (emptyAt != null) {
            const plyRaw = (update as { currentPly?: unknown }).currentPly;
            const ply = typeof plyRaw === 'number' && Number.isFinite(plyRaw) ? Math.max(0, Math.trunc(plyRaw)) : 0;
            const contiguousLen = countContiguousMoves(existing);
            const now = Date.now();
            const lastReplay = lastReplayFromStartAtByGid.get(gid) ?? 0;
            const replayTopic = gameMovesDiffTopic(gid);
            if (contiguousLen === 0 && ply > 1 && !pendingSnapshotTopics.has(replayTopic) && now - lastReplay >= 1000) {
                lastReplayFromStartAtByGid.set(gid, now);
                pendingSnapshotTopics.add(replayTopic);
                postAck({
                    type: 'request_snapshot',
                    topic: replayTopic,
                    fromSeq: 0,
                    reason_code: SNAPSHOT_REASON_WORKER_FUTURE_MOVE,
                });
                return;
            }
        }
    }
    if (shouldDeferFutureMove(update, patched)) {
        const plyRaw = (update as { currentPly?: unknown }).currentPly;
        const ply = typeof plyRaw === 'number' && Number.isFinite(plyRaw) ? Math.max(0, Math.trunc(plyRaw)) : 0;

        // Buffer the deferred move first
        if (ply > 0) {
            const deferred = deferredMovesByGid.get(gid) ?? new Map<number, WorkerSnapshotUpdate>();
            // Last-write-wins for the same ply.
            deferred.set(ply, update);
            deferredMovesByGid.set(gid, deferred);
        }

        // Apply cooldown before requesting snapshot to reduce request frequency
        const now = Date.now();
        const lastRequest = lastFutureMoveSnapshotAtByGid.get(gid) ?? 0;
        if (now - lastRequest >= FUTURE_MOVE_SNAPSHOT_COOLDOWN_MS && !pendingSnapshotTopics.has(topic)) {
            lastFutureMoveSnapshotAtByGid.set(gid, now);
            pendingSnapshotTopics.add(topic);
            const rawFromSeq = seqByTopic.get(topic);
            const fromSeq =
                typeof rawFromSeq === 'number' && Number.isFinite(rawFromSeq) ? Math.max(0, Math.trunc(rawFromSeq)) : 0;
            postAck({ type: 'request_snapshot', topic, fromSeq, reason_code: SNAPSHOT_REASON_WORKER_FUTURE_MOVE });
        }

        update = stripMoveFields(update);
    }
    const merged = mergeWorkerSnapshotMutable(patched, update);
    const drained = drainDeferredMovesForGid(gid, merged);
    applyWsSeqMeta(drained, topic, seqByTopic.get(topic));
    snapshotsByGid.set(gid, drained);
    maybeRequestSnapshotOnMoveGap(topic, drained);
    maybeRequestSnapshotOnMoveHoles(topic, drained);
    const workers = workersByGid.get(gid);
    if (workers) {
        const includeSnapshot = hasMoveDelta(update);
        for (const workerIdx of workers) {
            // Root-cause fix:
            // Move history integrity is managed in this worker. If we emit move-bearing deltas to the main thread,
            // it must re-merge moves and can observe transient holes under rAF coalescing / state churn, which then
            // triggers `[LiveMoves] skipping out-of-order move` loops. Instead, send the merged snapshot for any
            // move-bearing update and suppress `snapshotDelta` in that case.
            const vm = buildViewModel(workerIdx, drained, {
                includeSnapshot,
                snapshotDelta: includeSnapshot ? null : update,
            });
            postAck({ type: 'vm', payload: vm });
        }
    }
}

function handleAssignmentSnapshot(topic: string, payload: unknown): void {
    if (!payload || typeof payload !== 'object') return;
    const inner = (payload as { payload?: unknown }).payload ?? payload;
    if (!inner || typeof inner !== 'object') return;
    const data = inner as AssignmentPayload;
    const raw = data.assignments;
    if (!raw || typeof raw !== 'object') return;
    const isSnapshot = topic.endsWith('.snapshot');
    // After a reset, ignore assignment diffs until a snapshot arrives.
    // This prevents processing stale replayed diffs that would trigger snapshot requests for old gids.
    if (awaitingAssignmentSnapshot && !isSnapshot) {
        debugLog('drop assignment diff while awaiting snapshot', { topic });
        clearPending(topic);
        return;
    }
    const revRaw = (inner as { assignment_rev?: unknown }).assignment_rev;
    const rev = typeof revRaw === 'number' && Number.isFinite(revRaw) ? Math.max(0, Math.trunc(revRaw)) : null;
    if (rev !== null && assignmentRev !== null && rev < assignmentRev) {
        debugLog('drop assignment snapshot (rev regression)', {
            topic,
            incoming: rev,
            current: assignmentRev,
        });
        clearPending(topic);
        return;
    }
    if (isSnapshot && awaitingAssignmentSnapshot) {
        // Clear the awaiting flag since we're about to process the authoritative snapshot.
        awaitingAssignmentSnapshot = false;
        resetState();
        // resetState sets awaitingAssignmentSnapshot=true, but we're processing a snapshot now, so clear it.
        awaitingAssignmentSnapshot = false;
    }
    if (isSnapshot) {
        debugLog('assignment snapshot', {
            rev,
            assignments: Object.keys(raw).length,
        });
    }
    const nextMap = new Map<number, string | null>();
    for (const [k, v] of Object.entries(raw)) {
        const idx = Number.parseInt(k, 10);
        if (!Number.isFinite(idx)) continue;
        const nextGid = typeof v === 'string' && v.trim() ? v.trim() : null;
        nextMap.set(idx, nextGid);
    }

    if (isSnapshot) {
        for (const [idx, prevGid] of assignmentsByWorker.entries()) {
            if (!nextMap.has(idx)) {
                assignmentsByWorker.delete(idx);
                if (prevGid) {
                    const prevSet = workersByGid.get(prevGid);
                    if (prevSet) {
                        prevSet.delete(idx);
                        if (prevSet.size === 0) {
                            workersByGid.delete(prevGid);
                            cleanupUnreferencedGid(prevGid);
                        }
                    }
                }
            }
        }
    }
    for (const [idx, nextGid] of nextMap.entries()) {
        const prevGid = assignmentsByWorker.get(idx) ?? null;
        if (prevGid === nextGid) continue;
        debugLog('assignment update', { worker: idx, prev: prevGid, next: nextGid, rev });
        assignmentsByWorker.set(idx, nextGid);

        if (prevGid) {
            const prevSet = workersByGid.get(prevGid);
            if (prevSet) {
                prevSet.delete(idx);
                if (prevSet.size === 0) {
                    workersByGid.delete(prevGid);
                    cleanupUnreferencedGid(prevGid);
                }
            }
        }
        if (nextGid) {
            const set = workersByGid.get(nextGid) ?? new Set<number>();
            set.add(idx);
            workersByGid.set(nextGid, set);
            // Ensure we have at least a placeholder snapshot so cards can render labels immediately.
            const existing = snapshotsByGid.get(nextGid);
            const base =
                existing ??
                ({
                    ...(createEmptyWorkerSnapshot() as unknown as WorkerSnapshotRecord),
                    game_id: nextGid,
                } as WorkerSnapshotRecord);
            snapshotsByGid.set(nextGid, base);
            const vm = buildViewModel(idx, base, { includeSnapshot: false, snapshotDelta: null });
            postAck({ type: 'vm', payload: vm });

            // Request catch-up for the newly assigned gid stream, but debounce to avoid
            // snapshot storms when assignment updates churn rapidly.
            scheduleAssignmentSnapshot(idx, nextGid);
        } else {
            // Unassigned: keep an empty snapshot for the worker.
            const empty = createEmptyWorkerSnapshot() as unknown as WorkerSnapshotRecord;
            const vm = buildViewModel(idx, empty, { includeSnapshot: false, snapshotDelta: null });
            postAck({ type: 'vm', payload: vm });
        }
    }
    clearPending(topic);
    if (isSnapshot) {
        assignmentReady = true;
    }
    if (rev !== null) {
        assignmentRev = rev;
    }
}

function scheduleAssignmentSnapshot(workerIdx: number, gid: string): void {
    const existingTimer = assignmentSnapshotTimers.get(workerIdx);
    if (existingTimer != null) {
        clearTimeout(existingTimer);
    }
    pendingAssignmentSnapshotGid.set(workerIdx, gid);
    const timer = setTimeout(() => {
        assignmentSnapshotTimers.delete(workerIdx);
        const latestGid = pendingAssignmentSnapshotGid.get(workerIdx);
        if (!latestGid) return;
        // Verify the worker is still assigned to this gid before requesting snapshot.
        // This prevents stale snapshot requests when assignment changes rapidly.
        const currentAssignment = assignmentsByWorker.get(workerIdx);
        if (currentAssignment !== latestGid) {
            pendingAssignmentSnapshotGid.delete(workerIdx);
            return;
        }
        const snapshotTopic = gameSnapshotTopic(latestGid);
        if (!pendingSnapshotTopics.has(snapshotTopic)) {
            pendingSnapshotTopics.add(snapshotTopic);
            postAck({
                type: 'request_snapshot',
                topic: snapshotTopic,
                reason_code: SNAPSHOT_REASON_WORKER_ASSIGNMENT,
            });
        }
    }, assignmentSnapshotDebounceMs);
    assignmentSnapshotTimers.set(workerIdx, timer as unknown as number);
}

function handleEnvelope(envelope: LiveEnvelope): void {
    const { topic = '', seq, payload } = envelope;
    if (!assignmentReady && topic.startsWith('live.game.')) {
        maybeRequestAssignmentSnapshot();
        return;
    }

    // If no sequence number, standard processing (no ordering guarantees needed or possible)
    if (typeof seq !== 'number') {
        processEnvelopeImmediately(envelope);
        return;
    }

    // Initialize sequence if unknown
    const lastSeq = seqByTopic.get(topic);
    if (lastSeq === undefined) {
        processEnvelopeImmediately(envelope);
        return;
    }

    const expected = lastSeq + 1;

    // CASE 1: Stale / Duplicate
    if (seq < expected) {
        return;
    }

    // CASE 2: Future / Gap
    if (seq > expected) {
        if (isStrictTopic(topic)) {
            const lastRequested = lastSnapshotRequestFromSeqByTopic.get(topic) ?? null;
            const parsed = parseGameTopic(topic);
            if (parsed?.gid && !hasAssignedWorkers(parsed.gid)) {
                return;
            }
            if (lastRequested !== lastSeq) {
                pendingSnapshotTopics.add(topic);
                lastSnapshotRequestFromSeqByTopic.set(topic, lastSeq);
                postAck({
                    type: 'request_snapshot',
                    topic,
                    fromSeq: lastSeq,
                    reason_code: SNAPSHOT_REASON_WORKER_SEQ_GAP,
                });
            }
            if (parsed?.kind === 'diff') {
                const diff = unwrapGameDiffPayload(payload, parsed.gid, topic);
                const typeStr =
                    typeof diff?.type === 'string' ? diff?.type : typeof diff?.kind === 'string' ? diff?.kind : '';
                const isClock = typeStr === 'clock_start' || typeStr === 'clock_increment';
                if (isClock) {
                    // Allow forward clock events so the UI doesn't freeze while waiting for catch-up.
                    processEnvelopeImmediately(envelope);
                }
            }
            return;
        }
        // Non-strict streams (analysis/state/snapshot) allow gaps: keep the latest.
        seqByTopic.set(topic, seq);
        processEnvelopeImmediately(envelope);
        return;
    }

    // CASE 3: Exact Match (seq == expected)
    processEnvelopeImmediately(envelope);
}

type ControlMessage = { type: 'reset'; reason?: string } | { type: 'diagnostics' };

function resetState(): void {
    debugLog('resetState', {
        assignmentsByWorker: assignmentsByWorker.size,
        workersByGid: workersByGid.size,
        snapshotsByGid: snapshotsByGid.size,
        assignmentRev,
        awaitingAssignmentSnapshot,
    });
    snapshotsByGid.clear();
    assignmentsByWorker.clear();
    workersByGid.clear();
    seqByTopic.clear();
    pendingSnapshotTopics.clear();
    lastSnapshotRequestFromSeqByTopic.clear();
    deferredMovesByGid.clear();
    assignmentReady = false;
    assignmentRev = null;
    // After reset, wait for a fresh assignment snapshot before processing diffs.
    awaitingAssignmentSnapshot = true;
    lastAssignmentSnapshotRequestAt = null;
    emptySnapshotAtByGid.clear();
    lastReplayFromStartAtByGid.clear();
    lastFutureMoveSnapshotAtByGid.clear();
    for (const timer of assignmentSnapshotTimers.values()) {
        clearTimeout(timer);
    }
    assignmentSnapshotTimers.clear();
    pendingAssignmentSnapshotGid.clear();
}

self.onmessage = (event: MessageEvent<LiveEnvelope | ControlMessage>) => {
    const data = event.data;
    if (!data || typeof data !== 'object') return;
    if ((data as ControlMessage).type === 'reset') {
        resetState();
        return;
    }
    if ((data as ControlMessage).type === 'diagnostics') {
        const diag = {
            snapshotsByGid: snapshotsByGid.size,
            assignmentsByWorker: assignmentsByWorker.size,
            workersByGid: workersByGid.size,
            seqByTopic: seqByTopic.size,
            pendingSnapshotTopics: pendingSnapshotTopics.size,
            lastSnapshotRequestFromSeqByTopic: lastSnapshotRequestFromSeqByTopic.size,
            deferredMovesByGid: deferredMovesByGid.size,
            emptySnapshotAtByGid: emptySnapshotAtByGid.size,
            lastReplayFromStartAtByGid: lastReplayFromStartAtByGid.size,
            assignmentSnapshotTimers: assignmentSnapshotTimers.size,
            pendingAssignmentSnapshotGid: pendingAssignmentSnapshotGid.size,
            awaitingAssignmentSnapshot,
            assignmentReady,
            assignmentRev,
        };
        postMessage({ type: 'diagnostics', payload: diag });
        return;
    }
    handleEnvelope(data as LiveEnvelope);
};

export const __testShouldIgnoreIncomingSnapshot = shouldIgnoreIncomingSnapshot;
export const __testHandleEnvelope = handleEnvelope;
export function __testResetState(): void {
    resetState();
}
export function __testHasSnapshot(gid: string): boolean {
    return snapshotsByGid.has(gid);
}
