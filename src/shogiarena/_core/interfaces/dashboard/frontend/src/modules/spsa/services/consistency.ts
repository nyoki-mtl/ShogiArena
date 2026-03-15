import { reportDashboardRecoverableFailure } from '@/modules/shared/utils/errors';
import type { NormalizedSpsaSummary, NormalizedSpsaUpdateDetail } from '@/modules/spsa/types';

export type SnapshotSource = 'sse' | 'rest';

export class SpsaConsistencyError extends Error {
    constructor(message: string) {
        super(message);
        this.name = 'SpsaConsistencyError';
    }
}

export function isConsistencyError(error: unknown): error is SpsaConsistencyError {
    return error instanceof SpsaConsistencyError;
}

let summarySnapshots: Partial<Record<SnapshotSource, NormalizedSpsaSummary>> = {};

export function resetSpsaConsistencyState(): void {
    summarySnapshots = {};
}

function reportAndThrow(scope: string, message: string): never {
    const error = new SpsaConsistencyError(message);
    reportDashboardRecoverableFailure(error, { scope });
    throw error;
}

function buildEngineSet(summary: NormalizedSpsaSummary): Set<string> {
    const names = new Set<string>();
    const engineList = summary.engines ?? [];
    engineList.forEach((name) => {
        const trimmed = (name ?? '').trim();
        if (trimmed) {
            names.add(trimmed);
        }
    });
    Object.keys(summary.engineTimeControls ?? {}).forEach((key) => {
        const trimmed = key.trim();
        if (trimmed) {
            names.add(trimmed);
        }
    });
    return names;
}

function detectSummaryConflict(next: NormalizedSpsaSummary, other: NormalizedSpsaSummary): string | null {
    const mismatches: string[] = [];
    if (next.variantId && other.variantId && next.variantId !== other.variantId) {
        mismatches.push(`variant_id mismatch (${next.variantId} vs ${other.variantId})`);
    }
    const nextEngines = buildEngineSet(next);
    const otherEngines = buildEngineSet(other);
    if (nextEngines.size === 0 && otherEngines.size === 0) {
        return mismatches.length ? mismatches.join('; ') : null;
    }
    if (nextEngines.size !== otherEngines.size) {
        mismatches.push(`engine set size differs (${nextEngines.size} vs ${otherEngines.size})`);
    }
    for (const engine of nextEngines) {
        if (!otherEngines.has(engine)) {
            mismatches.push(`engine '${engine}' missing on counterpart`);
        }
    }
    for (const engine of otherEngines) {
        if (!nextEngines.has(engine)) {
            mismatches.push(`engine '${engine}' missing on incoming snapshot`);
        }
    }
    return mismatches.length ? mismatches.join('; ') : null;
}

export function mergeSpsaSummarySnapshot(
    snapshot: NormalizedSpsaSummary,
    source: SnapshotSource,
): NormalizedSpsaSummary {
    const counterpart = source === 'sse' ? summarySnapshots.rest : summarySnapshots.sse;
    summarySnapshots[source] = snapshot;
    if (!counterpart) {
        return snapshot;
    }
    const conflict = detectSummaryConflict(snapshot, counterpart);
    if (conflict) {
        reportAndThrow('SpsaConsistency.Summary', conflict);
    }
    return source === 'sse' ? snapshot : counterpart;
}

export function assertUpdateDetailConsistency(
    incoming: NormalizedSpsaUpdateDetail,
    existing: NormalizedSpsaUpdateDetail,
    source: SnapshotSource,
): void {
    if (incoming.updateIdx !== existing.updateIdx) {
        reportAndThrow(
            'SpsaConsistency.UpdateDetail',
            `Update detail index mismatch (${incoming.updateIdx} vs ${existing.updateIdx})`,
        );
    }
    const mismatches: string[] = [];
    if (incoming.variantId && existing.variantId && incoming.variantId !== existing.variantId) {
        mismatches.push(`variant_id ${incoming.variantId} vs ${existing.variantId}`);
    }
    const baselineNext = incoming.engines.baseline ?? null;
    const baselineExisting = existing.engines.baseline ?? null;
    if (baselineNext && baselineExisting && baselineNext !== baselineExisting) {
        mismatches.push(`baseline ${baselineNext} vs ${baselineExisting}`);
    }
    const tunedNext = incoming.engines.tuned ?? null;
    const tunedExisting = existing.engines.tuned ?? null;
    if (tunedNext && tunedExisting && tunedNext !== tunedExisting) {
        mismatches.push(`tuned ${tunedNext} vs ${tunedExisting}`);
    }
    if (!mismatches.length) {
        return;
    }
    const prefix = source === 'sse' ? 'SSE vs cached' : 'REST vs cached';
    reportAndThrow(
        'SpsaConsistency.UpdateDetail',
        `${prefix} detail mismatch for update ${incoming.updateIdx}: ${mismatches.join('; ')}`,
    );
}
