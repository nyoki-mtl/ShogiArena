import type { NormalizedTournamentSummary, TournamentSummary } from '@/modules/tournament/types';
import type { JsonObject } from '@/types/shared';

export function buildTournamentStoreSummaryPayload(
    rawSummary: TournamentSummary,
    normalized: NormalizedTournamentSummary,
): JsonObject {
    return {
        ...(rawSummary as unknown as JsonObject),
        engines: [...normalized.engines],
        engineMeta: normalized.engineMeta as unknown as JsonObject,
        engineTimeControls: { ...normalized.engineTimeControls },
        engineInstances: { ...normalized.engineInstances },
        defaultTimeControl: normalized.defaultTimeControl,
    };
}
