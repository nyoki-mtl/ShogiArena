import type { LiveGameRecord } from '@/modules/live/types/public';
import type { WorkerSnapshotRecord } from '@/modules/live/types/updates';
import { DEFAULT_INITIAL_SFEN } from '@/modules/live/utils';
import {
    assertFieldAbsent,
    ensureObject,
    normalizeId,
    normalizeNumberArray,
    normalizeOptionalNullableNumber,
    normalizeOptionalString,
    normalizeStringArray,
} from './common';

export function normalizeLiveGameRecord(raw: LiveGameRecord, context = 'game_record'): WorkerSnapshotRecord {
    const payload = ensureObject(raw, context);
    assertFieldAbsent(payload, 'eval_values', context);

    const gameId = normalizeId(payload.game_id ?? payload.gameId, `${context}.game_id`);
    if (!gameId) {
        throw new Error(`${context}.game_id: required`);
    }

    const initialSfen =
        normalizeOptionalString(payload.initial_sfen ?? payload.initialSfen, `${context}.initial_sfen`) ??
        DEFAULT_INITIAL_SFEN;
    const sfen = normalizeOptionalString(payload.sfen, `${context}.sfen`) ?? null;

    const moves = normalizeStringArray(payload.moves, `${context}.moves`);
    const ki2Moves = normalizeStringArray(payload.ki2_moves, `${context}.ki2_moves`);
    const evalBlack = normalizeNumberArray(payload.eval_black, `${context}.eval_black`) ?? [];
    const evalWhite = normalizeNumberArray(payload.eval_white, `${context}.eval_white`) ?? [];
    const nodesValues = normalizeNumberArray(payload.nodes_values, `${context}.nodes_values`) ?? [];
    const depthValues = normalizeNumberArray(payload.depth_values, `${context}.depth_values`) ?? [];
    const seldepthValues = normalizeNumberArray(payload.seldepth_values, `${context}.seldepth_values`) ?? [];
    const moveTimesMs = normalizeNumberArray(payload.move_times_ms, `${context}.move_times_ms`) ?? [];
    const wallTimesMs = normalizeNumberArray(payload.wall_times_ms, `${context}.wall_times_ms`) ?? [];
    const latencyDeltasMs = normalizeNumberArray(payload.latency_deltas_ms, `${context}.latency_deltas_ms`) ?? [];

    const blackName =
        normalizeOptionalString(payload.black_name ?? payload.black_player, `${context}.black_name`) ?? null;
    const whiteName =
        normalizeOptionalString(payload.white_name ?? payload.white_player, `${context}.white_name`) ?? null;

    const timeControlBlack =
        normalizeOptionalString(payload.time_control_black, `${context}.time_control_black`) ?? null;
    const timeControlWhite =
        normalizeOptionalString(payload.time_control_white, `${context}.time_control_white`) ?? null;

    const resultCode = normalizeOptionalNullableNumber(payload.result_code, `${context}.result_code`) ?? null;

    const currentPly = Math.max(0, moves.length);

    return {
        game_id: gameId,
        initial_sfen: initialSfen,
        sfen,
        black_name: blackName,
        white_name: whiteName,
        moves,
        ki2_moves: ki2Moves,
        eval_black: evalBlack,
        eval_white: evalWhite,
        nodes_values: nodesValues,
        depth_values: depthValues,
        seldepth_values: seldepthValues,
        move_times_ms: moveTimesMs,
        wall_times_ms: wallTimesMs,
        latency_deltas_ms: latencyDeltasMs,
        latency_alerts: [],
        currentPly,
        result_code: resultCode,
        time_control_black: timeControlBlack,
        time_control_white: timeControlWhite,
    };
}
