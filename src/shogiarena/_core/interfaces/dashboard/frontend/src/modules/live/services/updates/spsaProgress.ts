import { SPSA_SUMMARY_PROGRESS_EVENT } from '@/modules/spsa/constants';
import type { LiveUpdatesContext, SseSummaryPayload } from '@/modules/live/types/updates';
import { normalizeSummaryPayload } from './normalizers';
import type { JsonObject, MutableJsonObject } from '@/types/shared';
import { applyLiveViewSnapshotIfPresent } from './common';

interface SpsaProgressDeps {
    ctx: LiveUpdatesContext;
    safeClone: <T>(value: T) => T;
    emitEvent: (eventName: string, payload: unknown) => void;
}

export function createSpsaProgressHandler({ ctx, safeClone, emitEvent }: SpsaProgressDeps) {
    const { state } = ctx;

    function propagateSpsaSummary(summaryPayload: SseSummaryPayload): void {
        const progress = summaryPayload.liveView?.progress;
        const isSpsaProgress =
            progress &&
            ((summaryPayload.liveView?.mode ?? 'unknown') === 'spsa' || (progress as JsonObject).kind === 'updates');
        if (!isSpsaProgress) {
            return;
        }

        const coerceNumber = (value: unknown): number | null => {
            if (typeof value === 'number' && Number.isFinite(value)) return value;
            return null;
        };

        const completed = coerceNumber(progress?.completed);
        const total = coerceNumber(progress?.total);

        if (completed === null || total === null) {
            throw new Error('SPSA progress payload missing required completed/total');
        }

        const snapshot: MutableJsonObject = {
            completed,
            total,
        };

        state.spsaSummary = snapshot;
        emitEvent(SPSA_SUMMARY_PROGRESS_EVENT, safeClone(snapshot));
    }

    function onSummaryUpdate(payload: SseSummaryPayload | null | undefined): void {
        const summaryPayload = normalizeSummaryPayload(payload ?? {});
        const hasLiveView = Object.hasOwn(summaryPayload, 'liveView');
        applyLiveViewSnapshotIfPresent({
            state,
            liveView: hasLiveView ? summaryPayload.liveView : undefined,
            source: 'summary',
            safeClone,
            emitEvent,
        });
        emitEvent('summary:update', { summary: safeClone(summaryPayload) });
        propagateSpsaSummary(summaryPayload);
    }

    return { onSummaryUpdate } as const;
}
