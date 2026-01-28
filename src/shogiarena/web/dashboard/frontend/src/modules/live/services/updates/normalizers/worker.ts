import type { WorkerSnapshotUpdate } from '@/modules/live/types/public';
import type { MutableJsonObject } from '@/types/shared';
import {
    assertFieldAbsent,
    normalizeId,
    normalizeNumberArray,
    normalizeOptionalNullableNumber,
    normalizeOptionalNumber,
    normalizeOptionalString,
    normalizeOptionalStringArray,
} from './common';

function sanitizeWorkerSnapshotUpdate(payload: WorkerSnapshotUpdate): WorkerSnapshotUpdate {
    const next: MutableJsonObject = { ...(payload as MutableJsonObject) };

    assertFieldAbsent(next, 'gameId', 'worker_update');
    assertFieldAbsent(next, 'moves_usi', 'worker_update');
    assertFieldAbsent(next, 'moves_ki2', 'worker_update');
    assertFieldAbsent(next, 'eval_values', 'worker_update');

    if ('game_id' in next) {
        next.game_id = normalizeId(next.game_id, 'worker_update.game_id');
    }

    if ('initial_sfen' in next) {
        next.initial_sfen = normalizeOptionalString(next.initial_sfen, 'worker_update.initial_sfen') ?? undefined;
    }
    if ('sfen' in next) {
        next.sfen = normalizeOptionalString(next.sfen, 'worker_update.sfen') ?? undefined;
    }
    if ('black_name' in next) {
        next.black_name = normalizeOptionalString(next.black_name, 'worker_update.black_name') ?? undefined;
    }
    if ('white_name' in next) {
        next.white_name = normalizeOptionalString(next.white_name, 'worker_update.white_name') ?? undefined;
    }

    if ('currentPly' in next) {
        const value = normalizeOptionalNumber(next.currentPly, 'worker_update.currentPly');
        if (value === undefined) {
            delete next.currentPly;
        } else {
            next.currentPly = Math.max(0, Math.trunc(value));
        }
    }

    if ('moves' in next) {
        next.moves = normalizeOptionalStringArray(next.moves, 'worker_update.moves');
    }
    if ('ki2_moves' in next) {
        next.ki2_moves = normalizeOptionalStringArray(next.ki2_moves, 'worker_update.ki2_moves');
    }

    const numberArrayFields: Array<keyof WorkerSnapshotUpdate> = [
        'eval_black',
        'eval_white',
        'nodes_values',
        'depth_values',
        'seldepth_values',
        'move_times_ms',
        'wall_times_ms',
        'latency_deltas_ms',
    ];
    for (const field of numberArrayFields) {
        if (field in next) {
            const arrayValue = normalizeNumberArray(next[field], `worker_update.${String(field)}`);
            if (arrayValue === undefined) {
                delete next[field];
            } else {
                next[field] = arrayValue;
            }
        }
    }

    const numericFields: Array<keyof WorkerSnapshotUpdate> = [
        'eval',
        'depth',
        'seldepth',
        'nodes',
        'time_ms',
        'wall_time_ms',
        'latency_ms',
        'applied_increment_ms',
        'pre_black_remain_ms',
        'pre_white_remain_ms',
        'occurred_at_ms',
    ];
    for (const field of numericFields) {
        if (field in next) {
            const value = normalizeOptionalNumber(next[field], `worker_update.${String(field)}`);
            if (value === undefined) {
                delete next[field];
            } else {
                next[field] = value;
            }
        }
    }

    const clockFields: Array<keyof WorkerSnapshotUpdate> = [
        'black_remain_ms',
        'white_remain_ms',
        'started_at_ms',
        'byoyomi_ms_black',
        'byoyomi_ms_white',
    ];
    for (const field of clockFields) {
        if (field in next) {
            next[field] = normalizeOptionalNullableNumber(next[field], `worker_update.${String(field)}`);
        }
    }

    if ('latency_alert' in next) {
        const value = next.latency_alert;
        next.latency_alert = typeof value === 'boolean' ? value : undefined;
    }
    if ('latency_alerts' in next) {
        const arrayValue = Array.isArray(next.latency_alerts)
            ? next.latency_alerts.map((value) => (typeof value === 'boolean' ? value : Boolean(value)))
            : undefined;
        if (arrayValue === undefined) {
            delete next.latency_alerts;
        } else {
            next.latency_alerts = arrayValue;
        }
    }

    if ('move' in next) {
        next.move = normalizeOptionalString(next.move, 'worker_update.move') ?? undefined;
    }
    if ('ki2_move' in next) {
        next.ki2_move = normalizeOptionalString(next.ki2_move, 'worker_update.ki2_move') ?? undefined;
    }
    if ('type' in next) {
        next.type = normalizeOptionalString(next.type, 'worker_update.type') ?? undefined;
    }
    if ('side' in next) {
        next.side = normalizeOptionalString(next.side, 'worker_update.side') ?? undefined;
    }
    if ('time_control_black' in next) {
        next.time_control_black =
            normalizeOptionalString(next.time_control_black, 'worker_update.time_control_black') ?? undefined;
    }
    if ('time_control_white' in next) {
        next.time_control_white =
            normalizeOptionalString(next.time_control_white, 'worker_update.time_control_white') ?? undefined;
    }

    return next as WorkerSnapshotUpdate;
}

function requireFiniteNumber(value: number | null | undefined, context: string): number {
    if (typeof value !== 'number' || !Number.isFinite(value)) {
        throw new Error(`${context}: expected finite number`);
    }
    return value;
}

function requireString(value: string | null | undefined, context: string): string {
    if (typeof value !== 'string' || value.trim() === '') {
        throw new Error(`${context}: expected non-empty string`);
    }
    return value;
}

function enforceClockContract(payload: WorkerSnapshotUpdate) {
    if (payload.type === 'clock_start') {
        requireString(payload.active ?? undefined, 'worker_update.clock_start.active');
        requireFiniteNumber(payload.black_remain_ms ?? undefined, 'worker_update.clock_start.black_remain_ms');
        requireFiniteNumber(payload.white_remain_ms ?? undefined, 'worker_update.clock_start.white_remain_ms');
        requireFiniteNumber(payload.started_at_ms ?? undefined, 'worker_update.clock_start.started_at_ms');
    }

    if (payload.type === 'clock_increment') {
        requireString(payload.side ?? undefined, 'worker_update.clock_increment.side');
        requireFiniteNumber(
            payload.applied_increment_ms ?? undefined,
            'worker_update.clock_increment.applied_increment_ms',
        );
        requireFiniteNumber(
            payload.pre_black_remain_ms ?? undefined,
            'worker_update.clock_increment.pre_black_remain_ms',
        );
        requireFiniteNumber(
            payload.pre_white_remain_ms ?? undefined,
            'worker_update.clock_increment.pre_white_remain_ms',
        );
        requireFiniteNumber(payload.black_remain_ms ?? undefined, 'worker_update.clock_increment.black_remain_ms');
        requireFiniteNumber(payload.white_remain_ms ?? undefined, 'worker_update.clock_increment.white_remain_ms');
        requireFiniteNumber(payload.occurred_at_ms ?? undefined, 'worker_update.clock_increment.occurred_at_ms');
    }
}

export function normalizeWorkerSnapshotUpdate(
    payload: WorkerSnapshotUpdate | null | undefined,
    context = 'worker_update',
): WorkerSnapshotUpdate | null {
    if (payload == null) {
        return null;
    }
    if (typeof payload !== 'object' || Array.isArray(payload)) {
        throw new Error(`Invalid ${context}: expected object payload`);
    }
    const sanitized = sanitizeWorkerSnapshotUpdate(payload);
    enforceClockContract(sanitized);
    return sanitized;
}
