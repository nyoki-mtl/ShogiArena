import { createMetricsHelpers } from './metrics';
import { describeError, isAbortError, isLikelyNetworkError } from './errors';
import type {
    DashboardSpsaExperimentalFeatures,
    DashboardSpsaPublicApi,
    SpsaLtcResultsResponse,
    SpsaLtcSummary,
    SpsaRefreshOptions,
    SpsaTabId,
} from '@/modules/spsa/types';
import { toTournamentSummaryFromSpsa } from './normalizers';
import type { NormalizedTournamentEngineMeta, NormalizedTournamentSummary } from '@/modules/tournament/types';
import { summaryStore } from '@/store';
import {
    parseTimeControlSpec as parseTournamentTimeControlSpec,
    formatNodesCountShort,
} from '@/modules/shared/utils/timeControl';
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
import { createRefreshQueue } from './refreshQueue';
import { createLifecycle } from './lifecycle';
import { getResumeCoordinator } from '@/modules/shared/services/resumeCoordinator';
import {
    ANALYSIS_HYDRATION_METRIC,
    DEFAULT_UPDATES_LIMIT,
    DETAIL_HYDRATION_METRIC,
    LTC_HYDRATION_METRIC,
    PARAMS_REFRESH_INTERVAL_MS,
    SSE_DISCONNECT_MESSAGE,
} from './api.constants';
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
    const resolveDetailStreamExperiment = (): DashboardSpsaExperimentalFeatures | undefined => {
        if (!document || typeof document.getElementById !== 'function') {
            return undefined;
        }
        const root = document.getElementById('spsaTab');
        if (!root) {
            return undefined;
        }
        const mode = (root.getAttribute('data-detail-stream') || '').toLowerCase();
        if (!mode || mode === 'off') {
            return undefined;
        }
        const endpointAttr = root.getAttribute('data-detail-stream-endpoint');
        const autoStartAttr = root.getAttribute('data-detail-stream-autostart');
        const paramsAttr = root.getAttribute('data-detail-stream-params');
        const autoStart =
            autoStartAttr && autoStartAttr.length > 0
                ? autoStartAttr.toLowerCase() !== '0' && autoStartAttr.toLowerCase() !== 'false'
                : undefined;
        const trimmedEndpoint = endpointAttr?.trim();
        const trimmedParams = paramsAttr?.trim();
        return {
            detailStream: {
                enabled: true,
                endpoint: trimmedEndpoint ? trimmedEndpoint : undefined,
                autoStart,
                params: trimmedParams && trimmedParams.length > 0 ? trimmedParams : undefined,
            },
        };
    };
    const experimentalFlags = resolveDetailStreamExperiment();
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
    const markSseDisconnected = (): void => {
        handleError(SSE_DISCONNECT_MESSAGE, { source: 'sse' });
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
    let isCorrelationStreamActiveRef: () => boolean = () => false;
    let isConvergenceStreamActiveRef: () => boolean = () => false;
    let isLtcResultsStreamActiveRef: () => boolean = () => false;
    let deriveStreamBackedLtcSummaryRef: () => SpsaLtcSummary | null = () => null;
    let deriveStreamBackedLtcResultsRef: (limit: number) => SpsaLtcResultsResponse | null = () => null;

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
        isCorrelationStreamActive: () => isCorrelationStreamActiveRef(),
        isConvergenceStreamActive: () => isConvergenceStreamActiveRef(),
        isLtcResultsStreamActive: () => isLtcResultsStreamActiveRef(),
        deriveStreamBackedLtcSummary: () => deriveStreamBackedLtcSummaryRef(),
        deriveStreamBackedLtcResults: (limit: number) => deriveStreamBackedLtcResultsRef(limit),
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
    const refreshAnalysisNow = (): void => {
        markAnalysisDataStale();
        if (!getState().active) {
            return;
        }
        if (resumeCoordinator.isCritical()) {
            resumeCoordinator.defer('spsa.analysis.refresh', () => {
                analysisHydrator.ensure('analysis', { immediate: true });
            });
            return;
        }
        analysisHydrator.ensure('analysis', { immediate: true });
    };
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

        const isSseConnected = state.connection.eventSourceStatus === 'open';
        const refreshTasks: Array<Promise<void>> = [];
        if (options.force || !isSummaryStreamOpen()) {
            refreshTasks.push(fetchers.requestSummary(Boolean(options.force), signal));
        }
        if (shouldRefreshParams(Boolean(options.force))) {
            refreshTasks.push(fetchers.requestParams(Boolean(options.force), signal));
        }

        if (!isSseConnected) {
            refreshTasks.push(fetchers.requestUpdates({ limit: state.updates.limit, offset: 0, signal }));
        }

        try {
            await Promise.all(refreshTasks.length ? refreshTasks : [Promise.resolve()]);
        } finally {
            unregisterAbortController(controller);
        }
    };

    const { refreshAll } = createRefreshQueue({ performRefresh });

    const refreshSummaryOnly = async (): Promise<void> => {
        try {
            await fetchers.requestSummary(true);
        } catch (error) {
            if (isAbortError(error)) {
                return;
            }
            reportRecoverableFailure('SpsaApi.refreshSummaryOnly', error, { notifyOffline: true });
            handleError(`Failed to refresh SPSA summary: ${describeError(error)}`);
        }
    };

    const streams = createSpsaStreams(streamCacheState, {
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
        markAnalysisDataStale: () => markAnalysisDataStale(),
        refreshAnalysisNow,
    });
    const {
        startRealtimeStreams,
        openUpdatesStream,
        closeUpdatesStream,
        openVariantGamesStream,
        closeVariantGamesStream,
        openLtcGamesStream,
        closeLtcGamesStream,
        openCorrelationStream,
        closeCorrelationStream,
        openConvergenceStream,
        closeConvergenceStream,
        openSummaryStream,
        closeSummaryStream,
        openLtcResultsStream,
        closeLtcResultsStream,
        openLtcProgressStream,
        closeLtcProgressStream,
        isCorrelationStreamActive,
        isConvergenceStreamActive,
        isLtcResultsStreamActive,
        isSummaryStreamOpen,
        deriveStreamBackedLtcSummary,
        deriveStreamBackedLtcResults,
        resetCaches,
        invalidateCorrelationCache,
    } = streams;

    let activeTab: SpsaTabId = 'overview';

    const openTabStreams = (): void => {
        switch (activeTab) {
            case 'correlation':
                openCorrelationStream();
                closeConvergenceStream();
                closeVariantGamesStream();
                return;
            case 'convergence':
                openConvergenceStream();
                closeCorrelationStream();
                closeVariantGamesStream();
                return;
            case 'updates':
                openVariantGamesStream();
                closeCorrelationStream();
                closeConvergenceStream();
                return;
            default:
                closeCorrelationStream();
                closeConvergenceStream();
                closeVariantGamesStream();
        }
    };

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

    isCorrelationStreamActiveRef = () => isCorrelationStreamActive();
    isConvergenceStreamActiveRef = () => isConvergenceStreamActive();
    deriveStreamBackedLtcSummaryRef = () => deriveStreamBackedLtcSummary();
    deriveStreamBackedLtcResultsRef = (limit: number) => deriveStreamBackedLtcResults(limit);
    isLtcResultsStreamActiveRef = () => isLtcResultsStreamActive();

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
        refreshAll,
        setActive,
        setVisibilityPaused,
        openStreams: {
            summary: openSummaryStream,
            updates: openUpdatesStream,
            variantGames: openVariantGamesStream,
            ltcGames: openLtcGamesStream,
            correlation: openCorrelationStream,
            convergence: openConvergenceStream,
            ltcResults: openLtcResultsStream,
            ltcProgress: openLtcProgressStream,
        },
        openTabStreams,
        closeStreams: {
            summary: closeSummaryStream,
            updates: closeUpdatesStream,
            variantGames: closeVariantGamesStream,
            ltcGames: closeLtcGamesStream,
            correlation: closeCorrelationStream,
            convergence: closeConvergenceStream,
            ltcResults: closeLtcResultsStream,
            ltcProgress: closeLtcProgressStream,
        },
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
        getLtcResultsSnapshot: (limit = 100) => deriveStreamBackedLtcResultsRef(limit),
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
        notifyTabChange: (tab: SpsaTabId) => {
            activeTab = tab;
            if (!getState().active) {
                return;
            }
            openTabStreams();
        },
        experimental: experimentalFlags,
    };

    return api;
}

function isDashboardOffline(): boolean {
    return (window as Window & { ARENA_DASHBOARD_STOPPED?: boolean }).ARENA_DASHBOARD_STOPPED === true;
}
