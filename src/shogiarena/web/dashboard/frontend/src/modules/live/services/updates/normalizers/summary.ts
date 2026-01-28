import type { SseSummaryPayload } from '@/modules/live/types/public';
import type { JsonObject, MutableJsonObject } from '@/types/shared';
import { ensureObject, normalizeOptionalNumber, normalizeOptionalString, normalizeRequiredString } from './common';
import { normalizeLiveViewSnapshot } from './liveView';

export function normalizeSummaryPayload(raw: unknown): SseSummaryPayload {
    if (raw == null) {
        return {} as SseSummaryPayload;
    }
    const obj = ensureObject(raw, 'summary payload');

    const next: MutableJsonObject = { ...obj };

    if ('defaultTimeControl' in next) {
        const normalized = normalizeOptionalString(next.defaultTimeControl, 'summary.defaultTimeControl');
        if (normalized === undefined) {
            delete next.defaultTimeControl;
        } else {
            next.defaultTimeControl = normalized ?? null;
        }
    }

    if ('engineTimeControls' in next) {
        const controls = next.engineTimeControls;
        if (!controls || typeof controls !== 'object' || Array.isArray(controls)) {
            throw new Error('summary payload engineTimeControls must be an object');
        }
        const record: Record<string, string> = {};
        for (const [key, value] of Object.entries(controls as JsonObject)) {
            record[normalizeRequiredString(key, 'summary.engineTimeControls key')] = normalizeRequiredString(
                value,
                `summary.engineTimeControls[${String(key)}]`,
            );
        }
        next.engineTimeControls = record;
    }

    const numericFields: Array<keyof SseSummaryPayload> = ['gamesCompleted', 'gamesScheduled'];
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

    const hasLiveView = Object.hasOwn(next, 'liveView');
    if (next.liveView === null) {
        next.liveView = null;
    } else {
        const liveViewSnapshot = normalizeLiveViewSnapshot(next.liveView, 'summary.liveView');
        if (liveViewSnapshot) {
            next.liveView = liveViewSnapshot;
        } else if (hasLiveView) {
            delete next.liveView;
        }
    }

    return next as SseSummaryPayload;
}
