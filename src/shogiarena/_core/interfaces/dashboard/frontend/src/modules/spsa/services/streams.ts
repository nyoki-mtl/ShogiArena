import type {
    SpsaConvergenceResponse,
    SpsaCorrelationResponse,
    SpsaLtcResultEntry,
    SpsaLtcResultsResponse,
    SpsaLtcSummary,
    SpsaSummaryResponse,
    SpsaUpdateEntry,
    SpsaUpdateProgress,
    NormalizedSpsaUpdateEntry,
    SpsaRefreshOptions,
} from '@/modules/spsa/types';
import { SPSA_SUMMARY_PROGRESS_EVENT } from '../constants';
import {
    SUMMARY_CACHE_TTL_MS,
    applyConvergenceMetrics,
    applySummaryProgressPatch,
    applyUpdatesResponse,
    ensureUpdateCacheCapacity,
    ingestRealtimeUpdates,
    setEventSource,
    setSummaryCache,
    updateLtcGamesCache,
    updateProgressSnapshot,
    updateLtcProgressCache,
    updateLtcWdlCache,
    updateVariantGamesCache,
    applyMobilitySeries,
    resolveSummary,
} from '../state';
import type { getState } from '../state';
import type { DashboardCore } from '@/types/dashboard';
import type { JsonObject } from '@/types/shared';
import { normalizePhaseWdlMap, normalizeSpsaSummary, normalizeSpsaUpdates, normalizeVariantGames } from './normalizers';
import { isConsistencyError, mergeSpsaSummarySnapshot } from './consistency';
import type { SpsaApiCallbacks } from './types';

const SSE_RECONNECT_DELAY_MS = 3_000;
const LTC_RESULTS_STREAM_LIMIT = 200;

export type SpsaStreamCacheState = {
    latestConvergenceData: SpsaConvergenceResponse | null;
    convergenceCacheExpiry: number;
    latestCorrelationData: SpsaCorrelationResponse | null;
    correlationCache: { data: SpsaCorrelationResponse; expiresAt: number } | null;
    ltcSummaryCache: { data: SpsaLtcSummary; expiresAt: number } | null;
    ltcResultsCache: Map<number, { data: SpsaLtcResultsResponse; expiresAt: number }>;
    latestLtcResults: SpsaLtcResultsResponse | null;
    latestLtcResultsAt: number | null;
    latestLtcResultsSeq: number | null;
    ltcResultsGapDetected: boolean;
    ltcResultsStreamOpenedAt: number | null;
};

export type SpsaStreamDeps = {
    core: DashboardCore;
    state: ReturnType<typeof getState>;
    callbacks: SpsaApiCallbacks;
    getApiBase: () => string;
    isDashboardOffline: () => boolean;
    reportRecoverableFailure: (
        context: string,
        error: unknown,
        options?: { notifyOffline?: boolean; title?: string },
    ) => void;
    handleError: (message: string, options?: { source?: 'sse' | 'generic' }) => void;
    refreshSummaryOnly: () => Promise<void>;
    refreshAll: (options?: SpsaRefreshOptions) => Promise<void>;
    markSseDisconnected: () => void;
    clearSseDisconnectNotice: () => void;
    markAnalysisDataStale: () => void;
    refreshAnalysisNow: () => void;
};

export type SpsaStreams = {
    startRealtimeStreams: () => void;
    openUpdatesStream: (force?: boolean) => void;
    closeUpdatesStream: () => void;
    openVariantGamesStream: () => void;
    closeVariantGamesStream: () => void;
    openLtcGamesStream: () => void;
    closeLtcGamesStream: () => void;
    openSummaryStream: () => void;
    closeSummaryStream: () => void;
    openCorrelationStream: () => void;
    closeCorrelationStream: () => void;
    openConvergenceStream: () => void;
    closeConvergenceStream: () => void;
    openLtcResultsStream: () => void;
    closeLtcResultsStream: () => void;
    openLtcProgressStream: () => void;
    closeLtcProgressStream: () => void;
    isCorrelationStreamActive: () => boolean;
    isConvergenceStreamActive: () => boolean;
    isLtcResultsStreamActive: () => boolean;
    isSummaryStreamOpen: () => boolean;
    deriveStreamBackedLtcSummary: () => SpsaLtcSummary | null;
    deriveStreamBackedLtcResults: (limit: number) => SpsaLtcResultsResponse | null;
    resetCaches: () => void;
    invalidateCorrelationCache: () => void;
};

export function createSpsaStreams(cacheState: SpsaStreamCacheState, deps: SpsaStreamDeps): SpsaStreams {
    const {
        core,
        state,
        callbacks,
        getApiBase,
        isDashboardOffline,
        reportRecoverableFailure,
        handleError,
        refreshSummaryOnly,
        refreshAll,
        markSseDisconnected,
        clearSseDisconnectNotice,
        markAnalysisDataStale,
        refreshAnalysisNow,
    } = deps;

    ensureUpdateCacheCapacity(state.updates.limit);

    let summarySource: EventSource | null = null;
    let summaryReconnectTimer: number | null = null;
    let updatesReconnectTimer: number | null = null;
    let variantGamesSource: EventSource | null = null;
    let variantGamesReconnectTimer: number | null = null;
    let ltcGamesSource: EventSource | null = null;
    let convergenceSource: EventSource | null = null;
    let convergenceReconnectTimer: number | null = null;
    let correlationSource: EventSource | null = null;
    let correlationReconnectTimer: number | null = null;
    let ltcResultsSource: EventSource | null = null;
    let ltcResultsReconnectTimer: number | null = null;
    let ltcProgressSource: EventSource | null = null;

    // In practice、SPSA 更新の SSE は完了時刻や LTC 判定を送ってこない場合がある。
    // 放置タブが REST ハイドレーションを一度も挟まないと Start だけ増えて Finish が空のままになる。
    // 差分だけでは埋められないフィールドが検知されたら、強制的に REST リフレッシュを走らせて整合性を回復する。
    const BACKFILL_DEBOUNCE_MS = 500;
    const BACKFILL_COOLDOWN_MS = 15_000;
    let hydrationBackfillTimer: number | null = null;
    let lastHydrationBackfillAt = 0;

    const hasIncompleteUpdates = (): boolean => {
        for (const entry of state.updates.cache.values()) {
            if (entry.isInitial) continue;
            const missingFinish = entry.startedAt != null && entry.endedAt == null && entry.isPending === false;
            const missingLtc = entry.hasLtcRegression && !entry.ltcRegression;
            if (missingFinish || missingLtc) {
                return true;
            }
        }
        return false;
    };

    const scheduleHydrationBackfill = (reason: string): void => {
        const now = Date.now();
        if (now - lastHydrationBackfillAt < BACKFILL_COOLDOWN_MS) {
            return;
        }
        if (hydrationBackfillTimer !== null) {
            return;
        }
        if (!hasIncompleteUpdates()) {
            return;
        }
        hydrationBackfillTimer = window.setTimeout(() => {
            hydrationBackfillTimer = null;
            lastHydrationBackfillAt = Date.now();
            void refreshAll({ force: true }).catch((error) => {
                reportRecoverableFailure('SpsaStreams.hydrationBackfill', error, {
                    title: `Failed to backfill SPSA updates (${reason})`,
                });
            });
        }, BACKFILL_DEBOUNCE_MS);
    };

    const resetCaches = (): void => {
        cacheState.latestConvergenceData = null;
        cacheState.convergenceCacheExpiry = 0;
        cacheState.latestCorrelationData = null;
        cacheState.correlationCache = null;
        cacheState.ltcSummaryCache = null;
        cacheState.ltcResultsCache.clear();
        cacheState.latestLtcResults = null;
        cacheState.latestLtcResultsAt = null;
        cacheState.latestLtcResultsSeq = null;
        cacheState.ltcResultsGapDetected = false;
        cacheState.ltcResultsStreamOpenedAt = null;
    };

    const invalidateCorrelationCache = (): void => {
        cacheState.correlationCache = null;
    };

    const deriveStreamBackedLtcSummary = (): SpsaLtcSummary | null => {
        return cacheState.latestLtcResults?.summary ?? null;
    };

    const deriveStreamBackedLtcResults = (limit: number): SpsaLtcResultsResponse | null => {
        const snapshot = cacheState.latestLtcResults;
        if (!snapshot) {
            return null;
        }
        const normalizedLimit = Math.max(1, limit);
        const entries = Array.isArray(snapshot.results) ? snapshot.results : [];
        if (entries.length === 0) {
            if (snapshot.total > 0) {
                return null;
            }
            return { total: 0, results: [], summary: snapshot.summary };
        }
        if (normalizedLimit > entries.length && snapshot.total > entries.length) {
            return null;
        }
        const sliceSize = Math.min(normalizedLimit, entries.length);
        return {
            total: snapshot.total,
            results: entries.slice(0, sliceSize),
            summary: snapshot.summary,
        };
    };

    const hydrateLtcCachesFromStream = (now: number): void => {
        if (!cacheState.latestLtcResults) {
            return;
        }
        cacheState.ltcResultsCache.clear();
        const availableCount = Array.isArray(cacheState.latestLtcResults.results)
            ? cacheState.latestLtcResults.results.length
            : 0;
        const candidateLimits = new Set<number>([50, 100, LTC_RESULTS_STREAM_LIMIT, availableCount]);
        if (availableCount >= 1) {
            candidateLimits.add(Math.min(25, availableCount));
        }
        for (const candidate of candidateLimits) {
            if (!candidate || candidate <= 0) continue;
            const payload = deriveStreamBackedLtcResults(candidate);
            if (!payload) continue;
            cacheState.ltcResultsCache.set(candidate, { data: payload, expiresAt: now + 30_000 });
        }
    };

    const normalizeProgressSnapshot = (raw: unknown): SpsaUpdateProgress => {
        if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
            throw new Error('SPSA progress payload must be an object');
        }
        const payload = raw as JsonObject;
        const completedValue = Number(payload.completed);
        if (!Number.isFinite(completedValue)) {
            throw new Error('SPSA progress payload requires numeric "completed"');
        }
        // Coerce before the finiteness check so numeric strings are accepted symmetrically with
        // "completed" (previously Number.isFinite ran on the raw value and rejected e.g. "100").
        const totalValue = Number(payload.total);
        if (!Number.isFinite(totalValue)) {
            throw new Error('SPSA progress payload requires numeric "total"');
        }
        const percentRaw = payload.percent;
        const percent = typeof percentRaw === 'number' && Number.isFinite(percentRaw) ? percentRaw : null;
        return {
            completed: Math.max(0, Math.trunc(completedValue)),
            total: Math.max(0, Math.trunc(totalValue)),
            percent,
        };
    };

    const publishSummaryProgress = (snapshot: SpsaUpdateProgress): void => {
        const existing =
            core.state?.spsaSummary && typeof core.state.spsaSummary === 'object'
                ? (core.state.spsaSummary as Record<string, unknown>)
                : {};
        const nextRecord: Record<string, unknown> = { ...existing, completed: snapshot.completed };
        if (typeof snapshot.total === 'number' && Number.isFinite(snapshot.total)) {
            nextRecord.total = snapshot.total;
        }
        const next = nextRecord as JsonObject;
        if (core.state) {
            core.state.spsaSummary = next;
        }
        core.events?.emit?.(SPSA_SUMMARY_PROGRESS_EVENT, next);
    };

    const applyProgressPayload = (raw: unknown): SpsaUpdateProgress | null => {
        try {
            const snapshot = normalizeProgressSnapshot(raw);
            updateProgressSnapshot(snapshot);
            applySummaryProgressPatch(snapshot);
            publishSummaryProgress(snapshot);
            return snapshot;
        } catch (_error) {
            handleError('Invalid SPSA progress payload from SSE', { source: 'sse' });
            markSseError();
            closeUpdatesStream();
            scheduleStreamReconnect();
            return null;
        }
    };

    function markSseError(): void {
        markSseDisconnected();
    }

    const handleStreamMessage = (message: JsonObject) => {
        const progressSnapshot = Object.hasOwn(message, 'progress') ? applyProgressPayload(message.progress) : null;
        const type = typeof message.type === 'string' ? message.type : 'unknown';
        if (type === 'initial') {
            const updates = extractUpdates(message.updates);
            if (updates.length) {
                applyUpdatesResponse({
                    updates,
                    total: typeof message.total === 'number' ? message.total : updates.length,
                    limit: state.updates.limit,
                    offset: 0,
                    has_more: false,
                    progress: progressSnapshot ?? undefined,
                });
                markAnalysisDataStale();
                callbacks.onUpdatesChanged?.({ reason: 'entries' });
                scheduleHydrationBackfill('initial');
            }
        } else if (type === 'update') {
            const updates = extractUpdates(message.updates);
            if (updates.length) {
                ingestRealtimeUpdates(updates);
                markAnalysisDataStale();
                callbacks.onUpdatesChanged?.({ reason: 'entries' });
                if (!isSummaryStreamOpen()) {
                    void refreshSummaryOnly();
                }
                scheduleHydrationBackfill('update');
            }
        }
    };

    const normalizeUpdate = (item: JsonObject): SpsaUpdateEntry | null => {
        const idxRaw = item.update_idx;
        const idx = typeof idxRaw === 'number' ? idxRaw : Number(idxRaw);
        if (!Number.isFinite(idx)) {
            return null;
        }
        return { ...item, update_idx: idx } as SpsaUpdateEntry;
    };

    const extractUpdates = (raw: unknown): SpsaUpdateEntry[] => {
        if (!Array.isArray(raw)) return [];
        return raw
            .filter((item): item is JsonObject => item != null && typeof item === 'object' && !Array.isArray(item))
            .map((item) => normalizeUpdate(item))
            .filter((entry): entry is SpsaUpdateEntry => entry !== null);
    };

    const buildUpdatesStreamUrl = (): string => {
        const apiBase = getApiBase();
        const lastIdx = state.updates.lastUpdateIdx ?? -1;
        const params = new URLSearchParams({
            last_update_idx: String(lastIdx),
            send_initial: 'true',
        });
        return `${apiBase}/api/spsa/updates/stream?${params.toString()}`;
    };

    const buildVariantGamesStreamUrl = (): string => {
        const apiBase = getApiBase();
        const params = new URLSearchParams({
            limit: String(state.updates.limit),
            send_initial: 'true',
        });
        return `${apiBase}/api/spsa/variant/games/stream?${params.toString()}`;
    };

    const buildLtcGamesStreamUrl = (): string => {
        const apiBase = getApiBase();
        const params = new URLSearchParams({
            limit: String(state.updates.limit),
            send_initial: 'true',
        });
        return `${apiBase}/api/spsa/ltc/games/stream?${params.toString()}`;
    };

    const buildSummaryStreamUrl = (): string => {
        const apiBase = getApiBase();
        const params = new URLSearchParams({ send_initial: 'true' });
        return `${apiBase}/api/spsa/summary/stream?${params.toString()}`;
    };

    const buildCorrelationStreamUrl = (): string => {
        const apiBase = getApiBase();
        const params = new URLSearchParams({ send_initial: 'true' });
        return `${apiBase}/api/spsa/analysis/correlation/stream?${params.toString()}`;
    };

    const buildLtcResultsStreamUrl = (): string => {
        const apiBase = getApiBase();
        const params = new URLSearchParams({
            limit: String(LTC_RESULTS_STREAM_LIMIT),
            send_initial: 'true',
        });
        return `${apiBase}/api/spsa/ltc/results/stream?${params.toString()}`;
    };

    const buildLtcProgressStreamUrl = (): string => {
        const apiBase = getApiBase();
        const params = new URLSearchParams({ send_initial: 'true' });
        return `${apiBase}/api/spsa/ltc/progress/stream?${params.toString()}`;
    };

    function clearUpdatesReconnectTimer(): void {
        if (updatesReconnectTimer !== null) {
            window.clearTimeout(updatesReconnectTimer);
            updatesReconnectTimer = null;
        }
    }

    function scheduleStreamReconnect(): void {
        if (isDashboardOffline()) return;
        clearUpdatesReconnectTimer();
        updatesReconnectTimer = window.setTimeout(() => {
            updatesReconnectTimer = null;
            openUpdatesStream();
        }, SSE_RECONNECT_DELAY_MS);
    }

    function closeUpdatesStream(): void {
        const source = state.connection.eventSource;
        if (source) {
            try {
                source.close();
            } catch (error) {
                console.warn('[SPSA] Failed to close updates SSE cleanly', error);
            }
        }
        setEventSource(null, 'closed');
        clearUpdatesReconnectTimer();
    }

    const openUpdatesStream = (force = false) => {
        if (typeof EventSource !== 'function') {
            handleError('SPSA updates stream requires EventSource support. Please use a modern browser.');
            return;
        }

        const existing = state.connection.eventSource;
        if (existing && state.connection.eventSourceStatus === 'open' && !force) {
            return;
        }
        if (existing) {
            try {
                existing.close();
            } catch (error) {
                console.warn('[SPSA] Failed to close existing updates SSE before reconnecting', error);
            }
            setEventSource(null, 'closed');
        }
        if (isDashboardOffline()) {
            return;
        }

        const url = buildUpdatesStreamUrl();
        try {
            const source = new EventSource(url);
            setEventSource(source, 'connecting');

            source.addEventListener('open', () => {
                setEventSource(source, 'open');
                clearUpdatesReconnectTimer();
                clearSseDisconnectNotice();
            });

            const handleEvent = (event: MessageEvent<string>) => {
                try {
                    const payload = JSON.parse(event.data) as JsonObject;
                    handleStreamMessage(payload);
                } catch (error) {
                    reportRecoverableFailure('SpsaStreams.updates.onmessage', error);
                }
            };

            source.addEventListener('spsa_updates', handleEvent);
            source.onmessage = handleEvent;

            source.onerror = (error) => {
                console.warn('[SPSA] Updates SSE error encountered', error);
                try {
                    source.close();
                } catch (closeError) {
                    console.warn('[SPSA] Failed to close errored updates SSE', closeError);
                }
                setEventSource(null, 'closed');
                if (isDashboardOffline()) {
                    return;
                }
                scheduleStreamReconnect();
                const cause = error instanceof ErrorEvent && error.error ? error.error : error;
                reportRecoverableFailure('SpsaStreams.updates.onerror', cause, { notifyOffline: true });
                markSseError();
            };
        } catch (error) {
            setEventSource(null, 'closed');
            scheduleStreamReconnect();
            reportRecoverableFailure('SpsaStreams.openUpdatesStream', error, { notifyOffline: true });
            markSseError();
        }
    };

    const isSummaryStreamOpen = (): boolean => {
        return Boolean(summarySource && summarySource.readyState === EventSource.OPEN);
    };

    const closeSummaryStream = () => {
        if (summarySource) {
            try {
                summarySource.close();
            } catch (error) {
                console.warn('[SPSA] Failed to close summary stream', error);
            }
        }
        summarySource = null;
        if (summaryReconnectTimer !== null) {
            window.clearTimeout(summaryReconnectTimer);
            summaryReconnectTimer = null;
        }
    };

    const scheduleSummaryReconnect = () => {
        if (summaryReconnectTimer !== null) {
            return;
        }
        summaryReconnectTimer = window.setTimeout(() => {
            summaryReconnectTimer = null;
            openSummaryStream();
        }, SSE_RECONNECT_DELAY_MS);
    };

    const applySummaryStreamPayload = (payload: JsonObject) => {
        const summaryRaw = payload.data;
        if (!summaryRaw || typeof summaryRaw !== 'object') {
            return;
        }
        const normalized = normalizeSpsaSummary(summaryRaw as SpsaSummaryResponse);
        const merged = mergeSpsaSummarySnapshot(normalized, 'sse');
        resolveSummary(merged);
        setSummaryCache(merged, SUMMARY_CACHE_TTL_MS);
        callbacks.onSummaryChanged?.();
    };

    const openSummaryStream = () => {
        if (typeof EventSource !== 'function') {
            handleError(
                'SPSA summary stream requires EventSource support. This browser is not supported for SPSA view.',
                { source: 'sse' },
            );
            return;
        }
        if (summarySource || isDashboardOffline()) {
            return;
        }

        const url = buildSummaryStreamUrl();

        try {
            const source = new EventSource(url);
            summarySource = source;

            const handleMessage = (event: MessageEvent<string>) => {
                try {
                    const payload = JSON.parse(event.data) as JsonObject;
                    const type = typeof payload.type === 'string' ? payload.type : 'summary';
                    if (type === 'summary') {
                        applySummaryStreamPayload(payload);
                    }
                } catch (error) {
                    if (!isConsistencyError(error)) {
                        reportRecoverableFailure('SpsaStreams.summary.parse', error);
                        return;
                    }
                    throw error;
                }
            };

            source.addEventListener('spsa_summary', handleMessage);
            source.onmessage = handleMessage;

            source.onerror = (error) => {
                console.warn('[SPSA] Summary SSE error', error);
                closeSummaryStream();
                if (isDashboardOffline()) {
                    return;
                }
                scheduleSummaryReconnect();
            };
        } catch (error) {
            console.error('[SPSA] Failed to open summary SSE', error);
            closeSummaryStream();
            scheduleSummaryReconnect();
        }
    };

    const closeVariantGamesStream = () => {
        if (variantGamesSource) {
            try {
                variantGamesSource.close();
            } catch (error) {
                console.warn('[SPSA] Failed to close variant games stream', error);
            }
        }
        variantGamesSource = null;
        if (variantGamesReconnectTimer !== null) {
            window.clearTimeout(variantGamesReconnectTimer);
            variantGamesReconnectTimer = null;
        }
    };

    const closeLtcGamesStream = () => {
        if (ltcGamesSource) {
            try {
                ltcGamesSource.close();
            } catch (error) {
                reportRecoverableFailure('SpsaStreams.ltcGames.close', error);
            }
        }
        ltcGamesSource = null;
    };

    const scheduleVariantGamesReconnect = () => {
        if (variantGamesReconnectTimer !== null) {
            return;
        }
        variantGamesReconnectTimer = window.setTimeout(() => {
            variantGamesReconnectTimer = null;
            openVariantGamesStream();
        }, SSE_RECONNECT_DELAY_MS);
    };

    const openVariantGamesStream = () => {
        if (typeof EventSource !== 'function') {
            handleError(
                'SPSA variant games stream requires EventSource support. This browser is not supported for SPSA view.',
                { source: 'sse' },
            );
            return;
        }
        if (variantGamesSource) {
            return;
        }

        const url = buildVariantGamesStreamUrl();
        try {
            const source = new EventSource(url);
            variantGamesSource = source;

            const handleMessage = (event: MessageEvent<string>) => {
                try {
                    const payload = JSON.parse(event.data) as JsonObject;
                    const type = typeof payload.type === 'string' ? payload.type : 'variant_games';
                    if (type === 'variant_games' || type === 'variant_games_batch') {
                        const updatesPayload =
                            type === 'variant_games_batch' && Array.isArray(payload.updates)
                                ? payload.updates
                                : [payload];
                        const affectedIndices: number[] = [];
                        for (const entry of updatesPayload) {
                            const idx = Number(entry.update_idx);
                            if (!Number.isFinite(idx)) continue;
                            const games = normalizeVariantGames(entry.games);
                            const phaseWdl = normalizePhaseWdlMap(entry.phase_wdl);
                            const wdlRaw = entry.wdl;
                            const wdl =
                                wdlRaw && typeof wdlRaw === 'object'
                                    ? {
                                          wins: Number((wdlRaw as JsonObject).wins) || 0,
                                          losses: Number((wdlRaw as JsonObject).losses) || 0,
                                          draws: Number((wdlRaw as JsonObject).draws) || 0,
                                      }
                                    : null;
                            const gamesCountValue = Number(entry.games_count);
                            const isPending = typeof entry.is_pending === 'boolean' ? entry.is_pending : undefined;
                            updateVariantGamesCache(idx as number, games, {
                                phaseWdl: phaseWdl ?? undefined,
                                gamesCount: Number.isFinite(gamesCountValue) ? gamesCountValue : games.length,
                                wdl,
                                isPending,
                            });
                            affectedIndices.push(idx as number);
                        }
                        if (affectedIndices.length) {
                            callbacks.onUpdatesChanged?.({
                                reason: 'variant-games',
                                affectedIndices,
                            });
                        }
                    } else if (type === 'variant_games_error') {
                        console.warn('[SPSA] Variant games stream error', payload.message);
                    }
                } catch (error) {
                    reportRecoverableFailure('SpsaStreams.variantGames.onmessage', error);
                }
            };

            source.addEventListener('spsa_variant_games', handleMessage);
            source.onmessage = handleMessage;

            source.onerror = (error) => {
                console.warn('[SPSA] Variant games SSE error', error);
                closeVariantGamesStream();
                scheduleVariantGamesReconnect();
            };
        } catch (error) {
            console.error('[SPSA] Failed to open variant games SSE', error);
            closeVariantGamesStream();
            scheduleVariantGamesReconnect();
        }
    };

    const openLtcGamesStream = () => {
        if (typeof EventSource !== 'function') {
            handleError(
                'SPSA LTC games stream requires EventSource support. This browser is not supported for SPSA view.',
                { source: 'sse' },
            );
            return;
        }
        if (ltcGamesSource) {
            return;
        }

        const url = buildLtcGamesStreamUrl();
        try {
            const source = new EventSource(url);
            ltcGamesSource = source;

            const handleMessage = (event: MessageEvent<string>) => {
                try {
                    const payload = JSON.parse(event.data) as JsonObject;
                    const type = typeof payload.type === 'string' ? payload.type : 'ltc_games_batch';
                    if (type === 'ltc_games_batch') {
                        const updates = Array.isArray(payload.updates) ? payload.updates : [];
                        const affectedIndices: number[] = [];
                        for (const entry of updates) {
                            const idx = Number(entry.update_idx);
                            if (!Number.isFinite(idx)) continue;
                            const games = normalizeVariantGames(entry.ltc_games);
                            const gamesCountValue = Number(entry.ltc_games_count);
                            updateLtcGamesCache(idx as number, games, {
                                gamesCount: Number.isFinite(gamesCountValue) ? gamesCountValue : games.length,
                            });
                            const wdlRaw = entry.ltc_wdl as JsonObject | undefined;
                            const hasLtcRegression =
                                typeof entry.has_ltc_regression === 'boolean' ? entry.has_ltc_regression : undefined;
                            if (wdlRaw) {
                                updateLtcWdlCache(idx as number, {
                                    tunedWins: Number(wdlRaw.tuned_wins) || 0,
                                    baselineWins: Number(wdlRaw.baseline_wins) || 0,
                                    draws: Number(wdlRaw.draws) || 0,
                                    totalGames: Number(wdlRaw.total_games) || undefined,
                                    hasLtcRegression,
                                });
                            }
                            affectedIndices.push(idx as number);
                        }
                        if (affectedIndices.length) {
                            callbacks.onUpdatesChanged?.({
                                reason: 'ltc-games',
                                affectedIndices,
                            });
                        }
                    } else if (type === 'ltc_games_error') {
                        reportRecoverableFailure('SpsaStreams.ltcGames.error', payload.message);
                    }
                } catch (error) {
                    reportRecoverableFailure('SpsaStreams.ltcGames.onmessage', error);
                }
            };

            source.addEventListener('spsa_ltc_games', handleMessage);
            source.onmessage = handleMessage;

            source.onerror = (error) => {
                reportRecoverableFailure('SpsaStreams.ltcGames.onerror', error);
                closeLtcGamesStream();
            };
        } catch (error) {
            console.error('[SPSA] Failed to open LTC games SSE', error);
            closeLtcGamesStream();
        }
    };

    const closeCorrelationStream = () => {
        if (correlationSource) {
            try {
                correlationSource.close();
            } catch (error) {
                console.warn('[SPSA] Failed to close correlation stream', error);
            }
        }
        correlationSource = null;
        if (correlationReconnectTimer !== null) {
            window.clearTimeout(correlationReconnectTimer);
            correlationReconnectTimer = null;
        }
    };

    const closeConvergenceStream = () => {
        if (convergenceSource) {
            try {
                convergenceSource.close();
            } catch (error) {
                console.warn('[SPSA] Failed to close convergence stream', error);
            }
        }
        convergenceSource = null;
        if (convergenceReconnectTimer !== null) {
            window.clearTimeout(convergenceReconnectTimer);
            convergenceReconnectTimer = null;
        }
    };

    const closeLtcResultsStream = () => {
        if (ltcResultsSource) {
            try {
                ltcResultsSource.close();
            } catch (error) {
                console.warn('[SPSA] Failed to close LTC results stream', error);
            }
        }
        ltcResultsSource = null;
        cacheState.ltcResultsStreamOpenedAt = null;
        if (ltcResultsReconnectTimer !== null) {
            window.clearTimeout(ltcResultsReconnectTimer);
            ltcResultsReconnectTimer = null;
        }
    };

    const closeLtcProgressStream = () => {
        if (ltcProgressSource) {
            try {
                ltcProgressSource.close();
            } catch (error) {
                reportRecoverableFailure('SpsaStreams.ltcProgress.close', error);
            }
        }
        ltcProgressSource = null;
    };

    const scheduleConvergenceReconnect = () => {
        if (convergenceReconnectTimer !== null) {
            return;
        }
        convergenceReconnectTimer = window.setTimeout(() => {
            convergenceReconnectTimer = null;
            openConvergenceStream();
        }, SSE_RECONNECT_DELAY_MS);
    };

    const scheduleCorrelationReconnect = () => {
        if (correlationReconnectTimer !== null) {
            return;
        }
        correlationReconnectTimer = window.setTimeout(() => {
            correlationReconnectTimer = null;
            openCorrelationStream();
        }, SSE_RECONNECT_DELAY_MS);
    };

    const scheduleLtcResultsReconnect = () => {
        if (ltcResultsReconnectTimer !== null) {
            return;
        }
        ltcResultsReconnectTimer = window.setTimeout(() => {
            ltcResultsReconnectTimer = null;
            openLtcResultsStream();
        }, SSE_RECONNECT_DELAY_MS);
    };

    const openConvergenceStream = () => {
        if (typeof EventSource !== 'function') {
            handleError(
                'SPSA convergence stream requires EventSource support. This browser is not supported for SPSA view.',
                { source: 'sse' },
            );
            return;
        }
        if (convergenceSource || isDashboardOffline()) {
            return;
        }

        const apiBase = getApiBase();
        const params = new URLSearchParams({ send_initial: 'true' });
        const url = `${apiBase}/api/spsa/analysis/convergence?${params.toString()}`;

        try {
            const source = new EventSource(url);
            convergenceSource = source;

            const handleMessage = (event: MessageEvent<string>) => {
                try {
                    const payload = JSON.parse(event.data) as JsonObject;
                    const type = typeof payload.type === 'string' ? payload.type : 'convergence_update';

                    if (type === 'convergence_update') {
                        const data = payload.data as unknown;
                        if (data && typeof data === 'object') {
                            const snapshot = data as SpsaConvergenceResponse;
                            if (snapshot.status === 'ready') {
                                cacheState.latestConvergenceData = snapshot;
                                cacheState.convergenceCacheExpiry = Date.now() + 30_000;
                                applyConvergenceMetrics(snapshot);
                                if (snapshot.ltc_results) {
                                    applyLtcResultsSnapshot(snapshot.ltc_results, { notify: false });
                                }
                                if (snapshot.mobility_series) {
                                    applyMobilitySeries(snapshot.mobility_series);
                                }
                            }
                            callbacks.onConvergenceChanged?.(snapshot);
                        }
                    }
                } catch (error) {
                    reportRecoverableFailure('SpsaStreams.convergence.onmessage', error);
                }
            };

            source.addEventListener('spsa_convergence', handleMessage);
            source.onmessage = handleMessage;

            source.onerror = (error) => {
                console.warn('[SPSA] Convergence SSE error', error);
                closeConvergenceStream();
                if (isDashboardOffline()) {
                    return;
                }
                scheduleConvergenceReconnect();
            };
        } catch (error) {
            console.error('[SPSA] Failed to open convergence SSE', error);
            closeConvergenceStream();
            scheduleConvergenceReconnect();
        }
    };

    const openCorrelationStream = () => {
        if (typeof EventSource !== 'function') {
            handleError(
                'SPSA correlation stream requires EventSource support. This browser is not supported for SPSA view.',
                { source: 'sse' },
            );
            return;
        }
        if (correlationSource || isDashboardOffline()) {
            return;
        }

        const url = buildCorrelationStreamUrl();

        try {
            const source = new EventSource(url);
            correlationSource = source;

            const handleMessage = (event: MessageEvent<string>) => {
                try {
                    const payload = JSON.parse(event.data) as JsonObject;
                    const type = typeof payload.type === 'string' ? payload.type : 'correlation_update';
                    if (type === 'correlation_update') {
                        const data = payload.data as unknown;
                        if (data && typeof data === 'object') {
                            const snapshot = data as SpsaCorrelationResponse;
                            if (snapshot.status === 'ready') {
                                cacheState.latestCorrelationData = snapshot;
                                cacheState.correlationCache = {
                                    data: snapshot,
                                    expiresAt: Date.now() + 30_000,
                                };
                            }
                            callbacks.onCorrelationChanged?.(snapshot);
                        }
                    }
                } catch (error) {
                    reportRecoverableFailure('SpsaStreams.correlation.onmessage', error);
                }
            };

            source.addEventListener('spsa_correlation', handleMessage);
            source.onmessage = handleMessage;

            source.onerror = (error) => {
                console.warn('[SPSA] Correlation SSE error', error);
                closeCorrelationStream();
                if (isDashboardOffline()) {
                    return;
                }
                scheduleCorrelationReconnect();
            };
        } catch (error) {
            console.error('[SPSA] Failed to open correlation SSE', error);
            closeCorrelationStream();
            scheduleCorrelationReconnect();
        }
    };

    const applyLtcResultsSnapshot = (
        snapshot: SpsaLtcResultsResponse,
        options: { seq?: number | null; notify?: boolean } = {},
    ): void => {
        const totalValue = Number(snapshot.total ?? cacheState.latestLtcResults?.total ?? 0);
        const normalizedTotal = Number.isFinite(totalValue) ? Math.max(0, Math.trunc(totalValue)) : 0;
        const results = Array.isArray(snapshot.results) ? snapshot.results : [];
        const resolvedSummary = snapshot.summary;
        cacheState.latestLtcResults = {
            total: normalizedTotal || results.length,
            results,
            summary: resolvedSummary,
        };
        const now = Date.now();
        cacheState.latestLtcResultsAt = now;
        if (normalizedTotal > results.length) {
            cacheState.ltcResultsGapDetected = true;
        } else if (normalizedTotal > 0) {
            cacheState.ltcResultsGapDetected = false;
        }
        if (options.seq !== null && options.seq !== undefined) {
            const prevSeq = cacheState.latestLtcResultsSeq;
            if (prevSeq !== null && options.seq > prevSeq + 1) {
                cacheState.ltcResultsGapDetected = true;
            }
            cacheState.latestLtcResultsSeq = options.seq;
        }
        cacheState.ltcSummaryCache = {
            data: resolvedSummary,
            expiresAt: now + 30_000,
        };
        hydrateLtcCachesFromStream(now);

        if (options.notify ?? true) {
            callbacks.onLtcResultsChanged?.(cacheState.latestLtcResults ?? undefined);
            refreshAnalysisNow();
        }
    };

    const applyLtcResultsStreamPayload = (payload: JsonObject) => {
        const raw = payload.data;
        if (!raw || typeof raw !== 'object') {
            return;
        }
        const record = raw as JsonObject;
        const seqValue =
            typeof payload.seq === 'number' && Number.isFinite(payload.seq) ? Math.trunc(payload.seq) : null;
        const totalValue = Number(record.total ?? cacheState.latestLtcResults?.total ?? 0);
        const incomingResults = Array.isArray(record.results) ? (record.results as SpsaLtcResultEntry[]) : null;
        const previousResults = cacheState.latestLtcResults?.results ?? [];
        const results = incomingResults ?? previousResults;
        const incomingSummary = (record.summary ?? null) as SpsaLtcSummary | null;
        const resolvedSummary = incomingSummary ?? cacheState.latestLtcResults?.summary ?? null;
        if (!resolvedSummary) {
            console.warn('[SPSA:LTC] stream payload missing summary', {
                total: totalValue,
                resultsCount: results.length,
                hasIncomingSummary: Boolean(incomingSummary),
            });
            return;
        }
        const snapshot: SpsaLtcResultsResponse = {
            total: Number.isFinite(totalValue) ? Math.max(0, Math.trunc(totalValue)) : results.length,
            results,
            summary: resolvedSummary,
        };
        applyLtcResultsSnapshot(snapshot, { seq: seqValue, notify: true });
    };

    const normalizeLtcProgressUpdates = (
        raw: unknown,
    ): Array<{
        updateIdx: number;
        ltcRegression: NormalizedSpsaUpdateEntry['ltcRegression'] | null;
        hasLtcRegression: boolean;
        ltcRejected: boolean | null;
        ltcRevertedTo: number | null;
    }> => {
        if (!Array.isArray(raw)) {
            return [];
        }
        const entries: SpsaUpdateEntry[] = [];
        const extras = new Map<number, { ltcRejected: boolean | null; ltcRevertedTo: number | null }>();
        for (const item of raw) {
            if (!item || typeof item !== 'object' || Array.isArray(item)) {
                continue;
            }
            const record = item as JsonObject;
            const idxRaw = record.update_idx;
            const idx = typeof idxRaw === 'number' ? idxRaw : Number(idxRaw);
            if (!Number.isFinite(idx)) {
                continue;
            }
            const ltcRejected = typeof record.is_ltc_rejected === 'boolean' ? record.is_ltc_rejected : null;
            const ltcRevertedRaw = record.ltc_reverted_to;
            const ltcRevertedTo = Number.isFinite(Number(ltcRevertedRaw)) ? Math.trunc(Number(ltcRevertedRaw)) : null;
            entries.push({
                update_idx: idx,
                ltc_regression: record.ltc_regression as SpsaUpdateEntry['ltc_regression'],
                has_ltc_regression: record.has_ltc_regression as SpsaUpdateEntry['has_ltc_regression'],
            });
            extras.set(idx, { ltcRejected, ltcRevertedTo });
        }
        if (entries.length === 0) {
            return [];
        }
        const normalized = normalizeSpsaUpdates(entries);
        return normalized.map((entry) => {
            const meta = extras.get(entry.updateIdx);
            return {
                updateIdx: entry.updateIdx,
                ltcRegression: entry.ltcRegression ?? null,
                hasLtcRegression: entry.hasLtcRegression ?? Boolean(entry.ltcRegression),
                ltcRejected: meta?.ltcRejected ?? null,
                ltcRevertedTo: meta?.ltcRevertedTo ?? null,
            };
        });
    };

    const openLtcResultsStream = () => {
        if (typeof EventSource !== 'function') {
            handleError(
                'SPSA LTC results stream requires EventSource support. This browser is not supported for SPSA view.',
                { source: 'sse' },
            );
            return;
        }
        if (ltcResultsSource) {
            return;
        }
        if (isDashboardOffline()) {
            return;
        }

        const url = buildLtcResultsStreamUrl();

        try {
            const source = new EventSource(url);
            ltcResultsSource = source;
            cacheState.ltcResultsStreamOpenedAt = Date.now();

            const handleMessage = (event: MessageEvent<string>) => {
                try {
                    const payload = JSON.parse(event.data) as JsonObject;
                    const type = typeof payload.type === 'string' ? payload.type : 'ltc_results';
                    if (type === 'ltc_results') {
                        applyLtcResultsStreamPayload(payload);
                    }
                } catch (error) {
                    reportRecoverableFailure('SpsaStreams.ltcResults.onmessage', error);
                }
            };

            source.addEventListener('spsa_ltc_results', handleMessage);
            source.onmessage = handleMessage;

            source.onerror = (error) => {
                console.warn('[SPSA] LTC results SSE error', error);
                closeLtcResultsStream();
                if (isDashboardOffline()) {
                    return;
                }
                scheduleLtcResultsReconnect();
            };
        } catch (error) {
            console.error('[SPSA] Failed to open LTC results SSE', error);
            closeLtcResultsStream();
            scheduleLtcResultsReconnect();
        }
    };

    const openLtcProgressStream = () => {
        if (typeof EventSource !== 'function') {
            handleError(
                'SPSA LTC progress stream requires EventSource support. This browser is not supported for SPSA view.',
                { source: 'sse' },
            );
            return;
        }
        if (ltcProgressSource || isDashboardOffline()) {
            return;
        }

        const url = buildLtcProgressStreamUrl();

        try {
            const source = new EventSource(url);
            ltcProgressSource = source;

            const handleMessage = (event: MessageEvent<string>) => {
                try {
                    const payload = JSON.parse(event.data) as JsonObject;
                    const type = typeof payload.type === 'string' ? payload.type : 'ltc_progress';
                    if (type === 'ltc_progress') {
                        const updates = normalizeLtcProgressUpdates(payload.updates);
                        if (!updates.length) {
                            return;
                        }
                        updates.forEach((entry) => {
                            updateLtcProgressCache(entry.updateIdx, {
                                ltcRegression: entry.ltcRegression ?? null,
                                hasLtcRegression: entry.hasLtcRegression ?? Boolean(entry.ltcRegression),
                                ltcRejected: entry.ltcRejected ?? undefined,
                                ltcRevertedTo: entry.ltcRevertedTo ?? undefined,
                            });
                        });
                        callbacks.onUpdatesChanged?.({ reason: 'entries' });
                    }
                } catch (error) {
                    reportRecoverableFailure('SpsaStreams.ltcProgress.onmessage', error);
                }
            };

            source.addEventListener('spsa_ltc_progress', handleMessage);
            source.onmessage = handleMessage;

            source.onerror = (error) => {
                reportRecoverableFailure('SpsaStreams.ltcProgress.onerror', error);
                closeLtcProgressStream();
                if (isDashboardOffline()) {
                    return;
                }
            };
        } catch (error) {
            console.error('[SPSA] Failed to open LTC progress SSE', error);
            closeLtcProgressStream();
        }
    };

    const startRealtimeStreams = (): void => {
        openSummaryStream();
        openUpdatesStream();
    };

    return {
        startRealtimeStreams,
        openUpdatesStream,
        closeUpdatesStream,
        openVariantGamesStream,
        closeVariantGamesStream,
        openLtcGamesStream,
        closeLtcGamesStream,
        openSummaryStream,
        closeSummaryStream,
        openCorrelationStream,
        closeCorrelationStream,
        openConvergenceStream,
        closeConvergenceStream,
        openLtcResultsStream,
        closeLtcResultsStream,
        openLtcProgressStream,
        closeLtcProgressStream,
        isCorrelationStreamActive: () =>
            Boolean(correlationSource && correlationSource.readyState === EventSource.OPEN),
        isConvergenceStreamActive: () =>
            Boolean(convergenceSource && convergenceSource.readyState === EventSource.OPEN),
        isLtcResultsStreamActive: () => Boolean(ltcResultsSource && ltcResultsSource.readyState === EventSource.OPEN),
        isSummaryStreamOpen,
        deriveStreamBackedLtcSummary,
        deriveStreamBackedLtcResults,
        resetCaches,
        invalidateCorrelationCache,
    };
}
