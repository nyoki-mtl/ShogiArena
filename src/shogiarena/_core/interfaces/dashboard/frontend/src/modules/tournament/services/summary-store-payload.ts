import type { NormalizedTournamentSummary, TournamentSummary } from '@/modules/tournament/types';
import type { JsonObject } from '@/types/shared';

export function buildTournamentStoreSummaryPayload(
    rawSummary: TournamentSummary,
    normalized: NormalizedTournamentSummary,
): JsonObject {
    // Drop the raw engines_meta array before spreading: the store's extractEngineMeta prefers
    // the array form, which would otherwise override the normalized engine_meta object set below.
    const { engines_meta: _enginesMeta, ...rest } = rawSummary as unknown as JsonObject;
    return {
        ...rest,
        engines: [...normalized.engines],
        engine_meta: normalized.engineMeta as unknown as JsonObject,
        engine_time_controls: { ...normalized.engineTimeControls },
        engine_instances: { ...normalized.engineInstances },
        default_time_control: normalized.defaultTimeControl,
    };
}
