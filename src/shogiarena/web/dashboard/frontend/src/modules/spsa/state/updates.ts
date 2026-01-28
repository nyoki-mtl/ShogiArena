import { normalizeSpsaUpdates, normalizeUpdatesProgress } from '@/modules/spsa/services/normalizers';
import { INITIAL_UPDATE_IDX } from '@/modules/spsa/types';
import type {
    NormalizedSpsaUpdateDetail,
    NormalizedSpsaUpdateEntry,
    SpsaDetailInclude,
    SpsaUpdateEntry,
    SpsaUpdateProgress,
    SpsaUpdatesResponse,
} from '@/modules/spsa/types';
import type { JsonObject } from '@/types/shared';

import {
    SpsaConsistencyError,
    assertUpdateDetailConsistency,
    type SnapshotSource,
} from '@/modules/spsa/services/consistency';

import { clearInitialUpdateArtifacts, getInitialUpdateDetail as getInitialDetail } from './initial-artifacts';
import { state, MAX_TREND_POINTS, MAX_UPDATE_CACHE_ENTRIES } from './store';
import type { SpsaPhaseWdlEntry, SpsaPhaseWdlMap } from '@/modules/spsa/types';

export function resetUpdates(): void {
    state.updates.entries = [];
    state.updates.total = 0;
    state.updates.offset = 0;
    state.updates.hasMore = false;
    state.updates.lastUpdateIdx = -1;
    state.updates.cache.clear();
    clearInitialUpdateArtifacts();
    state.updates.detailCache.clear();
    state.updates.expanded.clear();
    state.updates.progress = null;
    resetTrend();
}

const normalizeCount = (value: unknown, label: string, strict: boolean, min = 0): number => {
    const num = Number(value);
    if (!Number.isFinite(num)) {
        if (strict) {
            throw new SpsaConsistencyError(`SpsaUpdatesResponse.${label} must be a finite number`);
        }
        return Math.max(min, 0);
    }
    const normalized = Math.trunc(num);
    if (strict && normalized < min) {
        throw new SpsaConsistencyError(`SpsaUpdatesResponse.${label} must be >= ${min}`);
    }
    return Math.max(min, normalized);
};

const normalizeHasMore = (value: unknown, strict: boolean): boolean => {
    if (typeof value === 'boolean') {
        return value;
    }
    if (strict) {
        throw new SpsaConsistencyError('SpsaUpdatesResponse.has_more must be boolean');
    }
    return Boolean(value);
};

export function applyUpdatesResponse(response: SpsaUpdatesResponse, options: { strict?: boolean } = {}): void {
    const strict = Boolean(options.strict);
    const limit = normalizeCount(response.limit, 'limit', strict, 1);
    const total = normalizeCount(response.total, 'total', strict, 0);
    const offset = normalizeCount(response.offset, 'offset', strict, 0);
    const hasMore = normalizeHasMore(response.has_more, strict);
    const progressSnapshot = normalizeUpdatesProgress(response.progress, { strict });

    const updatesArray = Array.isArray(response.updates)
        ? response.updates
        : (() => {
              if (strict) {
                  throw new SpsaConsistencyError('SpsaUpdatesResponse.updates must be an array');
              }
              return [] as SpsaUpdateEntry[];
          })();
    const normalizedUpdates = normalizeSpsaUpdates(updatesArray, { strict });
    if (strict && normalizedUpdates.length > limit) {
        throw new SpsaConsistencyError(
            `SpsaUpdatesResponse: updates length ${normalizedUpdates.length} exceeds limit ${limit}`,
        );
    }

    if (progressSnapshot) {
        updateProgressSnapshot(progressSnapshot);
    }

    state.updates.entries = normalizedUpdates;
    state.updates.total = total;
    state.updates.limit = limit;
    state.updates.offset = offset;
    state.updates.progress = progressSnapshot ?? state.updates.progress;
    state.updates.hasMore = hasMore;
    ensureUpdateCacheCapacity(limit);
    updateCacheFromEntries(state.updates.entries);
    pruneUpdateCache();
    recomputeTrend();
}

export function ingestRealtimeUpdates(entries: readonly SpsaUpdateEntry[]): void {
    if (!entries?.length) return;
    const coalesce = <T>(nextValue: T | null | undefined, prevValue: T | null | undefined): T | null | undefined => {
        if (nextValue !== null && nextValue !== undefined) {
            return nextValue;
        }
        return prevValue ?? null;
    };
    const normalized = normalizeSpsaUpdates(entries).map((entry) => {
        const existing = state.updates.cache.get(entry.updateIdx);
        if (!existing) {
            return entry;
        }
        const mergeWins = Math.max(existing.wins ?? 0, entry.wins ?? 0);
        const mergeLosses = Math.max(existing.losses ?? 0, entry.losses ?? 0);
        const mergeDraws = Math.max(existing.draws ?? 0, entry.draws ?? 0);
        const mergeGames = Math.max(existing.gamesCompleted ?? 0, entry.gamesCompleted ?? 0);
        const mergedPhaseWdl =
            entry.phaseWdl && Object.keys(entry.phaseWdl).length > 0 ? entry.phaseWdl : existing.phaseWdl;
        const mergedLtcRegression = entry.ltcRegression ?? existing.ltcRegression ?? null;
        const mergedHasLtcRegression = Boolean(entry.hasLtcRegression || existing.hasLtcRegression);
        return {
            ...existing,
            ...entry,
            timestamp: coalesce(entry.timestamp, existing.timestamp),
            startedAt: coalesce(entry.startedAt, existing.startedAt),
            endedAt: coalesce(entry.endedAt, existing.endedAt),
            deltaNorm: coalesce(entry.deltaNorm, existing.deltaNorm),
            deltaStep: coalesce(entry.deltaStep, existing.deltaStep),
            step: coalesce(entry.step, existing.step),
            sPlus: coalesce(entry.sPlus, existing.sPlus),
            sMinus: coalesce(entry.sMinus, existing.sMinus),
            phaseWdl: mergedPhaseWdl,
            wins: mergeWins,
            losses: mergeLosses,
            draws: mergeDraws,
            gamesCompleted: mergeGames,
            ltcRegression: mergedLtcRegression,
            hasLtcRegression: mergedHasLtcRegression,
        };
    });
    for (const entry of normalized) {
        state.updates.cache.set(entry.updateIdx, entry);
        state.updates.lastUpdateIdx = Math.max(state.updates.lastUpdateIdx, entry.updateIdx);
    }

    const merged = [...normalized, ...state.updates.entries];
    const seen = new Set<number>();
    const ordered: NormalizedSpsaUpdateEntry[] = [];
    for (const entry of merged) {
        const idx = entry.updateIdx;
        if (!Number.isFinite(idx) || seen.has(idx)) continue;
        seen.add(idx);
        ordered.push(entry);
    }
    ordered.sort((a, b) => b.updateIdx - a.updateIdx);
    state.updates.entries = ordered.slice(0, state.updates.limit);
    pruneUpdateCache();
    recomputeTrend();
}

export function updateProgressSnapshot(progress: SpsaUpdateProgress | null | undefined): void {
    if (!progress) {
        return;
    }
    const completed = Number(progress.completed ?? 0);
    const totalRaw = progress.total;
    const total = typeof totalRaw === 'number' && Number.isFinite(totalRaw) ? totalRaw : null;
    const percent = typeof progress.percent === 'number' && Number.isFinite(progress.percent) ? progress.percent : null;
    state.updates.progress = {
        completed,
        total,
        percent,
    };
}

export function getUpdateFromCache(updateIdx: number): NormalizedSpsaUpdateEntry | null {
    const value = state.updates.cache.get(updateIdx);
    return value ?? null;
}

export function getCachedUpdateDetail(updateIdx: number): NormalizedSpsaUpdateDetail | null {
    const key = Number.isFinite(updateIdx) && updateIdx === INITIAL_UPDATE_IDX ? INITIAL_UPDATE_IDX : updateIdx;
    if (key === INITIAL_UPDATE_IDX) {
        const existing = state.updates.detailCache.get(INITIAL_UPDATE_IDX);
        if (existing) return existing;
        return getInitialDetail();
    }
    const value = state.updates.detailCache.get(key);
    return value ?? null;
}

export function cacheUpdateDetail(detail: NormalizedSpsaUpdateDetail, source: SnapshotSource = 'rest'): void {
    const key = detail.isInitial ? INITIAL_UPDATE_IDX : detail.updateIdx;
    const existing = state.updates.detailCache.get(key) ?? null;
    if (existing) {
        assertUpdateDetailConsistency(detail, existing, source);
    }
    const mergedDetail = mergeDetailSnapshots(existing, detail);
    state.updates.detailCache.set(key, mergedDetail);
    const cachedEntry = state.updates.cache.get(key);
    const mergedPhaseWdl = mergePhaseWdl(cachedEntry?.phaseWdl, detail.phaseWdl);
    if (cachedEntry) {
        cachedEntry.phaseWdl = mergedPhaseWdl;
        if (Number.isFinite(detail.gamesCount)) {
            cachedEntry.gamesCompleted = detail.gamesCount;
        }
        if (!detail.isPending) {
            cachedEntry.isPending = false;
        }
        if (detail.perturbations) {
            cachedEntry.perturbations = detail.perturbations;
        }
        if (detail.ltcRegression !== undefined) {
            cachedEntry.ltcRegression = detail.ltcRegression;
            cachedEntry.hasLtcRegression = detail.hasLtcRegression ?? Boolean(detail.ltcRegression);
        }
    }
    state.updates.entries = state.updates.entries.map((entry) =>
        entry.updateIdx === key
            ? {
                  ...entry,
                  phaseWdl: mergedPhaseWdl,
                  gamesCompleted: Number.isFinite(detail.gamesCount) ? detail.gamesCount : entry.gamesCompleted,
                  isPending: detail.isPending ? entry.isPending : false,
                  ltcRegression: detail.ltcRegression ?? entry.ltcRegression,
                  hasLtcRegression: detail.hasLtcRegression ?? entry.hasLtcRegression,
              }
            : entry,
    );
    if (state.updates.initialEntry && key === INITIAL_UPDATE_IDX) {
        state.updates.initialEntry = {
            ...state.updates.initialEntry,
            phaseWdl: mergedPhaseWdl,
            gamesCompleted: Number.isFinite(detail.gamesCount)
                ? detail.gamesCount
                : state.updates.initialEntry.gamesCompleted,
            isPending: detail.isPending ? state.updates.initialEntry.isPending : false,
            ltcRegression: detail.ltcRegression ?? state.updates.initialEntry.ltcRegression,
            hasLtcRegression: detail.hasLtcRegression ?? state.updates.initialEntry.hasLtcRegression,
        };
    }
}

export function updateVariantGamesCache(
    updateIdx: number,
    games: NormalizedSpsaUpdateDetail['games'],
    metadata?: {
        phaseWdl?: SpsaPhaseWdlMap | null;
        gamesCount?: number | null;
        wdl?: { wins: number; losses: number; draws: number } | null;
        isPending?: boolean;
    },
): void {
    const key = updateIdx;
    const safeGamesCount = Number.isFinite(metadata?.gamesCount) ? (metadata?.gamesCount as number) : games.length;

    let existing = state.updates.detailCache.get(key);
    if (!existing) {
        const summary = state.updates.cache.get(key);
        if (summary) {
            existing = {
                raw: summary.raw ?? ({} as unknown),
                updateIdx: key,
                variantId: summary.variantId ?? null,
                params: summary.params ?? {},
                deltas: summary.deltas ?? {},
                gradients: summary.gradients ?? {},
                perturbations: summary.perturbations ?? { plus: {}, minus: {} },
                sPlus: summary.sPlus ?? null,
                sMinus: summary.sMinus ?? null,
                step: summary.step ?? null,
                gainCk: summary.gainCk ?? null,
                gainAk: summary.gainAk ?? null,
                isPending: summary.isPending ?? false,
                isInitial: summary.isInitial ?? false,
                engines: { baseline: null, tuned: null },
                wdl: {
                    wins: summary.wins ?? 0,
                    losses: summary.losses ?? 0,
                    draws: summary.draws ?? 0,
                },
                games: [],
                gamesCount: summary.gamesCompleted ?? 0,
                ltcGames: [],
                ltcGamesCount: 0,
                ltcRegression: summary.ltcRegression ?? null,
                hasLtcRegression: summary.hasLtcRegression ?? Boolean(summary.ltcRegression),
                phaseWdl: summary.phaseWdl ?? {
                    plus: { wins: 0, losses: 0, draws: 0 },
                    minus: { wins: 0, losses: 0, draws: 0 },
                },
                timestamp: summary.timestamp ?? null,
                startedAt: summary.startedAt ?? null,
                endedAt: summary.endedAt ?? null,
                deltaNorm: summary.deltaNorm ?? null,
                deltaStep: summary.deltaStep ?? null,
                payload: (summary.payload as JsonObject) ?? {},
                view: 'slim',
                detailWindow: 'short',
                loadedIncludes: [],
                availableIncludes: [],
                scoreHistory: [],
            } as NormalizedSpsaUpdateDetail;
            state.updates.detailCache.set(key, existing);
        }
    }

    if (existing) {
        const nextDetail: NormalizedSpsaUpdateDetail = {
            ...existing,
            games,
            gamesCount: safeGamesCount,
            phaseWdl: metadata?.phaseWdl ?? existing.phaseWdl,
            isPending: metadata?.isPending ?? existing.isPending,
            wdl: metadata?.wdl ?? existing.wdl,
            availableIncludes: ensureInclude(existing.availableIncludes, 'variant_games'),
            loadedIncludes: ensureInclude(existing.loadedIncludes, 'variant_games'),
        };
        state.updates.detailCache.set(key, nextDetail);
    }

    const cachedEntry = state.updates.cache.get(key);
    const mergedPhaseWdl = metadata?.phaseWdl ?? cachedEntry?.phaseWdl;
    if (cachedEntry) {
        state.updates.cache.set(key, {
            ...cachedEntry,
            phaseWdl: mergedPhaseWdl ?? cachedEntry.phaseWdl,
            gamesCompleted: safeGamesCount,
            isPending: metadata?.isPending ?? cachedEntry.isPending,
            wins: metadata?.wdl?.wins ?? cachedEntry.wins,
            losses: metadata?.wdl?.losses ?? cachedEntry.losses,
            draws: metadata?.wdl?.draws ?? cachedEntry.draws,
        });
    }

    state.updates.entries = state.updates.entries.map((entry) =>
        entry.updateIdx === key
            ? {
                  ...entry,
                  phaseWdl: mergedPhaseWdl ?? entry.phaseWdl,
                  gamesCompleted: safeGamesCount,
                  isPending: metadata?.isPending ?? entry.isPending,
                  wins: metadata?.wdl?.wins ?? entry.wins,
                  losses: metadata?.wdl?.losses ?? entry.losses,
                  draws: metadata?.wdl?.draws ?? entry.draws,
              }
            : entry,
    );

    if (state.updates.initialEntry && key === INITIAL_UPDATE_IDX) {
        state.updates.initialEntry = {
            ...state.updates.initialEntry,
            phaseWdl: mergedPhaseWdl ?? state.updates.initialEntry.phaseWdl,
            gamesCompleted: safeGamesCount,
            isPending: metadata?.isPending ?? state.updates.initialEntry.isPending,
            wins: metadata?.wdl?.wins ?? state.updates.initialEntry.wins,
            losses: metadata?.wdl?.losses ?? state.updates.initialEntry.losses,
            draws: metadata?.wdl?.draws ?? state.updates.initialEntry.draws,
        };
    }
}

export function updateLtcGamesCache(
    updateIdx: number,
    games: NormalizedSpsaUpdateDetail['ltcGames'],
    metadata?: {
        gamesCount?: number | null;
    },
): void {
    const key = updateIdx;
    const safeGamesCount = Number.isFinite(metadata?.gamesCount) ? (metadata?.gamesCount as number) : games.length;

    let existing = state.updates.detailCache.get(key);
    if (!existing) {
        const summary = state.updates.cache.get(key);
        if (summary) {
            existing = {
                raw: summary.raw ?? ({} as unknown),
                updateIdx: key,
                variantId: summary.variantId ?? null,
                params: summary.params ?? {},
                deltas: summary.deltas ?? {},
                gradients: summary.gradients ?? {},
                perturbations: summary.perturbations ?? { plus: {}, minus: {} },
                sPlus: summary.sPlus ?? null,
                sMinus: summary.sMinus ?? null,
                step: summary.step ?? null,
                gainCk: summary.gainCk ?? null,
                gainAk: summary.gainAk ?? null,
                isPending: summary.isPending ?? false,
                isInitial: summary.isInitial ?? false,
                engines: { baseline: null, tuned: null },
                wdl: {
                    wins: summary.wins ?? 0,
                    losses: summary.losses ?? 0,
                    draws: summary.draws ?? 0,
                },
                games: [],
                gamesCount: summary.gamesCompleted ?? 0,
                ltcGames: [],
                ltcGamesCount: 0,
                ltcRegression: summary.ltcRegression ?? null,
                hasLtcRegression: summary.hasLtcRegression ?? Boolean(summary.ltcRegression),
                phaseWdl: summary.phaseWdl ?? {
                    plus: { wins: 0, losses: 0, draws: 0 },
                    minus: { wins: 0, losses: 0, draws: 0 },
                },
                timestamp: summary.timestamp ?? null,
                startedAt: summary.startedAt ?? null,
                endedAt: summary.endedAt ?? null,
                deltaNorm: summary.deltaNorm ?? null,
                deltaStep: summary.deltaStep ?? null,
                payload: (summary.payload as JsonObject) ?? {},
                view: 'slim',
                detailWindow: 'short',
                loadedIncludes: [],
                availableIncludes: [],
                scoreHistory: [],
            } as NormalizedSpsaUpdateDetail;
            state.updates.detailCache.set(key, existing);
        }
    }

    if (existing) {
        const nextDetail: NormalizedSpsaUpdateDetail = {
            ...existing,
            ltcGames: games,
            ltcGamesCount: safeGamesCount,
            availableIncludes: ensureInclude(existing.availableIncludes, 'ltc_games'),
            loadedIncludes: ensureInclude(existing.loadedIncludes, 'ltc_games'),
        };
        state.updates.detailCache.set(key, nextDetail);
    }
}

export function updateLtcWdlCache(
    updateIdx: number,
    wdl: {
        tunedWins: number;
        baselineWins: number;
        draws: number;
        totalGames?: number;
        hasLtcRegression?: boolean;
    },
): void {
    const entry = state.updates.cache.get(updateIdx);
    if (!entry) {
        return;
    }
    const shouldApply =
        Boolean(wdl.hasLtcRegression) || Boolean(entry.hasLtcRegression) || Boolean(entry.ltcRegression);
    const totalGames =
        typeof wdl.totalGames === 'number' ? wdl.totalGames : wdl.tunedWins + wdl.baselineWins + wdl.draws;
    if (!shouldApply && totalGames === 0) {
        return;
    }
    const current = entry.ltcRegression ?? null;
    const next = {
        status: current?.status ?? 'running',
        winrate: current?.winrate ?? null,
        elo: current?.elo ?? null,
        tunedWins: wdl.tunedWins,
        baselineWins: wdl.baselineWins,
        draws: wdl.draws,
        totalGames,
        totalPairs: current?.totalPairs ?? null,
        pairsPlayed: current?.pairsPlayed ?? null,
        accepted: current?.accepted ?? null,
        baselineUpdateIdx: current?.baselineUpdateIdx ?? null,
        baselineVariantToken: current?.baselineVariantToken ?? null,
        tunedVariantToken: current?.tunedVariantToken ?? null,
        startedAt: current?.startedAt ?? null,
        completedAt: current?.completedAt ?? null,
        failReasons: current?.failReasons ?? [],
        sprt: current?.sprt ?? null,
        sprtDecision: current?.sprtDecision ?? null,
    };

    entry.ltcRegression = next;
    entry.hasLtcRegression = true;

    state.updates.entries = state.updates.entries.map((item) =>
        item.updateIdx === updateIdx
            ? {
                  ...item,
                  ltcRegression: next,
                  hasLtcRegression: true,
              }
            : item,
    );

    if (state.updates.initialEntry && updateIdx === INITIAL_UPDATE_IDX) {
        state.updates.initialEntry = {
            ...state.updates.initialEntry,
            ltcRegression: next,
            hasLtcRegression: true,
        };
    }

    const detail = state.updates.detailCache.get(updateIdx);
    if (detail) {
        detail.ltcRegression = next;
        detail.hasLtcRegression = true;
    }
}

export function updateLtcProgressCache(
    updateIdx: number,
    payload: {
        ltcRegression?: NormalizedSpsaUpdateEntry['ltcRegression'] | null;
        hasLtcRegression?: boolean | null;
        ltcRejected?: boolean | null;
        ltcRevertedTo?: number | null;
    },
): void {
    const existing = state.updates.cache.get(updateIdx);
    if (!existing) {
        return;
    }
    const mergedLtcRegression = payload.ltcRegression ?? existing.ltcRegression ?? null;
    const mergedHasLtcRegression =
        payload.hasLtcRegression ?? existing.hasLtcRegression ?? Boolean(mergedLtcRegression);
    const mergedLtcRejected = payload.ltcRejected ?? existing.ltcRejected ?? false;
    const mergedLtcRevertedTo =
        payload.ltcRevertedTo !== undefined && payload.ltcRevertedTo !== null
            ? payload.ltcRevertedTo
            : (existing.ltcRevertedTo ?? null);

    existing.ltcRegression = mergedLtcRegression;
    existing.hasLtcRegression = mergedHasLtcRegression;
    existing.ltcRejected = mergedLtcRejected;
    if (mergedLtcRevertedTo !== null) {
        existing.ltcRevertedTo = mergedLtcRevertedTo;
    }

    state.updates.entries = state.updates.entries.map((entry) =>
        entry.updateIdx === updateIdx
            ? {
                  ...entry,
                  ltcRegression: mergedLtcRegression,
                  hasLtcRegression: mergedHasLtcRegression,
                  ltcRejected: mergedLtcRejected,
                  ltcRevertedTo: mergedLtcRevertedTo,
              }
            : entry,
    );
    if (state.updates.initialEntry && updateIdx === INITIAL_UPDATE_IDX) {
        state.updates.initialEntry = {
            ...state.updates.initialEntry,
            ltcRegression: mergedLtcRegression,
            hasLtcRegression: mergedHasLtcRegression,
            ltcRejected: mergedLtcRejected,
            ltcRevertedTo: mergedLtcRevertedTo,
        };
    }
    const detail = state.updates.detailCache.get(updateIdx);
    if (detail) {
        detail.ltcRegression = mergedLtcRegression;
        detail.hasLtcRegression = mergedHasLtcRegression;
    }
}

export function setUpdateExpanded(updateIdx: number, expanded: boolean): void {
    const key = updateIdx === INITIAL_UPDATE_IDX ? INITIAL_UPDATE_IDX : updateIdx;
    if (expanded) {
        state.updates.expanded.clear();
        state.updates.expanded.add(key);
    } else {
        state.updates.expanded.delete(key);
    }
}

export function ensureUpdateCacheCapacity(limit: number): void {
    state.updates.limit = limit;
    if (state.updates.entries.length > limit) {
        state.updates.entries = state.updates.entries.slice(0, limit);
    }
}

export function recomputeTrend(): void {
    const candidates = Array.from(state.updates.cache.values()).filter((entry) => !entry.isInitial && !entry.isPending);
    if (!candidates.length) {
        resetTrend();
        return;
    }
    candidates.sort((a, b) => {
        const tsA = resolveTimestamp(a);
        const tsB = resolveTimestamp(b);
        if (tsA !== tsB) return tsA - tsB;
        return a.updateIdx - b.updateIdx;
    });
    const limit = Number.isFinite(MAX_TREND_POINTS) ? Math.trunc(MAX_TREND_POINTS) : null;
    const recent = limit !== null && limit > 0 ? candidates.slice(-limit) : candidates;
    const deltaNorm: number[] = [];
    const deltaStep: number[] = [];
    const gainAk: number[] = [];
    const timestamps: number[] = [];
    const variantIndices: number[] = [];
    for (const entry of recent) {
        const norm = resolveDeltaNorm(entry);
        const step = resolveDeltaStep(entry);
        deltaNorm.push(norm ?? 0);
        deltaStep.push(step ?? 0);
        gainAk.push(resolveGainAk(entry) ?? Number.NaN);
        timestamps.push(resolveTimestamp(entry));
        variantIndices.push(Number.isFinite(entry.updateIdx) ? (entry.updateIdx as number) : Number.NaN);
    }
    state.trend.deltaNorm = deltaNorm;
    state.trend.deltaStep = deltaStep;
    state.trend.gainAk = gainAk;
    state.trend.timestamps = timestamps;
    state.trend.variantIndices = variantIndices;
}

export function applyMobilitySeries(series?: { gain_ak?: number[]; variant_indices?: number[] } | null): void {
    if (!series || typeof series !== 'object') {
        return;
    }
    const gainSeries = Array.isArray(series.gain_ak) ? series.gain_ak : [];
    const indexSeries = Array.isArray(series.variant_indices) ? series.variant_indices : [];
    state.trend.gainAk = gainSeries.map((value) => (Number.isFinite(value) ? (value as number) : Number.NaN));
    state.trend.variantIndices = indexSeries.map((value) =>
        Number.isFinite(value) ? Math.trunc(value as number) : Number.NaN,
    );
}

export { getInitialUpdateDetail } from './initial-artifacts';

function resetTrend(): void {
    state.trend.deltaNorm = [];
    state.trend.deltaStep = [];
    state.trend.gainAk = [];
    state.trend.timestamps = [];
    state.trend.variantIndices = [];
}

function updateCacheFromEntries(entries: readonly NormalizedSpsaUpdateEntry[]): void {
    for (const entry of entries) {
        const idx = entry.updateIdx;
        if (!Number.isFinite(idx)) continue;
        state.updates.cache.set(idx, entry);
        state.updates.lastUpdateIdx = Math.max(state.updates.lastUpdateIdx, idx);
    }
}

function pruneUpdateCache(): void {
    const rawLimit = MAX_UPDATE_CACHE_ENTRIES;
    if (!Number.isFinite(rawLimit)) {
        return;
    }
    const limit = Math.trunc(rawLimit);
    if (limit <= 0) {
        return;
    }
    const cache = state.updates.cache;
    if (cache.size <= limit) {
        return;
    }
    const entries = Array.from(cache.values());
    entries.sort((a, b) => {
        const tsA = resolveTimestamp(a);
        const tsB = resolveTimestamp(b);
        if (tsA !== tsB) {
            return tsA - tsB;
        }
        return a.updateIdx - b.updateIdx;
    });
    const excess = cache.size - limit;
    for (let index = 0; index < excess && index < entries.length; index += 1) {
        const entry = entries[index];
        if (!Number.isFinite(entry.updateIdx)) {
            continue;
        }
        cache.delete(entry.updateIdx);
    }
}

function resolveTimestamp(entry: NormalizedSpsaUpdateEntry): number {
    if (Number.isFinite(entry.timestamp)) {
        return entry.timestamp as number;
    }
    return Date.now();
}

function resolveDeltaNorm(entry: NormalizedSpsaUpdateEntry): number | null {
    return Number.isFinite(entry.deltaNorm) ? (entry.deltaNorm as number) : null;
}

function resolveDeltaStep(entry: NormalizedSpsaUpdateEntry): number | null {
    return Number.isFinite(entry.deltaStep) ? (entry.deltaStep as number) : null;
}

function resolveGainAk(entry: NormalizedSpsaUpdateEntry): number | null {
    return Number.isFinite(entry.gainAk) ? (entry.gainAk as number) : null;
}

function mergePhaseWdl(
    existing: SpsaPhaseWdlMap | undefined,
    incoming: SpsaPhaseWdlMap | undefined,
): SpsaPhaseWdlMap | undefined {
    if (!incoming || Object.keys(incoming).length === 0) {
        return existing;
    }
    const result: Record<string, SpsaPhaseWdlEntry> = existing ? { ...existing } : {};
    for (const [key, value] of Object.entries(incoming)) {
        if (!value) continue;
        result[key] = {
            wins: Number.isFinite(value.wins) ? value.wins : 0,
            losses: Number.isFinite(value.losses) ? value.losses : 0,
            draws: Number.isFinite(value.draws) ? value.draws : 0,
        };
    }
    return result as SpsaPhaseWdlMap;
}

function mergeDetailSnapshots(
    existing: NormalizedSpsaUpdateDetail | null,
    incoming: NormalizedSpsaUpdateDetail,
): NormalizedSpsaUpdateDetail {
    if (!existing) {
        return incoming;
    }
    const availableSet = new Set<SpsaDetailInclude>(incoming.availableIncludes);
    for (const include of existing.availableIncludes) {
        availableSet.add(include);
    }
    const loadedSet = new Set<SpsaDetailInclude>(incoming.loadedIncludes);
    const merged: NormalizedSpsaUpdateDetail = {
        ...existing,
        ...incoming,
        availableIncludes: Array.from(availableSet),
        loadedIncludes: incoming.loadedIncludes,
    };

    preserveVariantGamesSection(existing, merged, loadedSet);
    preserveLtcGamesSection(existing, merged, loadedSet);

    merged.loadedIncludes = Array.from(loadedSet);
    return merged;
}

function preserveVariantGamesSection(
    existing: NormalizedSpsaUpdateDetail,
    merged: NormalizedSpsaUpdateDetail,
    loadedSet: Set<SpsaDetailInclude>,
): void {
    if (loadedSet.has('variant_games')) {
        return;
    }
    if (!existing.loadedIncludes.includes('variant_games')) {
        return;
    }
    const countsMatch = merged.gamesCount === existing.gamesCount;
    merged.games = existing.games;
    if (countsMatch) {
        loadedSet.add('variant_games');
    }
}

function preserveLtcGamesSection(
    existing: NormalizedSpsaUpdateDetail,
    merged: NormalizedSpsaUpdateDetail,
    loadedSet: Set<SpsaDetailInclude>,
): void {
    if (loadedSet.has('ltc_games')) {
        return;
    }
    if (!existing.loadedIncludes.includes('ltc_games')) {
        return;
    }
    const countsMatch = merged.ltcGamesCount === existing.ltcGamesCount;
    merged.ltcGames = existing.ltcGames;
    if (countsMatch) {
        loadedSet.add('ltc_games');
    }
}

function ensureInclude(existing: readonly SpsaDetailInclude[], include: SpsaDetailInclude): SpsaDetailInclude[] {
    if (existing.includes(include)) {
        return existing as SpsaDetailInclude[];
    }
    return [...existing, include];
}
