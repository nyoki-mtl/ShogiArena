import type { WorkerSnapshotRecord } from '@/modules/live/types/updates';
import type { MutableJsonObject } from '@/types/shared';
import {
    assertFieldAbsent,
    ensureObject,
    normalizeId,
    normalizeNumberArray,
    normalizeOptionalNullableNumber,
    normalizeOptionalString,
    normalizeRequiredString,
    normalizeStringArray,
} from './common';

function normalizeBooleanArray(value: unknown, context: string): (boolean | null)[] {
    if (value === undefined || value === null) {
        return [];
    }
    if (!Array.isArray(value)) {
        throw new Error(`${context}: expected array of booleans`);
    }
    return value.map((entry, index) => {
        if (entry === null) return null;
        if (typeof entry === 'boolean') return entry;
        throw new Error(`${context}[${index}]: expected boolean or null`);
    });
}

function requireField<T>(value: T | undefined | null, context: string): T {
    if (value === undefined || value === null) {
        throw new Error(`${context}: required field is missing`);
    }
    return value;
}

/**
 * 正規化済みの WorkerSnapshotRecord を返す。未知/legacy フィールドは即座に例外化する。
 */
export function normalizeWorkerSnapshotRecord(raw: unknown, context: string = 'worker_snapshot'): WorkerSnapshotRecord {
    const payload = ensureObject(raw, context);

    assertFieldAbsent(payload, 'gameId', context);
    assertFieldAbsent(payload, 'moves_usi', context);
    assertFieldAbsent(payload, 'moves_ki2', context);
    assertFieldAbsent(payload, 'eval_values', context);

    const gameId = normalizeId(payload.game_id, `${context}.game_id`);
    const initialSfen = normalizeRequiredString(payload.initial_sfen, `${context}.initial_sfen`);
    const sfen = normalizeOptionalString(payload.sfen, `${context}.sfen`);

    const moves = normalizeStringArray(requireField(payload.moves, `${context}.moves`), `${context}.moves`);
    const ki2Moves = normalizeStringArray(
        requireField(payload.ki2_moves, `${context}.ki2_moves`),
        `${context}.ki2_moves`,
    );

    const evalBlack = normalizeNumberArray(
        requireField(payload.eval_black, `${context}.eval_black`),
        `${context}.eval_black`,
    );
    const evalWhite = normalizeNumberArray(
        requireField(payload.eval_white, `${context}.eval_white`),
        `${context}.eval_white`,
    );
    const nodesValues = normalizeNumberArray(
        requireField(payload.nodes_values, `${context}.nodes_values`),
        `${context}.nodes_values`,
    );
    const depthValues = normalizeNumberArray(
        requireField(payload.depth_values, `${context}.depth_values`),
        `${context}.depth_values`,
    );
    const seldepthValues = normalizeNumberArray(
        requireField(payload.seldepth_values, `${context}.seldepth_values`),
        `${context}.seldepth_values`,
    );
    const moveTimesMs = normalizeNumberArray(
        requireField(payload.move_times_ms, `${context}.move_times_ms`),
        `${context}.move_times_ms`,
    );
    const wallTimesMs = normalizeNumberArray(
        requireField(payload.wall_times_ms, `${context}.wall_times_ms`),
        `${context}.wall_times_ms`,
    );
    const latencyDeltasMs = normalizeNumberArray(
        requireField(payload.latency_deltas_ms, `${context}.latency_deltas_ms`),
        `${context}.latency_deltas_ms`,
    );
    const latencyAlerts = normalizeBooleanArray(
        requireField(payload.latency_alerts, `${context}.latency_alerts`),
        `${context}.latency_alerts`,
    );

    const currentPlyRaw = requireField(payload.currentPly, `${context}.currentPly`);
    if (typeof currentPlyRaw !== 'number' || !Number.isFinite(currentPlyRaw)) {
        throw new Error(`${context}.currentPly: expected finite number`);
    }
    const currentPly = Math.max(0, Math.trunc(currentPlyRaw));

    const result: MutableJsonObject = {
        game_id: gameId,
        initial_sfen: initialSfen,
        sfen,
        black_name: normalizeOptionalString(payload.black_name, `${context}.black_name`),
        white_name: normalizeOptionalString(payload.white_name, `${context}.white_name`),
        moves,
        ki2_moves: ki2Moves,
        eval_black: evalBlack ?? [],
        eval_white: evalWhite ?? [],
        nodes_values: nodesValues ?? [],
        depth_values: depthValues ?? [],
        seldepth_values: seldepthValues ?? [],
        move_times_ms: moveTimesMs ?? [],
        wall_times_ms: wallTimesMs ?? [],
        latency_deltas_ms: latencyDeltasMs ?? [],
        latency_alerts: latencyAlerts ?? [],
        currentPly,
    };

    const optionalNumericFields: Array<keyof WorkerSnapshotRecord> = [
        'result_code',
        'black_remain_ms',
        'white_remain_ms',
        'byoyomi_ms_black',
        'byoyomi_ms_white',
        'started_at_ms',
        'time_ms',
        'wall_time_ms',
        'latency_ms',
    ];
    optionalNumericFields.forEach((field) => {
        if (field in payload) {
            result[field] = normalizeOptionalNullableNumber(payload[field], `${context}.${String(field)}`);
        }
    });

    const optionalStringFields: Array<keyof WorkerSnapshotRecord> = [
        'time_control_black',
        'time_control_white',
        'black_player',
        'white_player',
        'move',
        'ki2_move',
        'type',
        'side',
        'active',
    ];
    optionalStringFields.forEach((field) => {
        if (field in payload) {
            result[field] = normalizeOptionalString(payload[field], `${context}.${String(field)}`);
        }
    });

    return result as WorkerSnapshotRecord;
}
