import type { LiveViewMode, LiveViewProgressSnapshot, LiveViewSnapshot } from '@/modules/live/types/public';
import { normalizeRuntimeMode } from '@/modules/shared/services/runtime-mode';
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

/**
 * Does this snapshot's progress count games, so a caller may read completed/total
 * as a game tally?
 *
 * The signal is the progress block's own `kind`. The `tournament` mode check is
 * a fallback for older payloads that carry no kind — not a list of modes allowed
 * to have game progress. Written the other way round, a mode whose progress is
 * game-counted passes only by the accident of its kind, which is how the CSA
 * summary got through before `'csa'` was a mode the page knew at all.
 */
export function hasGameCountedProgress(snapshot: LiveViewSnapshot | null | undefined): boolean {
    if (!snapshot?.progress) return false;
    return snapshot.progress.kind === 'games' || snapshot.mode === 'tournament';
}

/**
 * The live view names the same modes the dashboard does, minus `generate`
 * (which has no live view of its own). Deriving from the shared catalogue keeps
 * a newly added mode from silently arriving here as `unknown`.
 */
function normalizeLiveViewMode(modeRaw: string | null | undefined): LiveViewMode {
    const normalized = normalizeRuntimeMode(modeRaw);
    return normalized === 'generate' ? 'unknown' : normalized;
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
