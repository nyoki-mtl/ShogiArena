import type { DashboardGamesApi } from '@/modules/games/types';
import type { DashboardCore } from '@/types/dashboard';
import type {
    DashboardSpsaPublicApi,
    NormalizedSpsaUpdateDetail,
    NormalizedSpsaUpdateEntry,
    SpsaDashboardState,
    SpsaTabId,
    SpsaUpdateDetailResponse,
    SpsaDetailInclude,
    SpsaDetailWindowMode,
    NormalizedSpsaGame,
} from '@/modules/spsa/types';
import { INITIAL_UPDATE_IDX } from '@/modules/spsa/types';
import type { DashboardNavigationApi } from '@/types/globals';
import {
    renderConvergenceError,
    renderConvergenceLoading,
    renderConvergenceComputed,
    renderConvergenceResult,
    renderCorrelationError,
    renderCorrelationLoading,
    renderCorrelationInsights,
    renderCorrelationResult,
    focusParameterInAnalysis,
    flushPendingCorrelationResult,
} from '../components/analysis';
import {
    renderUpdateDetail,
    renderUpdateDetailError,
    renderUpdatesLoading,
    renderUpdatesTable,
    showUpdateDetailLoading,
    refreshParameterSelection,
} from '../components/updates';
import { getCachedUpdateDetail, getUpdateFromCache, setUpdateExpanded, getState } from '../state';
import { createDetailStreamController, createDetailStreamNotifier } from './detailStream';
import { getAnalysisPipeline } from './analysisPipeline';
import { createAnalysisStateMachine } from './analysisStateMachine';
import {
    SPSA_DETAIL_DEFAULT_WINDOW,
    SPSA_DETAIL_REQUIRED_INCLUDES,
    type SpsaDetailResolvedWindow,
} from '@/modules/spsa/constants';
import type { JsonObject } from '@/types/shared';

interface EventOptions {
    readonly root: HTMLElement;
    readonly api: DashboardSpsaPublicApi;
    readonly state: SpsaDashboardState;
}

interface SpsaEventBinding {
    destroy: () => void;
    focusUpdate: (updateIdx: number, options?: { scroll?: boolean }) => void;
    switchTab: (tab: SpsaTabId) => void;
    refreshParameterAnalysis: (options?: CorrelationRefreshOptions) => void;
    refreshConvergence: (options?: { showLoading?: boolean }) => void;
    getActiveTab: () => SpsaTabId;
}

const TAB_BUTTON_SELECTOR = '.tab-button';
const TAB_CONTENT_SELECTOR = '.tab-content';

const ACTION_UPDATE_DETAIL = 'spsa:update-detail';
const ACTION_SWITCH_TAB = 'spsa:switch-tab';
const ACTION_FOCUS_PARAMETER = 'spsa:focus-parameter';
const DEFAULT_DETAIL_STREAM_PARAMS = `view=slim&window=${SPSA_DETAIL_DEFAULT_WINDOW}&include=ltc_games`;
const REQUIRED_DETAIL_INCLUDES = new Set<SpsaDetailInclude>(SPSA_DETAIL_REQUIRED_INCLUDES);
const pendingDetailIncludes = new Map<number, Set<SpsaDetailInclude>>();
const inflightDetailRequests = new Map<number, symbol>();
let activeTab: SpsaTabId = 'updates';

const SPSA_TABS: readonly SpsaTabId[] = ['overview', 'updates', 'correlation', 'convergence', 'export'];
const isSpsaTabId = (value: string): value is SpsaTabId => (SPSA_TABS as readonly string[]).includes(value);

type SpsaWindow = Window & {
    DashboardGames?: DashboardGamesApi;
    DashboardNavigation?: DashboardNavigationApi;
    DashboardCore?: DashboardCore;
};

function openVariantGameInLiveView(gameId: string, status: string): void {
    if (!gameId) return;
    const owner = window as SpsaWindow;
    const gamesApi = owner.DashboardGames;
    if (gamesApi && typeof gamesApi.navigateToLiveView === 'function') {
        gamesApi.navigateToLiveView(gameId, { source: 'spsa-variant', status });
        return;
    }

    const navigation = owner.DashboardNavigation;
    if (navigation && typeof navigation.openGame === 'function') {
        void navigation
            .openGame(gameId, { source: 'spsa-variant', status })
            .catch((error: unknown) => console.error('[SPSA] Failed to open Live View', error));
        return;
    }

    navigation?.openTab?.('live');
}

export function setupSpsaEvents({ root, api, state }: EventOptions): SpsaEventBinding {
    boundApi = api;
    root.querySelectorAll<HTMLElement>(TAB_BUTTON_SELECTOR).forEach((button) => {
        if (!button.dataset.action) {
            button.dataset.action = ACTION_SWITCH_TAB;
        }
    });

    const onClick = (event: Event) => {
        const rawTarget = event.target as HTMLElement | null;
        if (!rawTarget) return;

        const gameLink = rawTarget.closest<HTMLButtonElement>('.games-game-link');
        if (gameLink && root.contains(gameLink)) {
            event.preventDefault();
            event.stopPropagation();
            const gameId = (gameLink.dataset.gameId || gameLink.textContent || '').trim();
            if (gameId) {
                const status = (gameLink.dataset.status || '').trim();
                openVariantGameInLiveView(gameId, status);
            }
            return;
        }

        const target = rawTarget.closest<HTMLElement>('[data-action]');
        if (!target || !root.contains(target)) return;

        const action = target.dataset.action;
        if (action === ACTION_UPDATE_DETAIL) {
            event.preventDefault();
            const row = target.closest<HTMLTableRowElement>('.update-row');
            const idx = row?.dataset.updateIdx ? Number(row.dataset.updateIdx) : Number(target.dataset.updateIdx);
            if (Number.isFinite(idx)) {
                void handleUpdateRowClick(api, state, idx);
                syncDetailStreamFocus();
            }
            return;
        }

        if (action === ACTION_FOCUS_PARAMETER) {
            event.preventDefault();
            const paramTarget = target.closest<HTMLElement>('[data-param-name]');
            const name = target.getAttribute('data-param-name') || paramTarget?.getAttribute('data-param-name') || '';
            const trimmed = name.trim();
            if (trimmed) {
                focusParameterInAnalysis(trimmed, { scrollIntoView: true });
                switchTabBinding('correlation');
            }
            return;
        }

        if (action === ACTION_SWITCH_TAB) {
            event.preventDefault();
            const tab = target.getAttribute('data-tab');
            if (tab && isSpsaTabId(tab)) {
                switchTabBinding(tab);
            }
        }
    };

    renderUpdatesLoading();
    root.addEventListener('click', onClick);

    const detailStreamExperiment = api.experimental?.detailStream;
    const detailStreamAutoStart = detailStreamExperiment?.autoStart ?? false;
    const detailStreamNotifier = createDetailStreamNotifier(window.DashboardCore);
    const detailStream = detailStreamExperiment?.enabled
        ? createDetailStreamController({
              state,
              api,
              endpoint: detailStreamExperiment.endpoint,
              params: detailStreamExperiment.params ?? DEFAULT_DETAIL_STREAM_PARAMS,
              autoStreamIdle: detailStreamAutoStart,
              notifier: detailStreamNotifier,
          })
        : null;
    if (detailStream && detailStreamAutoStart) {
        detailStream.start();
    }

    const syncDetailStreamFocus = () => {
        if (!detailStream || detailStreamAutoStart) {
            return;
        }
        const targets = Array.from(state.updates.expanded);
        detailStream.updateTargets(targets);
        if (targets.length > 0) {
            detailStream.start();
        }
    };

    const clearDetailStreamFocus = () => {
        if (!detailStream || detailStreamAutoStart) {
            return;
        }
        detailStream.updateTargets([]);
    };

    const switchTabBinding = (tabName: SpsaTabId) => {
        switchTab(root, tabName, api, state);
        activeTab = tabName;
        api.notifyTabChange?.(tabName);
        if (tabName === 'updates') {
            syncDetailStreamFocus();
        } else {
            clearDetailStreamFocus();
        }
    };

    const resolveInitialTab = (): SpsaTabId => {
        const buttons = Array.from(root.querySelectorAll<HTMLElement>('.analysis-tabs .tab-button[data-tab]'));
        const resolveButtonTab = (): string | null => {
            for (const button of buttons) {
                if (button.classList.contains('active') || button.getAttribute('aria-selected') === 'true') {
                    return button.getAttribute('data-tab');
                }
            }
            return null;
        };
        const buttonTab = resolveButtonTab();
        if (buttonTab === 'correlation' || buttonTab === 'convergence' || buttonTab === 'updates') {
            return buttonTab;
        }
        const activeContent = root.querySelector<HTMLElement>(`${TAB_CONTENT_SELECTOR}.active`);
        if (activeContent?.id === 'correlation-tab') {
            return 'correlation';
        }
        if (activeContent?.id === 'convergence-tab') {
            return 'convergence';
        }
        if (activeContent?.id === 'updates-tab') {
            return 'updates';
        }
        return 'overview';
    };

    const initialTab = resolveInitialTab();
    switchTabBinding(initialTab);

    const focusUpdateBinding = (updateIdx: number, options?: { scroll?: boolean }) => {
        if (!Number.isFinite(updateIdx)) return;
        switchTabBinding('updates');
        api.ensureDetailHydration?.('updates');
        renderUpdatesTable(state);
        void handleUpdateRowClick(api, state, updateIdx);
        syncDetailStreamFocus();
        if (options?.scroll === false) return;
        renderUpdatesTable(state);
        const row = root.querySelector<HTMLElement>(`.update-row[data-update-idx="${updateIdx}"]`);
        if (row && typeof row.scrollIntoView === 'function') {
            try {
                row.scrollIntoView({ behavior: 'smooth', block: 'center' });
            } catch (error) {
                console.warn('[SPSA] Smooth scrolling failed when focusing update row', error);
                row.scrollIntoView();
            }
        }
    };

    return {
        destroy: () => {
            root.removeEventListener('click', onClick);
            detailStream?.updateTargets([]);
            detailStream?.stop();
        },
        focusUpdate: focusUpdateBinding,
        switchTab: switchTabBinding,
        refreshParameterAnalysis: (options?: CorrelationRefreshOptions) => {
            api.ensureDetailHydration?.('analysis');
            const mergedOptions: CorrelationRefreshOptions = {
                ...options,
                force: (options?.force ?? false) || activeTab === 'correlation',
            };
            requestCorrelationRefresh(api, mergedOptions);
        },
        refreshConvergence: (options) => requestConvergenceRefresh(api, options ?? {}),
        getActiveTab: () => activeTab,
    };
}

async function handleUpdateRowClick(
    api: DashboardSpsaPublicApi,
    state: SpsaDashboardState,
    updateIdx: number,
): Promise<void> {
    setUpdateExpanded(updateIdx, true);
    renderUpdatesTable(state);

    const cached = getCachedUpdateDetail(updateIdx);
    const summaryEntry = getUpdateFromCache(updateIdx);
    const needsRefresh = shouldRefreshDetail(cached, summaryEntry);

    if (cached) {
        renderUpdateDetail(cached, state.params.data ?? null);
        ensureDetailSectionsLoaded(api, cached, { hydrateOptionalSections: true });
        if (!needsRefresh) {
            return;
        }
    }

    if (!cached) {
        showUpdateDetailLoading(updateIdx);
        if (summaryEntry) {
            const placeholder = buildDetailFromSummary(summaryEntry);
            renderUpdateDetail(placeholder, state.params.data ?? null);
        }
    }

    const requestToken = Symbol(`detail:${updateIdx}`);
    inflightDetailRequests.set(updateIdx, requestToken);
    try {
        const detail = await api.fetchUpdateDetail(updateIdx, { include: ['variant_games', 'ltc_games'] });
        if (inflightDetailRequests.get(updateIdx) !== requestToken) {
            return;
        }
        inflightDetailRequests.delete(updateIdx);
        renderUpdateDetail(detail, state.params.data ?? null);
        ensureDetailSectionsLoaded(api, detail, { hydrateOptionalSections: true });
    } catch (error) {
        if (inflightDetailRequests.get(updateIdx) === requestToken) {
            inflightDetailRequests.delete(updateIdx);
            renderUpdateDetailError(updateIdx, 'Failed to load update details');
        }
        throw new Error('Failed to load SPSA update detail', { cause: error });
    }
}

type DetailSectionOptions = {
    readonly hydrateOptionalSections?: boolean;
};

function ensureDetailSectionsLoaded(
    api: DashboardSpsaPublicApi,
    detail: NormalizedSpsaUpdateDetail,
    options: DetailSectionOptions = {},
): void {
    if (typeof api.fetchUpdateDetail !== 'function') {
        return;
    }
    if (!options.hydrateOptionalSections) {
        return;
    }
    const missing = computeMissingIncludes(detail);
    if (!missing.length) {
        return;
    }
    const key = detail.isInitial ? INITIAL_UPDATE_IDX : detail.updateIdx;
    const tracker = pendingDetailIncludes.get(key) ?? new Set<SpsaDetailInclude>();
    const requestable = missing.filter((include) => !tracker.has(include));
    if (!requestable.length) {
        return;
    }
    for (const include of requestable) {
        tracker.add(include);
    }
    pendingDetailIncludes.set(key, tracker);
    const detailWindow: SpsaDetailWindowMode | undefined = requestable.includes('score_history') ? 'long' : undefined;
    void api
        .fetchUpdateDetail(key, { include: requestable, window: detailWindow })
        .then((latestDetail) => {
            const currentState = getState();
            if (currentState.updates.expanded.has(key)) {
                renderUpdateDetail(latestDetail, currentState.params.data ?? null);
            }
        })
        .catch((error) => console.warn('[SPSA] Failed to backfill SPSA detail sections', error))
        .finally(() => {
            const current = pendingDetailIncludes.get(key);
            if (!current) return;
            for (const include of requestable) {
                current.delete(include);
            }
            if (current.size === 0) {
                pendingDetailIncludes.delete(key);
            }
        });
}

function computeMissingIncludes(detail: NormalizedSpsaUpdateDetail): SpsaDetailInclude[] {
    if (detail.isInitial) {
        return [];
    }
    const missing: SpsaDetailInclude[] = [];
    REQUIRED_DETAIL_INCLUDES.forEach((include) => {
        if (!shouldAttemptInclude(detail, include)) {
            return;
        }
        if (detail.loadedIncludes.includes(include)) {
            return;
        }
        missing.push(include);
    });
    return missing;
}

function shouldAttemptInclude(detail: NormalizedSpsaUpdateDetail, include: SpsaDetailInclude): boolean {
    const available = detail.availableIncludes.includes(include);
    switch (include) {
        case 'variant_games':
            return available || (Number.isFinite(detail.gamesCount) && (detail.gamesCount as number) > 0);
        case 'ltc_games':
            return (
                available ||
                (Number.isFinite(detail.ltcGamesCount) && (detail.ltcGamesCount as number) > 0) ||
                Boolean(detail.ltcRegression) ||
                Boolean(detail.hasLtcRegression)
            );
        case 'score_history':
            return available;
        default:
            return available;
    }
}

function buildDetailFromSummary(entry: NormalizedSpsaUpdateEntry): NormalizedSpsaUpdateDetail {
    const perturbations = entry.perturbations ?? { plus: {}, minus: {} };
    const fallbackWindow: SpsaDetailResolvedWindow = 'full';
    return {
        raw: (entry.raw as unknown as SpsaUpdateDetailResponse) ?? ({} as SpsaUpdateDetailResponse),
        updateIdx: entry.updateIdx,
        variantId: entry.variantId,
        params: entry.params ?? {},
        deltas: entry.deltas ?? {},
        gradients: entry.gradients ?? {},
        perturbations,
        sPlus: entry.sPlus ?? null,
        sMinus: entry.sMinus ?? null,
        step: entry.step ?? null,
        gainCk: entry.gainCk ?? null,
        gainAk: entry.gainAk ?? null,
        isPending: entry.isPending ?? false,
        isInitial: entry.isInitial ?? entry.updateIdx === INITIAL_UPDATE_IDX,
        engines: { baseline: null, tuned: null },
        wdl: {
            wins: entry.wins ?? 0,
            losses: entry.losses ?? 0,
            draws: entry.draws ?? 0,
        },
        games: [] as NormalizedSpsaGame[],
        gamesCount: entry.gamesCompleted ?? 0,
        ltcGames: [] as NormalizedSpsaGame[],
        ltcGamesCount: 0,
        ltcRegression: entry.ltcRegression ?? null,
        hasLtcRegression: entry.hasLtcRegression ?? Boolean(entry.ltcRegression),
        phaseWdl: entry.phaseWdl ?? {
            plus: { wins: 0, losses: 0, draws: 0 },
            minus: { wins: 0, losses: 0, draws: 0 },
        },
        timestamp: entry.timestamp ?? null,
        startedAt: entry.startedAt ?? null,
        endedAt: entry.endedAt ?? null,
        deltaNorm: entry.deltaNorm ?? null,
        deltaStep: entry.deltaStep ?? null,
        payload: (entry.payload as JsonObject) ?? {},
        view: 'full',
        detailWindow: fallbackWindow,
        loadedIncludes: [],
        availableIncludes: [],
        scoreHistory: [],
    } as NormalizedSpsaUpdateDetail;
}

function shouldRefreshDetail(
    cached: NormalizedSpsaUpdateDetail | null,
    summary: NormalizedSpsaUpdateEntry | null,
): boolean {
    if (!cached) {
        return true;
    }
    if (!summary) {
        return false;
    }

    const cachedGames = Number.isFinite(cached.gamesCount) ? (cached.gamesCount as number) : 0;
    const summaryGames = Number.isFinite(summary.gamesCompleted) ? (summary.gamesCompleted as number) : 0;
    if (summaryGames > cachedGames) {
        return true;
    }

    const cachedHasGames = Array.isArray(cached.games) && cached.games.length > 0;
    if (!cachedHasGames && summaryGames > 0) {
        return true;
    }

    if (!summary.isPending && cached.isPending) {
        return true;
    }

    return false;
}

const loadedTabs = new Set<string>();
let correlationRefreshInFlight = false;
let correlationRefreshQueued = false;
let correlationRetryTimer: number | null = null;
let convergenceRefreshInFlight = false;
let convergenceRefreshQueued = false;

let boundApi: DashboardSpsaPublicApi | null = null;

const correlationStateMachine = createAnalysisStateMachine({
    onStateChange: (transition) => {
        if (transition.to === 'warming') {
            renderCorrelationLoading();
        } else if (transition.to === 'error') {
            renderCorrelationError(transition.reason ?? 'Analysis timed out');
        }
    },
    onAutoRetry: () => {
        if (boundApi) {
            requestCorrelationRefresh(boundApi, { showLoading: false });
        }
    },
});

const convergenceStateMachine = createAnalysisStateMachine({
    onStateChange: (transition) => {
        if (transition.to === 'warming') {
            renderConvergenceLoading();
        } else if (transition.to === 'error') {
            renderConvergenceError(transition.reason ?? 'Analysis timed out');
        }
    },
    onAutoRetry: () => {
        if (boundApi) {
            requestConvergenceRefresh(boundApi);
        }
    },
});

type ConvergenceRefreshOptions = {
    readonly showLoading?: boolean;
    readonly resetOnFailure?: boolean;
};

function requestConvergenceRefresh(api: DashboardSpsaPublicApi, options: ConvergenceRefreshOptions = {}): void {
    if (!loadedTabs.has('convergence')) {
        return;
    }
    if (convergenceRefreshInFlight) {
        convergenceRefreshQueued = true;
        return;
    }
    convergenceRefreshInFlight = true;
    convergenceStateMachine.startLoading();
    if (options.showLoading) {
        renderConvergenceLoading();
    }
    Promise.resolve()
        .then(() => api.fetchConvergenceAnalysis())
        .then((result) => {
            convergenceStateMachine.handleResponse(result.status ?? 'ready');
            if (result.status !== 'ready') {
                return;
            }
            const ltcSnapshot = result.ltc_results ?? api.getLtcResultsSnapshot?.(100) ?? null;
            return analysisPipeline
                .computeConvergence(result, ltcSnapshot)
                .then((payload) => {
                    renderConvergenceComputed(payload, ltcSnapshot);
                })
                .catch((error) => {
                    console.warn('[SPSA] Convergence worker failed, using main thread', error);
                    renderConvergenceResult(result);
                });
        })
        .catch((error) => {
            console.error('[SPSA] Failed to load convergence analysis', error);
            convergenceStateMachine.handleResponse('error');
            renderConvergenceError('Failed to load convergence analysis');
            if (options.resetOnFailure) {
                loadedTabs.delete('convergence');
            }
        })
        .finally(() => {
            convergenceRefreshInFlight = false;
            if (convergenceRefreshQueued) {
                convergenceRefreshQueued = false;
                requestConvergenceRefresh(api);
            }
        });
}

export type CorrelationRefreshOptions = {
    readonly showLoading?: boolean;
    readonly force?: boolean;
};

const CORRELATION_RETRY_DELAY_MS = 3_000;
const analysisPipeline = getAnalysisPipeline();

function scheduleCorrelationRetry(
    api: DashboardSpsaPublicApi,
    options: CorrelationRefreshOptions,
    reason: string,
): void {
    if (correlationRetryTimer !== null) {
        return;
    }
    correlationRetryTimer = window.setTimeout(() => {
        correlationRetryTimer = null;
        requestCorrelationRefresh(api, { ...options, showLoading: false });
    }, CORRELATION_RETRY_DELAY_MS);
    console.warn('[SPSA] Correlation refresh retry scheduled due to', reason);
}

function requestCorrelationRefresh(api: DashboardSpsaPublicApi, options: CorrelationRefreshOptions = {}): void {
    const shouldRun = options.force || loadedTabs.has('correlation');
    if (!shouldRun) return;
    if (correlationRefreshInFlight) {
        correlationRefreshQueued = true;
        return;
    }
    if (correlationRetryTimer !== null) {
        window.clearTimeout(correlationRetryTimer);
        correlationRetryTimer = null;
    }
    correlationRefreshInFlight = true;
    correlationStateMachine.startLoading();
    if (options.showLoading) {
        renderCorrelationLoading();
    }
    Promise.resolve()
        .then(() => api.fetchCorrelationAnalysis())
        .then((result) => {
            correlationStateMachine.handleResponse(result.status ?? 'ready');
            if (result.status !== 'ready') {
                return;
            }
            const paramsData = getState().params.data ?? null;
            if (!paramsData) {
                renderCorrelationResult(result);
                return;
            }
            const stateSnapshot = getState();
            const updatesEntries = stateSnapshot.updates.entries ?? [];
            const initialEntry = stateSnapshot.updates.initialEntry ?? null;
            return analysisPipeline
                .computeCorrelation(result, paramsData, updatesEntries, initialEntry)
                .then((insights) => {
                    renderCorrelationInsights(insights);
                })
                .catch((error) => {
                    console.warn('[SPSA] Correlation worker failed, using main thread', error);
                    renderCorrelationResult(result);
                });
        })
        .catch((error) => {
            correlationStateMachine.handleResponse('error');
            renderCorrelationError('Failed to load correlation analysis');
            scheduleCorrelationRetry(api, options, error instanceof Error ? error.message : String(error));
        })
        .finally(() => {
            correlationRefreshInFlight = false;
            if (correlationRefreshQueued) {
                correlationRefreshQueued = false;
                requestCorrelationRefresh(api, { showLoading: false });
            }
        });
}

function switchTab(root: HTMLElement, tabName: string, api: DashboardSpsaPublicApi, state: SpsaDashboardState): void {
    const buttons = root.querySelectorAll<HTMLElement>(TAB_BUTTON_SELECTOR);
    buttons.forEach((btn) => {
        btn.classList.toggle('active', btn.dataset.tab === tabName);
    });

    const contents = root.querySelectorAll<HTMLElement>(TAB_CONTENT_SELECTOR);
    contents.forEach((content) => {
        content.classList.toggle('active', content.id === `${tabName}-tab`);
    });

    if (tabName === 'correlation') {
        const firstActivation = !loadedTabs.has('correlation');
        loadedTabs.add('correlation');
        api.ensureDetailHydration?.('analysis');
        const renderedFromCache = flushPendingCorrelationResult();
        requestCorrelationRefresh(api, { showLoading: firstActivation && !renderedFromCache, force: true });
        return;
    }

    if (tabName === 'convergence') {
        const firstActivation = !loadedTabs.has('convergence');
        if (firstActivation) {
            loadedTabs.add('convergence');
        }
        api.ensureDetailHydration?.('analysis');
        requestConvergenceRefresh(api, { showLoading: firstActivation, resetOnFailure: firstActivation });
        return;
    }

    if (tabName === 'updates') {
        api.ensureDetailHydration?.('updates');
        renderUpdatesTable(state);
        refreshParameterSelection(state);
    }
}
