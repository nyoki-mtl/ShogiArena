import type { SseSummaryPayload } from '@/modules/live/types/public';
import type { JsonObject, MutableJsonObject } from '@/types/shared';
import { ensureObject, normalizeOptionalNumber, normalizeOptionalString, normalizeRequiredString } from './common';
import { normalizeLiveViewSnapshot } from './live-view';

export function normalizeSummaryPayload(raw: unknown): SseSummaryPayload {
    if (raw == null) {
        throw new Error('summary payload is missing');
    }
    const obj = ensureObject(raw, 'summary payload');

    const next: MutableJsonObject = { ...obj };

    if ('default_time_control' in next) {
        const normalized = normalizeOptionalString(next.default_time_control, 'summary.default_time_control');
        if (normalized === undefined) {
            delete next.default_time_control;
        } else {
            next.default_time_control = normalized ?? null;
        }
    }

    if ('engine_time_controls' in next) {
        const controls = next.engine_time_controls;
        if (!controls || typeof controls !== 'object' || Array.isArray(controls)) {
            throw new Error('summary payload engine_time_controls must be an object');
        }
        const record: Record<string, string> = {};
        for (const [key, value] of Object.entries(controls as JsonObject)) {
            record[normalizeRequiredString(key, 'summary.engine_time_controls key')] = normalizeRequiredString(
                value,
                `summary.engine_time_controls[${String(key)}]`,
            );
        }
        next.engine_time_controls = record;
    }

    const numericFields: Array<keyof SseSummaryPayload> = ['games_completed', 'games_scheduled'];
    for (const field of numericFields) {
        if (field in next) {
            const num = normalizeOptionalNumber(next[field], `summary.${String(field)}`);
            if (num === undefined) {
                delete next[field];
            } else {
                next[field] = num;
            }
        }
    }

    const hasLiveView = Object.hasOwn(next, 'live_view');
    if (next.live_view === null) {
        next.live_view = null;
    } else {
        const liveViewSnapshot = normalizeLiveViewSnapshot(next.live_view, 'summary.live_view');
        if (liveViewSnapshot) {
            next.live_view = liveViewSnapshot;
        } else if (hasLiveView) {
            delete next.live_view;
        }
    }

    return next as SseSummaryPayload;
}
