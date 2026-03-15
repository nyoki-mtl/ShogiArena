import { normalizeLiveViewSnapshot } from '@/modules/live/services/updates/normalizers/liveView';
import type { TournamentSummary } from '@/modules/tournament/types';
import type { MutableJsonObject } from '@/types/shared';

// Validate incoming summary payloads from SSE before mutating global state.
// This keeps the live summary renderer aligned with sse_contract.md:
// liveView.progress must include completed/total for all modes and legacy/undefined
// shapes are rejected early so the stream fail-fast behaviour is consistent.
export function normalizeSummaryEventPayload(raw: unknown): TournamentSummary {
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
        throw new Error('summary event payload must be an object');
    }

    const next = { ...(raw as TournamentSummary) } as MutableJsonObject;

    if (!('liveView' in next)) {
        throw new Error('summary.liveView is required');
    }

    const normalized = normalizeLiveViewSnapshot(next.liveView, 'summary.liveView');
    if (!normalized || !normalized.progress) {
        throw new Error('summary.liveView.progress is required');
    }
    next.liveView = normalized;

    return next as TournamentSummary;
}
