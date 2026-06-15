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
    started_at_ms?: unknown;
    occurred_at_ms?: unknown;
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

function readField(payload: ClockCorrectionPayload, key: string): unknown {
    return (payload as Record<string, unknown>)[key];
}

function computeSideToMove(
    initialSfen: string | null | undefined,
    current_ply: number,
    normalizeSFEN: (sfen: string) => string,
): ClockSide {
    const normalized = normalizeSFEN(initialSfen ?? DEFAULT_INITIAL_SFEN);
    const parts = normalized.split(' ');
    const startIsBlack = parts.length > 1 ? parts[1] === 'b' : true;
    const sideToMoveIsBlack = startIsBlack ? current_ply % 2 === 0 : current_ply % 2 === 1;
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

    const blackRemain = coerceNumber(readField(payload, 'black_remain_ms'));
    const whiteRemain = coerceNumber(readField(payload, 'white_remain_ms'));
    const blackByoyomi = coerceNumber(readField(payload, 'byoyomi_ms_black'));
    const whiteByoyomi = coerceNumber(readField(payload, 'byoyomi_ms_white'));
    const blackTc = coerceString(readField(payload, 'time_control_black'));
    const whiteTc = coerceString(readField(payload, 'time_control_white'));
    const startedAtMs = coerceNumber(readField(payload, 'started_at_ms'));
    const occurredAtMs = coerceNumber(readField(payload, 'occurred_at_ms'));
    const clockAtMs = source === 'clock_start' ? startedAtMs : occurredAtMs;

    if (blackRemain !== undefined || blackByoyomi !== undefined || blackTc !== undefined) {
        const existing = ws.pendingClockBySide.black;
        ws.pendingClockBySide.black = {
            remainMs: blackRemain ?? existing?.remainMs,
            byoyomiMs: blackByoyomi ?? existing?.byoyomiMs,
            timeControl: blackTc ?? existing?.timeControl,
            clockAtMs: clockAtMs ?? existing?.clockAtMs,
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
            clockAtMs: clockAtMs ?? existing?.clockAtMs,
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

function seedClockFromTimeControlIfMissing(
    ws: WorkerRuntimeState,
    side: ClockSide,
    parsed: ParsedTimeControlSpec,
): void {
    const initialMs =
        parsed.mode === 'fixed'
            ? Math.max(0, Math.trunc(parsed.fixedMs || 0))
            : Math.max(0, Math.trunc((parsed.initial || 0) * 1000));
    const byoyomiMs = Math.max(0, Math.trunc((parsed.byoyomi || 0) * 1000));
    if (side === 'black') {
        if (ws.blackRemainMs == null && initialMs > 0) {
            ws.blackRemainMs = initialMs;
        }
        if (ws.byoyomiMsBlack == null && byoyomiMs > 0) {
            ws.byoyomiMsBlack = byoyomiMs;
        }
    } else {
        if (ws.whiteRemainMs == null && initialMs > 0) {
            ws.whiteRemainMs = initialMs;
        }
        if (ws.byoyomiMsWhite == null && byoyomiMs > 0) {
            ws.byoyomiMsWhite = byoyomiMs;
        }
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
        seedClockFromTimeControlIfMissing(ws, 'black', parsed);
        blackMode = parsed.mode;
        touched = true;
    }
    if (tcWhiteRaw) {
        const parsed = parseTimeControlSpec(tcWhiteRaw) as ParsedTimeControlSpec;
        applyParsedTimeControl(ws, 'white', parsed);
        seedClockFromTimeControlIfMissing(ws, 'white', parsed);
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

export function resetWorkerRuntimeClockStateForNewGame(ws: WorkerRuntimeState): void {
    ws.lastSeenPly = undefined;
    ws.lastSideToMove = undefined;
    ws.pendingClockBySide = undefined;
    ws.clockActive = null;
    ws.startedAtMs = undefined;
    ws.blackRemainMs = undefined;
    ws.whiteRemainMs = undefined;
    ws.timeControlBlack = undefined;
    ws.timeControlWhite = undefined;
    ws.byoyomiMsBlack = undefined;
    ws.byoyomiMsWhite = undefined;
    ws.blackFrozenByoText = null;
    ws.whiteFrozenByoText = null;
    ws.tcBlackHasTimePool = undefined;
    ws.tcWhiteHasTimePool = undefined;
    ws.tcBlackHasSearchLimit = undefined;
    ws.tcWhiteHasSearchLimit = undefined;
    ws.clockDisplayMode = undefined;
}

function applyPendingClockForSide(
    ws: WorkerRuntimeState,
    side: ClockSide,
    nowMs: number,
    options?: { activate?: boolean },
): void {
    const pending = ws.pendingClockBySide?.[side];
    const activate = options?.activate !== false;
    const clockAtMs = Number(pending?.clockAtMs ?? 0);
    const hasClockAnchor = clockAtMs > 0;
    const currentStartedAt = Number(ws.startedAtMs ?? 0);
    const hasRunningAnchor = currentStartedAt > 0;
    if (pending && hasClockAnchor && hasRunningAnchor && clockAtMs < currentStartedAt) {
        if (ws.pendingClockBySide) {
            delete ws.pendingClockBySide[side];
        }
        return;
    }
    if (pending) {
        const allowRemainOverwrite = hasClockAnchor || !hasRunningAnchor;
        if (side === 'black') {
            if (allowRemainOverwrite && pending.remainMs !== undefined) ws.blackRemainMs = pending.remainMs;
            if (pending.byoyomiMs !== undefined) ws.byoyomiMsBlack = pending.byoyomiMs;
            if (pending.timeControl !== undefined) ws.timeControlBlack = pending.timeControl;
        } else {
            if (allowRemainOverwrite && pending.remainMs !== undefined) ws.whiteRemainMs = pending.remainMs;
            if (pending.byoyomiMs !== undefined) ws.byoyomiMsWhite = pending.byoyomiMs;
            if (pending.timeControl !== undefined) ws.timeControlWhite = pending.timeControl;
        }
        if (ws.pendingClockBySide) {
            delete ws.pendingClockBySide[side];
        }
    }
    if (activate) {
        ws.clockActive = side;
        if (hasClockAnchor) {
            ws.startedAtMs = clockAtMs;
        } else if (!hasRunningAnchor) {
            ws.startedAtMs = nowMs;
        }
    }
}

export function syncClockToTurnBoundary(params: {
    ws: WorkerRuntimeState;
    current_ply: number | null | undefined;
    initialSfen: string | null | undefined;
    normalizeSFEN: (sfen: string) => string;
    nowMs: number;
}): void {
    const { ws, current_ply, initialSfen, normalizeSFEN, nowMs } = params;
    if (typeof current_ply !== 'number' || !Number.isFinite(current_ply)) return;

    const sideToMove = computeSideToMove(initialSfen, current_ply, normalizeSFEN);
    const lastSeenPly = ws.lastSeenPly;
    const lastSide = ws.lastSideToMove;
    const isNewPly = typeof lastSeenPly !== 'number' || lastSeenPly !== current_ply;
    const isNewSide = lastSide !== sideToMove;

    if (!isNewPly && !isNewSide) {
        return;
    }

    ws.lastSeenPly = current_ply;
    ws.lastSideToMove = sideToMove;
    const otherSide: ClockSide = sideToMove === 'black' ? 'white' : 'black';
    // Keep both clocks in sync at move boundaries (ShogiHome-style).
    applyPendingClockForSide(ws, otherSide, nowMs, { activate: false });
    applyPendingClockForSide(ws, sideToMove, nowMs, { activate: true });
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
