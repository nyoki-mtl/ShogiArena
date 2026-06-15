import type { LiveViewMode, LiveViewProgressSnapshot, LiveViewSnapshot } from '@/modules/live/types/public';
import {
    normalizeOptionalBoolean,
    normalizeOptionalNullableNumber,
    normalizeOptionalNumber,
    normalizeOptionalString,
} from './common';

export function normalizeLiveViewSnapshot(raw: unknown, context = 'liveView'): LiveViewSnapshot | null {
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
        return null;
    }
    const obj = raw as Record<string, unknown>;
    const version = normalizeOptionalNumber(obj.version, `${context}.version`);
    const modeRaw = normalizeOptionalString(obj.mode, `${context}.mode`);
    const progress = normalizeLiveViewProgress(obj.progress, `${context}.progress`);
    const snapshot: LiveViewSnapshot = {
        version: version ?? null,
        mode: normalizeLiveViewMode(modeRaw),
        progress,
    };
    if (snapshot.version === null && snapshot.mode === 'unknown' && !snapshot.progress) {
        return null;
    }
    return snapshot;
}

function normalizeLiveViewMode(modeRaw: string | null | undefined): LiveViewMode {
    if (!modeRaw) {
        return 'unknown';
    }
    const lowered = modeRaw.trim().toLowerCase();
    if (lowered === 'spsa') {
        return 'spsa';
    }
    if (lowered === 'tournament') {
        return 'tournament';
    }
    if (lowered === 'match') {
        return 'match';
    }
    if (lowered === 'sprt') {
        return 'sprt';
    }
    return 'unknown';
}

function normalizeLiveViewProgress(raw: unknown, context: string): LiveViewProgressSnapshot | null {
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
        return null;
    }
    const obj = raw as Record<string, unknown>;
    const kindRaw = normalizeOptionalString(obj.kind, `${context}.kind`);
    const kind: LiveViewProgressSnapshot['kind'] =
        kindRaw === 'updates' || kindRaw === 'games' || kindRaw === 'match' || kindRaw === 'sprt' ? kindRaw : 'unknown';
    const unitLabel =
        normalizeOptionalString(obj.unit_label, `${context}.unit_label`) ?? (kind === 'updates' ? 'updates' : 'games');
    const completed = normalizeOptionalNullableNumber(obj.completed, `${context}.completed`);
    const total = normalizeOptionalNullableNumber(obj.total, `${context}.total`);
    const cancelled = normalizeOptionalNullableNumber(obj.cancelled, `${context}.cancelled`);
    const stateRaw = normalizeOptionalString(obj.state, `${context}.state`);
    const state =
        stateRaw && ['normal', 'paused', 'draining', 'finished'].includes(stateRaw)
            ? (stateRaw as 'normal' | 'paused' | 'draining' | 'finished')
            : undefined;
    const isFinal = normalizeOptionalBoolean(obj.is_final);
    const updatedAtRaw = obj.updated_at;
    let updatedAt: string | null | undefined;
    if (updatedAtRaw === null) {
        updatedAt = null;
    } else if (typeof updatedAtRaw === 'string') {
        updatedAt = updatedAtRaw;
    } else if (typeof updatedAtRaw === 'number' && Number.isFinite(updatedAtRaw)) {
        updatedAt = String(updatedAtRaw);
    }

    if (
        completed === undefined &&
        total === undefined &&
        cancelled === undefined &&
        state === undefined &&
        isFinal === undefined &&
        updatedAt === undefined &&
        kind === 'unknown'
    ) {
        return null;
    }

    // 進捗スナップショットは completed/total 必須（全モード共通）
    if (kind !== 'unknown' && (completed == null || total == null)) {
        throw new Error(`${context}: progress requires completed and total`);
    }

    return {
        kind,
        unit_label: unitLabel,
        completed: completed ?? null,
        total: total ?? null,
        cancelled: cancelled ?? null,
        is_final: isFinal,
        state,
        updated_at: updatedAt ?? null,
    } satisfies LiveViewProgressSnapshot;
}
