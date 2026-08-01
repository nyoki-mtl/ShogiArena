import type { LiveViewSnapshot } from '@/modules/live/types';
import type { SpsaDashboardState, NormalizedSpsaUpdateEntry, NormalizedSpsaSummary } from '@/modules/spsa/types';
import type { JsonObject } from '@/types/shared';

const ELEMENT_IDS = {
    variantValue: 'variantValue',
    updateCount: 'updateCount',
    bestEloValue: 'bestEloValue',
    bestEloConfidence: 'bestEloConfidence',
    progressValue: 'progressValue',
    etaValue: 'etaValue',
    deltaNormValue: 'deltaNormValue',
    deltaNormWindow: 'deltaNormWindow',
    operationalHealth: 'operationalHealth',
    operationalIdentity: 'operationalIdentity',
    operationalCompletion: 'operationalCompletion',
    operationalProgress: 'operationalProgress',
    operationalLtc: 'operationalLtc',
    operationalManifest: 'operationalManifest',
    operationalPreflight: 'operationalPreflight',
    operationalTunable: 'operationalTunable',
    operationalArtifact: 'operationalArtifact',
    operationalRemote: 'operationalRemote',
    operationalNodeMultiplier: 'operationalNodeMultiplier',
};

const elementCache = new Map<string, HTMLElement>();

function getElement(id: string): HTMLElement | null {
    const cached = elementCache.get(id);
    if (cached?.isConnected) {
        return cached;
    }
    const node = document.getElementById(id);
    if (node instanceof HTMLElement) {
        elementCache.set(id, node);
        return node;
    }
    return null;
}

function setText(id: string, value: string): void {
    const node = getElement(id);
    if (!node) return;
    node.textContent = value;
}

function formatSignedNumber(value: number | null | undefined, digits = 1): string {
    if (!Number.isFinite(value)) return '-';
    const numeric = Number(value);
    const threshold = 10 ** -digits / 2;
    const normalized = Math.abs(numeric) < threshold ? 0 : numeric;
    const fixed = normalized.toFixed(digits);
    if (normalized > 0) {
        return `+${fixed}`;
    }
    if (normalized < 0) {
        return fixed;
    }
    return (0).toFixed(digits);
}

function formatEta(seconds: number | null | undefined): string {
    if (!Number.isFinite(seconds)) return '-';
    const totalSeconds = Math.max(0, Math.floor(seconds as number));
    const hours = Math.floor(totalSeconds / 3600);
    const minutes = Math.floor((totalSeconds % 3600) / 60);
    if (hours === 0 && minutes === 0) {
        return '<1m';
    }
    return `${hours}h ${minutes}m`;
}

function formatGradient(value: number | null): string {
    if (!Number.isFinite(value)) return '-';
    const numeric = value as number;
    const epsilon = Number.EPSILON * Math.sign(numeric || 1);
    const rounded = Math.round((numeric + epsilon) * 10_000) / 10_000;
    return rounded.toFixed(4);
}

function formatConvergenceWindow(sampleSize: number | null, maxWindowSize: number = 20): string {
    if (sampleSize === null || !Number.isFinite(sampleSize) || sampleSize <= 0) {
        return `Moving avg (${maxWindowSize} updates)`;
    }
    const actualSize = Math.min(Math.max(1, Math.floor(sampleSize)), maxWindowSize);
    const plural = actualSize === 1 ? 'update' : 'updates';
    return `Moving avg (${actualSize} ${plural})`;
}

function formatGamesPlayed(summary: NormalizedSpsaSummary | null): string {
    const fallbackCount = summary ? summary.wins + summary.losses + summary.draws : null;
    if (fallbackCount === null || !Number.isFinite(fallbackCount)) {
        return '-';
    }
    try {
        return Number(fallbackCount).toLocaleString();
    } catch (_error) {
        return String(fallbackCount);
    }
}

function asObject(value: unknown): JsonObject {
    return value && typeof value === 'object' && !Array.isArray(value) ? (value as JsonObject) : {};
}

function textValue(value: unknown, fallback = 'Unknown'): string {
    if (typeof value === 'string' && value.trim()) return value.trim();
    if (typeof value === 'number' && Number.isFinite(value)) return String(value);
    if (typeof value === 'boolean') return value ? 'yes' : 'no';
    return fallback;
}

function shortDigest(value: unknown): string | null {
    if (typeof value !== 'string' || !value.trim()) return null;
    const normalized = value.trim();
    return normalized.length > 12 ? normalized.slice(0, 12) : normalized;
}

function joinStatus(parts: Array<string | null | undefined>, fallback = 'Unknown'): string {
    const visible = parts.filter((part): part is string => Boolean(part?.trim()));
    return visible.length > 0 ? visible.join(' · ') : fallback;
}

export function renderOperationalStatus(summary: NormalizedSpsaSummary | null): void {
    const operational = summary?.operationalStatus ?? {};
    const completion = asObject(operational.completion);
    const baseline = asObject(operational.accepted_baseline);
    const decision = asObject(operational.last_ltc_decision);
    const manifest = asObject(operational.manifest);
    const preflight = asObject(operational.fixed_option_preflight);
    const tunable = asObject(operational.tunable_manifest);
    const engineDigests = asObject(tunable.engine_digests);
    const artifact = asObject(operational.artifact_health);
    const ledger = asObject(operational.ledger);
    const remote = asObject(operational.remote_execution);
    const participation = asObject(remote.participation);
    const nodeMultiplier = asObject(operational.node_multiplier);

    const health = textValue(artifact.status, 'unknown').toLowerCase();
    const healthNode = getElement(ELEMENT_IDS.operationalHealth);
    if (healthNode) {
        healthNode.textContent = health === 'healthy' ? 'Healthy' : health === 'degraded' ? 'Degraded' : 'Unknown';
        healthNode.dataset.status = health === 'healthy' || health === 'degraded' ? health : 'unknown';
    }

    setText(
        ELEMENT_IDS.operationalIdentity,
        joinStatus([
            operational.run_id ? `run ${textValue(operational.run_id)}` : null,
            operational.session_id ? `session ${textValue(operational.session_id)}` : null,
        ]),
    );
    setText(
        ELEMENT_IDS.operationalCompletion,
        joinStatus([
            textValue(completion.status),
            completion.termination_reason ? textValue(completion.termination_reason) : null,
            completion.resumable === true ? 'resumable' : null,
        ]),
    );
    setText(
        ELEMENT_IDS.operationalProgress,
        joinStatus([
            completion.last_committed_update !== undefined
                ? `committed ${textValue(completion.last_committed_update)}`
                : null,
            completion.pending_update !== null && completion.pending_update !== undefined
                ? `pending ${textValue(completion.pending_update)}`
                : null,
            completion.pending_stage ? textValue(completion.pending_stage) : null,
        ]),
    );
    setText(
        ELEMENT_IDS.operationalLtc,
        joinStatus([
            baseline.update_idx !== undefined ? `baseline ${textValue(baseline.update_idx)}` : null,
            decision.decision ? `${textValue(decision.decision)} @${textValue(decision.tested_update_idx)}` : null,
        ]),
    );
    setText(
        ELEMENT_IDS.operationalManifest,
        joinStatus([
            textValue(manifest.status),
            manifest.schema_version !== null && manifest.schema_version !== undefined
                ? `schema ${textValue(manifest.schema_version)}`
                : null,
            shortDigest(manifest.resume_hash) ? `resume ${shortDigest(manifest.resume_hash)}` : null,
        ]),
    );
    const scopes = Array.isArray(preflight.evidence_scopes)
        ? preflight.evidence_scopes.map((scope) => textValue(scope)).join(',')
        : null;
    setText(
        ELEMENT_IDS.operationalPreflight,
        joinStatus([textValue(preflight.status), scopes ? `scope ${scopes}` : null]),
    );
    setText(
        ELEMENT_IDS.operationalTunable,
        joinStatus([
            textValue(tunable.status),
            tunable.runtime_scope ? textValue(tunable.runtime_scope) : null,
            shortDigest(tunable.evidence_digest) ? `evidence ${shortDigest(tunable.evidence_digest)}` : null,
            Object.entries(engineDigests).length > 0
                ? `engine ${Object.entries(engineDigests)
                      .map(([name, digest]) => `${name}:${shortDigest(digest) ?? 'missing'}`)
                      .join(',')}`
                : null,
        ]),
    );
    setText(
        ELEMENT_IDS.operationalArtifact,
        joinStatus([
            textValue(artifact.status),
            ledger.schema_version ? `ledger ${textValue(ledger.schema_version)}` : null,
            artifact.revision !== null && artifact.revision !== undefined
                ? `revision ${textValue(artifact.revision)}`
                : null,
        ]),
    );
    setText(
        ELEMENT_IDS.operationalRemote,
        remote.status === 'observed'
            ? joinStatus([
                  remote.endpoint_identity ? `endpoint ${textValue(remote.endpoint_identity)}` : null,
                  remote.deployment_id ? `deployment ${textValue(remote.deployment_id)}` : null,
                  remote.job_id ? `job ${textValue(remote.job_id)}` : null,
                  participation.pair_id ? `pair ${textValue(participation.pair_id)}` : null,
                  participation.attempt_id ? `attempt ${textValue(participation.attempt_id)}` : null,
              ])
            : 'Not observed',
    );
    setText(
        ELEMENT_IDS.operationalNodeMultiplier,
        nodeMultiplier.status === 'applied'
            ? `applied ×${textValue(nodeMultiplier.value)}`
            : textValue(nodeMultiplier.status, 'unknown'),
    );
}

function formatUpdateSummary(
    completed: number,
    total: number,
    etaSeconds: number | null | undefined,
    unitLabel: string,
): string {
    const parts: string[] = [];
    if (total > 0) {
        const percent = Math.round((completed / total) * 100);
        parts.push(`${completed}/${total} ${unitLabel} (${percent}%)`);
    } else {
        parts.push(`${completed} ${unitLabel}`);
    }
    const eta = formatEta(etaSeconds);
    if (eta !== '-') {
        parts.push(`ETA ${eta}`);
    }
    return parts.join(' · ') || '-';
}

function resolveLiveViewProgress(liveView: LiveViewSnapshot | null | undefined): LiveViewSnapshot['progress'] | null {
    if (!liveView || !liveView.progress) {
        return null;
    }
    if (liveView.mode === 'spsa') {
        return liveView.progress;
    }
    return liveView.progress.kind === 'updates' ? liveView.progress : null;
}

function normalizeVariantToken(value: unknown): string | null {
    if (typeof value !== 'string') return null;
    const trimmed = value.trim();
    return trimmed.length > 0 ? trimmed : null;
}

type VariantInfo = {
    token: string | null;
    updateIdx: number | null;
    ltcElo: number | null;
};

function resolveVariantInfo(state: SpsaDashboardState): VariantInfo {
    // Find the latest LTC-accepted variant (highest update_idx with accepted === true)
    let bestAccepted: NormalizedSpsaUpdateEntry | null = null;
    let bestUpdateIdx = -1;

    for (const entry of state.updates.entries) {
        if (entry.ltcRegression?.accepted === true) {
            const updateIdx = Number.isFinite(entry.updateIdx) ? (entry.updateIdx as number) : -1;
            if (updateIdx > bestUpdateIdx) {
                bestUpdateIdx = updateIdx;
                bestAccepted = entry;
            }
        }
    }

    if (bestAccepted) {
        const token =
            normalizeVariantToken(bestAccepted.ltcRegression?.tunedVariantToken) ??
            normalizeVariantToken(bestAccepted.variantId);
        const idx = Number.isFinite(bestAccepted.updateIdx) ? (bestAccepted.updateIdx as number) : null;
        const ltcElo = bestAccepted.ltcRegression?.elo ?? null;
        if (token) {
            return { token, updateIdx: idx, ltcElo };
        }
    }

    // If no LTC-accepted variant exists, return initial params (v000000)
    return { token: 'v000000', updateIdx: -1, ltcElo: null };
}

function resolveLatestUpdate(updates: readonly NormalizedSpsaUpdateEntry[]): NormalizedSpsaUpdateEntry | null {
    if (!updates.length) return null;
    return updates[0] ?? null;
}

function resolveDeltaNorm(entry: NormalizedSpsaUpdateEntry | null): number | null {
    if (!entry) return null;
    return Number.isFinite(entry.deltaNorm) ? (entry.deltaNorm as number) : null;
}

type ConvergenceDeltaInfo = {
    value: number | null;
    isMovingAverage: boolean;
};

function resolveConvergenceDelta(
    state: SpsaDashboardState,
    latestUpdate: NormalizedSpsaUpdateEntry | null,
): ConvergenceDeltaInfo {
    const metrics = state.convergenceMetrics;
    if (metrics) {
        const { recentAvgMeanVectorDeltaNorm, recentMeanVectorDeltaNorm, recentAvgDeltaNorm } = metrics;
        if (typeof recentAvgMeanVectorDeltaNorm === 'number' && Number.isFinite(recentAvgMeanVectorDeltaNorm)) {
            return { value: recentAvgMeanVectorDeltaNorm, isMovingAverage: true };
        }
        if (typeof recentMeanVectorDeltaNorm === 'number' && Number.isFinite(recentMeanVectorDeltaNorm)) {
            return { value: recentMeanVectorDeltaNorm, isMovingAverage: true };
        }
        if (typeof recentAvgDeltaNorm === 'number' && Number.isFinite(recentAvgDeltaNorm)) {
            return { value: recentAvgDeltaNorm, isMovingAverage: true };
        }
    }
    // Fallback to latest update's deltaNorm
    const fallbackValue = resolveDeltaNorm(latestUpdate);
    return { value: fallbackValue, isMovingAverage: false };
}

export function renderHero(state: SpsaDashboardState, liveView?: LiveViewSnapshot | null): void {
    const summary = state.summary.data ?? null;
    const latestUpdate = resolveLatestUpdate(state.updates.entries);
    const liveViewProgress = resolveLiveViewProgress(liveView ?? null);

    // Best Variant (leftmost) - shows latest LTC-accepted variant or v000000
    const variantInfo = resolveVariantInfo(state);
    setText(ELEMENT_IDS.variantValue, variantInfo.token ?? '-');

    const updateIdx = variantInfo.updateIdx;
    if (updateIdx !== null && updateIdx >= 0) {
        setText(ELEMENT_IDS.updateCount, `Update ${updateIdx}`);
    } else if (updateIdx === -1) {
        setText(ELEMENT_IDS.updateCount, 'Initial Params');
    } else {
        setText(ELEMENT_IDS.updateCount, '-');
    }

    // Best vs Initial Elo - unified with Best Variant (latest accepted LTC result)
    // Use the LTC Elo from the same entry as the Best Variant for consistency
    const ltcElo = variantInfo.ltcElo;
    if (ltcElo !== null && Number.isFinite(ltcElo)) {
        setText(ELEMENT_IDS.bestEloValue, formatSignedNumber(ltcElo, 1));
        // Show context: this is the LTC Elo from the latest accepted update
        setText(ELEMENT_IDS.bestEloConfidence, 'LTC Elo');
    } else {
        setText(ELEMENT_IDS.bestEloValue, '-');
        setText(ELEMENT_IDS.bestEloConfidence, '-');
    }

    // Games Played
    const progressSnapshot = liveViewProgress ?? state.updates.progress;
    const updatesCompleted =
        liveViewProgress?.completed ?? summary?.updatesCompleted ?? progressSnapshot?.completed ?? 0;
    const numUpdates = liveViewProgress?.total ?? summary?.numUpdates ?? progressSnapshot?.total ?? 0;
    const unitLabelRaw = typeof liveViewProgress?.unit_label === 'string' ? liveViewProgress.unit_label : 'updates';
    const unitLabel = unitLabelRaw.trim() || 'updates';
    setText(ELEMENT_IDS.progressValue, formatGamesPlayed(summary));

    const etaSeconds = summary?.etaSeconds ?? null;
    setText(ELEMENT_IDS.etaValue, formatUpdateSummary(updatesCompleted, numUpdates, etaSeconds, unitLabel));

    // Param Convergence (formerly ‖meanΔ‖)
    const deltaInfo = resolveConvergenceDelta(state, latestUpdate);
    setText(ELEMENT_IDS.deltaNormValue, formatGradient(deltaInfo.value));

    if (deltaInfo.isMovingAverage) {
        const sampleSize = state.convergenceMetrics?.sampleSize ?? null;
        setText(ELEMENT_IDS.deltaNormWindow, formatConvergenceWindow(sampleSize, 20));
    } else {
        setText(ELEMENT_IDS.deltaNormWindow, 'Latest update');
    }
    renderOperationalStatus(summary);
}
