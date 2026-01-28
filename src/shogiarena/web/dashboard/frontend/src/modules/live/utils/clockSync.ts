import { DEFAULT_INITIAL_SFEN } from '@/modules/live/utils';
import type { WorkerRuntimeState } from '@/modules/live/types';
import { parseTimeControlSpec } from '@/modules/live/services/time/parse';
import type { ParsedTimeControlSpec } from '@/modules/live/types/time';

export type ClockSide = 'black' | 'white';

export type ClockCorrectionSource = 'clock_start' | 'clock_increment' | 'snapshot';

export interface ClockCorrectionPayload {
    active?: unknown;
    side?: unknown;
    black_remain_ms?: unknown;
    white_remain_ms?: unknown;
    byoyomi_ms_black?: unknown;
    byoyomi_ms_white?: unknown;
    time_control_black?: unknown;
    time_control_white?: unknown;
}

function normalizeClockSide(value: unknown): ClockSide | null {
    if (typeof value !== 'string') return null;
    const trimmed = value.trim().toLowerCase();
    if (trimmed === 'black') return 'black';
    if (trimmed === 'white') return 'white';
    return null;
}

function coerceNumber(value: unknown): number | undefined {
    return typeof value === 'number' && Number.isFinite(value) ? value : undefined;
}

function coerceString(value: unknown): string | null | undefined {
    if (typeof value === 'string') return value;
    return undefined;
}

function computeSideToMove(
    initialSfen: string | null | undefined,
    currentPly: number,
    normalizeSFEN: (sfen: string) => string,
): ClockSide {
    const normalized = normalizeSFEN(initialSfen ?? DEFAULT_INITIAL_SFEN);
    const parts = normalized.split(' ');
    const startIsBlack = parts.length > 1 ? parts[1] === 'b' : true;
    const sideToMoveIsBlack = startIsBlack ? currentPly % 2 === 0 : currentPly % 2 === 1;
    return sideToMoveIsBlack ? 'black' : 'white';
}

export function queueClockCorrections(
    ws: WorkerRuntimeState,
    payload: ClockCorrectionPayload,
    source: ClockCorrectionSource,
    receivedAtMs: number,
): void {
    if (!ws.pendingClockBySide) {
        ws.pendingClockBySide = {};
    }

    const blackRemain = coerceNumber(payload.black_remain_ms);
    const whiteRemain = coerceNumber(payload.white_remain_ms);
    const blackByoyomi = coerceNumber(payload.byoyomi_ms_black);
    const whiteByoyomi = coerceNumber(payload.byoyomi_ms_white);
    const blackTc = coerceString(payload.time_control_black);
    const whiteTc = coerceString(payload.time_control_white);

    if (blackRemain !== undefined || blackByoyomi !== undefined || blackTc !== undefined) {
        const existing = ws.pendingClockBySide.black;
        ws.pendingClockBySide.black = {
            remainMs: blackRemain ?? existing?.remainMs,
            byoyomiMs: blackByoyomi ?? existing?.byoyomiMs,
            timeControl: blackTc ?? existing?.timeControl,
            receivedAtMs,
            source,
        };
    }

    if (whiteRemain !== undefined || whiteByoyomi !== undefined || whiteTc !== undefined) {
        const existing = ws.pendingClockBySide.white;
        ws.pendingClockBySide.white = {
            remainMs: whiteRemain ?? existing?.remainMs,
            byoyomiMs: whiteByoyomi ?? existing?.byoyomiMs,
            timeControl: whiteTc ?? existing?.timeControl,
            receivedAtMs,
            source,
        };
    }
}

function applyParsedTimeControl(ws: WorkerRuntimeState, side: ClockSide, parsed: ParsedTimeControlSpec): void {
    const hasTimePool = parsed.mode === 'fixed' || parsed.initial > 0 || parsed.increment > 0 || parsed.byoyomi > 0;
    const hasSearchLimit = (parsed.depth ?? 0) > 0 || (parsed.nodes ?? 0) > 0;
    if (side === 'black') {
        ws.tcBlackHasTimePool = hasTimePool;
        ws.tcBlackHasSearchLimit = hasSearchLimit;
    } else {
        ws.tcWhiteHasTimePool = hasTimePool;
        ws.tcWhiteHasSearchLimit = hasSearchLimit;
    }
}

export function updateTimeControlState(
    ws: WorkerRuntimeState,
    timeControlBlack: unknown,
    timeControlWhite: unknown,
): void {
    const tcBlackRaw = typeof timeControlBlack === 'string' ? timeControlBlack.trim() : '';
    const tcWhiteRaw = typeof timeControlWhite === 'string' ? timeControlWhite.trim() : '';
    let touched = false;
    let blackMode: ParsedTimeControlSpec['mode'] | null = null;
    let whiteMode: ParsedTimeControlSpec['mode'] | null = null;

    if (tcBlackRaw) {
        const parsed = parseTimeControlSpec(tcBlackRaw) as ParsedTimeControlSpec;
        applyParsedTimeControl(ws, 'black', parsed);
        blackMode = parsed.mode;
        touched = true;
    }
    if (tcWhiteRaw) {
        const parsed = parseTimeControlSpec(tcWhiteRaw) as ParsedTimeControlSpec;
        applyParsedTimeControl(ws, 'white', parsed);
        whiteMode = parsed.mode;
        touched = true;
    }
    if (!touched) {
        return;
    }

    const hasTimePoolAny = Boolean(ws.tcBlackHasTimePool) || Boolean(ws.tcWhiteHasTimePool);
    const hasSearchAny = Boolean(ws.tcBlackHasSearchLimit) || Boolean(ws.tcWhiteHasSearchLimit);
    const anyFixed = blackMode === 'fixed' || whiteMode === 'fixed';

    if (hasTimePoolAny) {
        ws.clockDisplayMode = anyFixed ? 'fixed' : 'time';
    } else if (hasSearchAny) {
        ws.clockDisplayMode = 'search';
    } else {
        ws.clockDisplayMode = 'unknown';
    }
}

function applyPendingClockForSide(ws: WorkerRuntimeState, side: ClockSide, nowMs: number): void {
    const pending = ws.pendingClockBySide?.[side];
    if (pending) {
        if (side === 'black') {
            if (pending.remainMs !== undefined) ws.blackRemainMs = pending.remainMs;
            if (pending.byoyomiMs !== undefined) ws.byoyomiMsBlack = pending.byoyomiMs;
            if (pending.timeControl !== undefined) ws.timeControlBlack = pending.timeControl;
        } else {
            if (pending.remainMs !== undefined) ws.whiteRemainMs = pending.remainMs;
            if (pending.byoyomiMs !== undefined) ws.byoyomiMsWhite = pending.byoyomiMs;
            if (pending.timeControl !== undefined) ws.timeControlWhite = pending.timeControl;
        }
        if (ws.pendingClockBySide) {
            delete ws.pendingClockBySide[side];
        }
    }
    ws.clockActive = side;
    ws.startedAtMs = nowMs;
}

export function syncClockToTurnBoundary(params: {
    ws: WorkerRuntimeState;
    currentPly: number | null | undefined;
    initialSfen: string | null | undefined;
    normalizeSFEN: (sfen: string) => string;
    nowMs: number;
}): void {
    const { ws, currentPly, initialSfen, normalizeSFEN, nowMs } = params;
    if (typeof currentPly !== 'number' || !Number.isFinite(currentPly)) return;

    const sideToMove = computeSideToMove(initialSfen, currentPly, normalizeSFEN);
    const lastSeenPly = ws.lastSeenPly;
    const lastSide = ws.lastSideToMove;
    const isNewPly = typeof lastSeenPly !== 'number' || lastSeenPly !== currentPly;
    const isNewSide = lastSide !== sideToMove;

    if (!isNewPly && !isNewSide) {
        return;
    }

    ws.lastSeenPly = currentPly;
    ws.lastSideToMove = sideToMove;
    applyPendingClockForSide(ws, sideToMove, nowMs);
}

export function maybeApplyImmediateClockStart(
    ws: WorkerRuntimeState,
    payload: ClockCorrectionPayload,
    nowMs: number,
): void {
    if (typeof ws.lastSeenPly === 'number') {
        return;
    }
    const active = normalizeClockSide(payload.active);
    if (!active) return;
    applyPendingClockForSide(ws, active, nowMs);
    if (ws.lastSideToMove == null) {
        ws.lastSideToMove = active;
    }
}
