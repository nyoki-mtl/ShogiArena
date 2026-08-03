import { createMetricsHelpers } from './metrics';
import { describeError, isLikelyNetworkError } from './errors';
import type {
    DashboardSpsaPublicApi,
    SpsaLtcResultsResponse,
    SpsaRefreshOptions,
    SpsaTabId,
} from '@/modules/spsa/types';
import { toTournamentSummaryFromSpsa } from './normalizers';
import type { NormalizedTournamentEngineMeta, NormalizedTournamentSummary } from '@/modules/tournament/types';
import { summaryStore } from '@/store';
import {
    parseTimeControlSpec as parseTournamentTimeControlSpec,
    formatNodesCountShort,
} from '@/modules/shared/utils/time-control';
import {
    cancelOngoingRequests,
    clearError,
    ensureUpdateCacheCapacity,
    getCachedSummary,
    getState,
    recomputeTrend,
    registerAbortController,
    resetUpdates,
    setActive,
    setError,
    setVisibilityPaused,
    unregisterAbortController,
} from '../state';
import { createSpsaStreams, type SpsaStreamCacheState } from './streams';
import type { SpsaApiOptions } from './types';
export type { UpdatesChangedContext } from './types';
import { createSpsaFetchers } from './fetchers';
import { createScopedHydrator, createStreamCacheState } from './hydration';
import { resetSpsaConsistencyState } from './consistency';
import { createBootstrapManager } from './bootstrap';
import { createRefreshQueue } from './refresh-queue';
import { createLifecycle } from './lifecycle';
import { getResumeCoordinator } from '@/modules/shared/services/resume-coordinator';
import {
    ANALYSIS_HYDRATION_METRIC,
    DEFAULT_UPDATES_LIMIT,
    DETAIL_HYDRATION_METRIC,
    LTC_HYDRATION_METRIC,
    PARAMS_REFRESH_INTERVAL_MS,
} from './api-constants';
import { reportDashboardRecoverableFailure } from '@/modules/shared/utils/errors';

type ErrorSource = 'sse' | 'generic';

export function createSpsaApi({
    core,
    document,
    callbacks = {},
    autoVisibilityRefresh = true,
}: SpsaApiOptions): DashboardSpsaPublicApi {
    const resumeCoordinator = getResumeCoordinator(window);
    resetSpsaConsistencyState();
    const state = getState();
    let activeErrorSource: ErrorSource | null = null;
    const handleError = (message: string, options?: { source?: ErrorSource }): void => {
        activeErrorSource = options?.source ?? 'generic';
        setError(message);
        callbacks.onError?.(message);
    };
    const emitClearError = (): void => {
        activeErrorSource = null;
        clearError();
        callbacks.onClearError?.();
    };
    const clearSseDisconnectNotice = (): void => {
        if (activeErrorSource !== 'sse') {
            return;
        }
        emitClearError();
    };
    const { startHydrationAttempt, recordHydrationCacheHit, recordDetailPayloadMetrics } = createMetricsHelpers();
    ensureUpdateCacheCapacity(DEFAULT_UPDATES_LIMIT);
    const streamCacheState: SpsaStreamCacheState = createStreamCacheState();
    let markAnalysisDataStale: () => void = () => {};
    let deriveCachedLtcResultsRef: (limit: number) => SpsaLtcResultsResponse | null = () => null;

    const reportRecoverableFailure = (
        context: string,
        error: unknown,
        options: { notifyOffline?: boolean; title?: string } = {},
    ): void => {
        console.warn(`[SPSA] ${context}`, error);
        if (options.title) {
            try {
                const detail = describeError(error);
                core.showApiError?.(options.title, detail);
            } catch (uiError) {
                console.warn('[SPSA] Failed to emit dashboard error notice', uiError);
            }
        }
        reportDashboardRecoverableFailure(error, {
            scope: context,
            userMessage: options.title,
        });
        if (options.notifyOffline && isLikelyNetworkError(error)) {
            core.notifyDashboardServerStopped?.();
        }
    };

    const getApiBase = (): string => {
        const base = core.getApiBase?.();
        if (!base) {
            throw new Error('DashboardCore.getApiBase returned an empty string for SPSA module');
        }
        return base;
    };

    const fetchers = createSpsaFetchers({
        core,
        state,
        callbacks,
        getApiBase,
        reportRecoverableFailure,
        handleError,
        startHydrationAttempt,
        recordHydrationCacheHit,
        markAnalysisDataStale: () => markAnalysisDataStale(),
        recordDetailPayloadMetrics,
        streamCacheState,
        resumeCoordinator,
    });

    const shouldRefreshParams = (force?: boolean): boolean => {
        if (force) return true;
        if (!state.params.data) return true;
        const lastFetched = state.params.lastFetched;
        if (typeof lastFetched !== 'number') {
            return true;
        }
        return Date.now() - lastFetched >= PARAMS_REFRESH_INTERVAL_MS;
    };

    const hydrateDetailData = async (): Promise<void> => {
        await Promise.all([
            fetchers.requestParams(true, undefined, { propagateError: true }),
            fetchers.requestUpdates({ limit: DEFAULT_UPDATES_LIMIT, offset: 0, propagateError: true }),
        ]);
    };

    const hydrateAnalysisData = async (): Promise<void> => {
        await Promise.all([fetchers.fetchCorrelationAnalysis(), fetchers.fetchConvergenceAnalysis()]);
    };

    const hydrateLtcData = async (): Promise<void> => {
        await Promise.all([fetchers.fetchLtcSummary(), fetchers.fetchLtcResults(100)]);
    };

    const updatesHydrator = createScopedHydrator(
        DETAIL_HYDRATION_METRIC,
        hydrateDetailData,
        'SpsaApi.hydrateDetailData.updates',
        startHydrationAttempt,
        reportRecoverableFailure,
    );
    const analysisHydrator = createScopedHydrator(
        ANALYSIS_HYDRATION_METRIC,
        hydrateAnalysisData,
        'SpsaApi.hydrateDetailData.analysis',
        startHydrationAttempt,
        reportRecoverableFailure,
    );
    const ltcHydrator = createScopedHydrator(
        LTC_HYDRATION_METRIC,
        hydrateLtcData,
        'SpsaApi.hydrateDetailData.ltc',
        startHydrationAttempt,
        reportRecoverableFailure,
    );
    const performRefresh = async (options: SpsaRefreshOptions): Promise<void> => {
        if (isDashboardOffline()) {
            handleError('Dashboard server is offline');
            core.notifyDashboardServerStopped?.();
            return;
        }

        emitClearError();

        const controller = new AbortController();
        registerAbortController(controller);
        const signal = controller.signal;

        const refreshTasks: Array<Promise<void>> = [];
        refreshTasks.push(fetchers.requestSummary(Boolean(options.force), signal));
        // The revision feed now fires whenever projected data moves, so a forced refresh can
        // arrive every poll. The parameter space cannot change mid-run, so honour its own
        // interval instead of refetching it on every one of those.
        const shouldForceParams = Boolean(options.force) && !options.skipParams;
        if (shouldRefreshParams(shouldForceParams)) {
            refreshTasks.push(fetchers.requestParams(shouldForceParams, signal));
        }

        if (options.force || state.connection.eventSourceStatus !== 'open') {
            refreshTasks.push(fetchers.requestUpdates({ limit: state.updates.limit, offset: 0, signal }));
        }

        try {
            await Promise.all(refreshTasks.length ? refreshTasks : [Promise.resolve()]);
        } finally {
            unregisterAbortController(controller);
        }
    };

    const { refreshAll } = createRefreshQueue({ performRefresh });

    const streams = createSpsaStreams(streamCacheState, {
        getApiBase,
        isDashboardOffline,
        reportRecoverableFailure,
        handleError,
        refreshAll,
        clearSseDisconnectNotice,
        markAnalysisDataStale: () => markAnalysisDataStale(),
    });
    const {
        startRealtimeStreams,
        hideRevisionStream,
        markRevisionOffline,
        closeRevisionStream,
        deriveCachedLtcResults,
        resetCaches,
        invalidateCorrelationCache,
    } = streams;

    const { resetHydrationState, beginBootstrapSequence } = createBootstrapManager({
        fetchers,
        hydrators: {
            updatesHydrator,
            analysisHydrator,
            ltcHydrator,
        },
        resetCaches,
        reportRecoverableFailure,
        startRealtimeStreams,
    });

    deriveCachedLtcResultsRef = (limit: number) => deriveCachedLtcResults(limit);

    markAnalysisDataStale = () => {
        invalidateCorrelationCache();
        analysisHydrator.markDirty();
        callbacks.onAnalysisStale?.();
    };

    const lifecycle = createLifecycle({
        core,
        document,
        callbacks,
        state,
        beginBootstrapSequence,
        resetHydrationState,
        resetUpdates,
        recomputeTrend,
        setActive,
        setVisibilityPaused,
        hideRevisionStream,
        markRevisionOffline,
        closeRevisionStream,
        cancelOngoingRequests,
    });

    const { connect, pause, disconnect, setupVisibilityRefresh } = lifecycle;

    setupVisibilityRefresh(autoVisibilityRefresh);

    const buildNormalizedSummary = (): NormalizedTournamentSummary | null => {
        const summary = getCachedSummary() ?? getState().summary.data;
        if (!summary) {
            return null;
        }
        // Build merged meta from unified store, which has data from all sources
        // (tournament, SPSA, runtime) with proper merge priority
        const mergedMeta: Record<string, NormalizedTournamentEngineMeta> = {};

        // Get engine names from the unified store's view model
        const viewModel = summaryStore.getViewModel();
        for (const name of viewModel.engines) {
            const storeMeta = summaryStore.getEngineMeta(name);
            if (!storeMeta) continue;

            // Convert store meta to tournament format
            const tournamentMeta: NormalizedTournamentEngineMeta = {
                name: storeMeta.name,
                enginePath: storeMeta.enginePath,
                merged_options: storeMeta.merged_options,
                resolved_options: storeMeta.resolved_options,
                option_sources: storeMeta.option_sources,
                option_sources_details: storeMeta.option_sources_details,
                runtime_usi_options: storeMeta.runtime_usi_options,
                runtime_engine_info: storeMeta.runtime_engine_info,
                raw: {} as NormalizedTournamentEngineMeta['raw'],
            };

            // Merge with initial meta, preserving merged_options if store has none
            const existing = mergedMeta[name];
            if (!existing) {
                mergedMeta[name] = tournamentMeta;
            } else {
                const storeHasOptions =
                    tournamentMeta.merged_options && Object.keys(tournamentMeta.merged_options).length > 0;
                const existingHasOptions = existing.merged_options && Object.keys(existing.merged_options).length > 0;
                if (storeHasOptions) {
                    mergedMeta[name] = { ...existing, ...tournamentMeta };
                } else if (existingHasOptions) {
                    // Preserve existing merged_options
                    mergedMeta[name] = { ...tournamentMeta, merged_options: existing.merged_options };
                } else {
                    mergedMeta[name] = tournamentMeta;
                }
            }
        }

        return toTournamentSummaryFromSpsa(summary, mergedMeta);
    };

    const formatTimeControlShort = (spec: string): string => {
        const parsed = parseTournamentTimeControlSpec(spec);

        if (parsed.mode === 'fixed' && parsed.fixedMs) {
            const seconds = Math.max(0, Math.floor(parsed.fixedMs / 1000));
            const minutes = Math.floor(seconds / 60);
            const remainder = seconds % 60;
            return minutes > 0 ? `${minutes}m${remainder ? `${remainder}s` : ''} fixed` : `${seconds}s fixed`;
        }

        if (parsed.mode === 'search') {
            const depthPart = parsed.depth != null ? `d${parsed.depth}` : null;
            const nodesPart = parsed.nodes != null ? `${formatNodesCountShort(parsed.nodes)} nodes` : null;
            return [depthPart, nodesPart].filter(Boolean).join(' ') || 'search-only';
        }

        const pieces: string[] = [];
        if (parsed.initial && parsed.initial > 0) {
            const seconds = Math.floor(parsed.initial / 1000);
            const minutes = Math.floor(seconds / 60);
            const remainder = seconds % 60;
            pieces.push(minutes > 0 ? `${minutes}m${remainder ? `${remainder}s` : ''}` : `${seconds}s`);
        }
        if (parsed.increment && parsed.increment > 0) {
            pieces.push(`+${Math.floor(parsed.increment / 1000)}s`);
        } else if (parsed.byoyomi && parsed.byoyomi > 0) {
            pieces.push(`+b${Math.floor(parsed.byoyomi / 1000)}s`);
        }
        if (parsed.depth != null) pieces.push(`d${parsed.depth}`);
        if (parsed.nodes != null) pieces.push(`${formatNodesCountShort(parsed.nodes)} nodes`);
        return pieces.join(' ').trim() || '—';
    };

    const api: DashboardSpsaPublicApi = {
        connect,
        disconnect,
        getStateSnapshot: () => getState(),
        setActive: (active: boolean) => {
            const currentlyActive = getState().active;
            if (active) {
                if (currentlyActive) {
                    return;
                }
                connect();
            } else {
                if (!currentlyActive) {
                    return;
                }
                pause();
            }
        },
        fetchUpdateDetail: fetchers.fetchUpdateDetail,
        fetchCorrelationAnalysis: fetchers.fetchCorrelationAnalysis,
        fetchConvergenceAnalysis: fetchers.fetchConvergenceAnalysis,
        fetchLtcSummary: fetchers.fetchLtcSummary,
        fetchLtcResults: fetchers.fetchLtcResults,
        getLtcResultsSnapshot: (limit = 100) => deriveCachedLtcResultsRef(limit),
        getNormalizedSummary: () => buildNormalizedSummary(),
        parseTimeControlSpec: (spec: string) => parseTournamentTimeControlSpec(spec),
        formatTimeControlShort,
        ensureDetailHydration: (trigger = 'updates') => {
            // Updatesタブのハイドレーションは常に最優先で起動し、他トリガーに依存させない
            updatesHydrator.ensure('updates', { immediate: trigger === 'analysis' || trigger === 'updates' });
            if (trigger === 'analysis') {
                if (resumeCoordinator.isCritical()) {
                    resumeCoordinator.defer('spsa.analysis.ensure', () => {
                        analysisHydrator.ensure('analysis', { immediate: true });
                    });
                } else {
                    analysisHydrator.ensure('analysis', { immediate: true });
                }
                return;
            }
            if (trigger === 'ltc') {
                if (!getState().active) {
                    ltcHydrator.markDirty();
                    return;
                }
                if (resumeCoordinator.isCritical()) {
                    resumeCoordinator.defer('spsa.ltc.ensure', () => {
                        ltcHydrator.ensure('ltc', { immediate: true });
                    });
                } else {
                    ltcHydrator.ensure('ltc', { immediate: true });
                }
            }
        },
        recordDetailPayloadMetrics,
        switchTab: () => {},
        focusUpdate: () => {},
        notifyTabChange: (_tab: SpsaTabId) => {
            return;
        },
    };

    return api;
}

function isDashboardOffline(): boolean {
    return (
        (window as Window & { ARENA_DASHBOARD_STOPPED?: boolean }).ARENA_DASHBOARD_STOPPED === true ||
        navigator.onLine === false
    );
}
