import { DASHBOARD_MODE_CHANGED_EVENT, getDashboardMode, getLiveViewSnapshotStore } from '@/modules/shared';
import type { DashboardCore, DashboardRuntimeMode } from '@/types/dashboard';
import type { DashboardSpsaApi, DashboardSpsaPublicApi } from '@/modules/spsa/types';
import type { NormalizedTournamentSummary, TournamentDashboardAPI } from '@/modules/tournament/types';
import { createSpsaApi } from './api';
import type { UpdatesChangedContext } from './types';
import { clearError, showError } from '../components/error';
import { setupSpsaEvents } from './events';
import type { CorrelationRefreshOptions } from './events';
import { renderHero } from '../components/hero';
import { bindRoot, getCachedUpdateDetail, getState, setActive } from '../state';
import {
    renderParameterPanel,
    refreshParameterSelection,
    renderUpdatesLoading,
    renderUpdatesTable,
    renderUpdateDetail,
} from '../components/updates';
import { renderMovers } from '../components/movers';
import {
    renderConvergenceResult,
    renderConvergenceComputed,
    renderCorrelationResult,
    renderCorrelationInsights,
    flushPendingCorrelationResult,
    renderLtcResultsUpdate,
} from '../components/analysis';
import { getAnalysisPipeline } from './analysisPipeline';

type ArenaWindow = Window & {
    DashboardCore?: DashboardCore;
    DashboardSpsa?: DashboardSpsaApi | DashboardSpsaPublicApi;
    DashboardTournament?: TournamentDashboardAPI;
};

const defaultWindow = window as ArenaWindow;
const initializedWindows: WeakSet<ArenaWindow> = new WeakSet();
const pendingModeListeners: WeakMap<ArenaWindow, () => void> = new WeakMap();
const ANALYSIS_REFRESH_DEBOUNCE_MS = 1_500;

function resolveTournamentType(
    core: DashboardCore | undefined,
    normalized: NormalizedTournamentSummary | null,
    _owner: ArenaWindow,
): string | null {
    const runtimeMode = core?.state?.runtimeMode;
    if (runtimeMode === 'spsa' || runtimeMode === 'tournament') {
        return runtimeMode;
    }

    const normalizedType = normalized?.tournamentType ?? null;
    if (typeof normalizedType === 'string' && normalizedType.trim()) {
        return normalizedType.trim().toLowerCase();
    }

    const coreSummary = core?.state?.spsaSummary;
    if (coreSummary && typeof coreSummary === 'object' && 'tournamentType' in coreSummary) {
        const rawType = (coreSummary as { tournamentType?: unknown }).tournamentType;
        if (typeof rawType === 'string' && rawType.trim()) {
            return rawType.trim().toLowerCase();
        }
    }

    return null;
}

function isSpsaMode(
    core: DashboardCore | undefined,
    owner: ArenaWindow,
    normalized: NormalizedTournamentSummary | null,
): boolean {
    if (!core) return false;
    if (getDashboardMode(core) === 'spsa') {
        return true;
    }
    const spsaFlag = Boolean(core.state?.spsaMode);
    const summaryMode = resolveTournamentType(core, normalized, owner) === 'spsa';
    return spsaFlag || summaryMode;
}

function attachNoopApi(owner: ArenaWindow): DashboardSpsaPublicApi {
    const state = getState();
    const noop: DashboardSpsaPublicApi = {
        connect: () => {},
        disconnect: () => {},
        getStateSnapshot: () => state,
        setActive: (active: boolean) => setActive(active),
        fetchUpdateDetail: async () => {
            throw new Error('SPSA dashboard is inactive');
        },
        fetchCorrelationAnalysis: async () => {
            throw new Error('SPSA dashboard is inactive');
        },
        fetchConvergenceAnalysis: async () => {
            throw new Error('SPSA dashboard is inactive');
        },
        fetchLtcSummary: async () => {
            throw new Error('SPSA dashboard is inactive');
        },
        fetchLtcResults: async () => {
            throw new Error('SPSA dashboard is inactive');
        },
        switchTab: () => {
            throw new Error('SPSA dashboard is inactive');
        },
        focusUpdate: () => {
            throw new Error('SPSA dashboard is inactive');
        },
    };
    owner.DashboardSpsa = noop;
    return noop;
}

function isDashboardSpsaPublicApi(
    value: DashboardSpsaApi | DashboardSpsaPublicApi | undefined,
): value is DashboardSpsaPublicApi {
    if (!value) return false;
    return (
        typeof value.connect === 'function' &&
        typeof value.disconnect === 'function' &&
        typeof value.getStateSnapshot === 'function' &&
        typeof value.setActive === 'function' &&
        typeof value.fetchUpdateDetail === 'function' &&
        typeof value.fetchCorrelationAnalysis === 'function' &&
        typeof value.fetchConvergenceAnalysis === 'function' &&
        typeof value.switchTab === 'function' &&
        typeof value.focusUpdate === 'function'
    );
}

export function installSpsaModule(owner: ArenaWindow = defaultWindow): DashboardSpsaPublicApi {
    if (initializedWindows.has(owner)) {
        return owner.DashboardSpsa as DashboardSpsaPublicApi;
    }

    const core = owner.DashboardCore;
    if (!core) {
        throw new Error('DashboardCore must be available before the SPSA module initialises');
    }
    const liveViewStore = getLiveViewSnapshotStore(core);

    const { document } = owner;
    let apiRef: DashboardSpsaPublicApi = isDashboardSpsaPublicApi(owner.DashboardSpsa)
        ? owner.DashboardSpsa
        : attachNoopApi(owner);

    const initialize = (): DashboardSpsaPublicApi => {
        const tournament = owner.DashboardTournament;
        const normalized =
            tournament && typeof tournament.getNormalizedSummary === 'function'
                ? tournament.getNormalizedSummary()
                : null;
        const root = document.getElementById('spsaTab');

        if (!root || !(root instanceof HTMLElement)) {
            throw new Error('SPSA tab container (#spsaTab) is required but was not found');
        }

        if (!isSpsaMode(core, owner, normalized)) {
            return apiRef;
        }

        bindRoot(root);
        const rootInitiallyActive = root.classList.contains('active');
        const state = getState();
        const analysisPipeline = getAnalysisPipeline();
        let triggerParameterAnalysisRefresh: ((options?: CorrelationRefreshOptions) => void) | null = null;
        let refreshConvergenceCharts: (() => void) | null = null;
        let notifyAnalysisStale: (() => void) | null = null;
        let eventsBinding: ReturnType<typeof setupSpsaEvents> | null = null;
        let pendingAnalysisRefresh: { force: boolean } | null = null;

        const renderHeroWithLiveView = () => renderHero(state, liveViewStore.getSnapshot());
        renderHeroWithLiveView();
        renderUpdatesLoading();
        renderParameterPanel(state);
        renderMovers(state);

        let pendingAnalysisRefreshTimer: number | null = null;
        const cancelAnalysisRefresh = (): void => {
            if (pendingAnalysisRefreshTimer !== null) {
                window.clearTimeout(pendingAnalysisRefreshTimer);
                pendingAnalysisRefreshTimer = null;
            }
        };
        const recordPendingAnalysisRefresh = (force: boolean): void => {
            if (pendingAnalysisRefresh) {
                pendingAnalysisRefresh.force = pendingAnalysisRefresh.force || force;
            } else {
                pendingAnalysisRefresh = { force };
            }
        };
        const canRunAnalysisRefresh = (): boolean => {
            const snapshot = getState();
            return snapshot.active && snapshot.params.status === 'ready';
        };
        const performAnalysisRefresh = ({ immediate = false, force = false } = {}): void => {
            if (!triggerParameterAnalysisRefresh || !eventsBinding) {
                recordPendingAnalysisRefresh(force);
                return;
            }
            const isCorrelationActive = eventsBinding.getActiveTab() === 'correlation';
            if (!isCorrelationActive && !force) {
                return;
            }
            const run = () => {
                cancelAnalysisRefresh();
                triggerParameterAnalysisRefresh?.({ force: force || isCorrelationActive });
            };
            if (immediate || isCorrelationActive) {
                run();
                return;
            }
            if (pendingAnalysisRefreshTimer !== null) {
                return;
            }
            pendingAnalysisRefreshTimer = window.setTimeout(run, ANALYSIS_REFRESH_DEBOUNCE_MS);
        };
        const flushPendingAnalysisRefresh = (): void => {
            if (!pendingAnalysisRefresh) {
                return;
            }
            if (!canRunAnalysisRefresh()) {
                return;
            }
            const pending = pendingAnalysisRefresh;
            pendingAnalysisRefresh = null;
            performAnalysisRefresh({ immediate: true, force: pending.force });
        };

        const queueAnalysisRefresh = ({ immediate = false, force = false } = {}): void => {
            const snapshot = getState();
            if (!snapshot.active) {
                recordPendingAnalysisRefresh(force);
                return;
            }
            if (snapshot.params.status !== 'ready') {
                recordPendingAnalysisRefresh(force);
                return;
            }
            const effectiveForce = force || pendingAnalysisRefresh?.force || false;
            pendingAnalysisRefresh = null;
            performAnalysisRefresh({ immediate, force: effectiveForce });
        };

        const handleUpdatesChanged = (context?: UpdatesChangedContext): void => {
            const reason = context?.reason ?? 'entries';
            if (reason === 'variant-games' || reason === 'ltc-games') {
                renderUpdatesTable(state);
                refreshParameterSelection(state);
                const snapshot = getState();
                const expanded = Array.from(snapshot.updates.expanded);
                if (!expanded.length) {
                    return;
                }
                const affected = context?.affectedIndices;
                const targetIdx = expanded.find((idx) => !affected || affected.includes(idx));
                if (targetIdx === undefined) {
                    return;
                }
                const detail = getCachedUpdateDetail(targetIdx);
                if (!detail) {
                    return;
                }
                renderUpdateDetail(detail, snapshot.params.data ?? null);
                return;
            }
            renderHeroWithLiveView();
            renderUpdatesTable(state);
            refreshParameterSelection(state);
            renderMovers(state);
            if (eventsBinding?.getActiveTab() === 'convergence') {
                refreshConvergenceCharts?.();
            }
            queueAnalysisRefresh({ immediate: true, force: true });
        };

        const api = createSpsaApi({
            core,
            document,
            callbacks: {
                onSummaryChanged: () => {
                    renderHeroWithLiveView();
                    renderMovers(state);
                },
                onParamsChanged: () => {
                    renderHeroWithLiveView();
                    renderParameterPanel(state);
                    renderUpdatesTable(state);
                    refreshParameterSelection(state);
                    renderMovers(state);
                    flushPendingCorrelationResult();
                    queueAnalysisRefresh({ immediate: true, force: true });
                    flushPendingAnalysisRefresh();
                },
                onUpdatesChanged: handleUpdatesChanged,
                onAnalysisStale: () => {
                    notifyAnalysisStale?.();
                },
                onConvergenceChanged: (snapshot) => {
                    const resolveSnapshot = snapshot ? Promise.resolve(snapshot) : api.fetchConvergenceAnalysis();
                    resolveSnapshot
                        .then((result) => {
                            const ltcSnapshot = result.ltc_results ?? api.getLtcResultsSnapshot?.(100) ?? null;
                            return analysisPipeline
                                .computeConvergence(result, ltcSnapshot)
                                .then((payload) => {
                                    renderConvergenceComputed(payload, ltcSnapshot);
                                    renderHeroWithLiveView();
                                })
                                .catch((error) => {
                                    console.warn('[SPSA] Convergence worker failed, using main thread', error);
                                    renderConvergenceResult(result);
                                    renderHeroWithLiveView();
                                });
                        })
                        .catch((error) => {
                            console.error('[SPSA] Failed to render convergence update', error);
                        });
                },
                onLtcResultsChanged: () => {
                    const isConvergenceTabActive = eventsBinding?.getActiveTab() === 'convergence';
                    if (!isConvergenceTabActive) {
                        return;
                    }
                    const snapshot = api.getLtcResultsSnapshot?.(100) ?? null;
                    if (snapshot) {
                        renderLtcResultsUpdate(snapshot);
                    }
                },
                onCorrelationChanged: (snapshot) => {
                    const paramsData = state.params.data ?? null;
                    if (!paramsData) {
                        renderCorrelationResult(snapshot);
                        return;
                    }
                    const updatesEntries = state.updates.entries ?? [];
                    const initialEntry = state.updates.initialEntry ?? null;
                    analysisPipeline
                        .computeCorrelation(snapshot, paramsData, updatesEntries, initialEntry)
                        .then((insights) => {
                            renderCorrelationInsights(insights);
                        })
                        .catch((error) => {
                            console.warn('[SPSA] Correlation worker failed, using main thread', error);
                            renderCorrelationResult(snapshot);
                        });
                },
                onError: (message) => showError(message),
                onClearError: () => clearError(),
            },
        });

        Object.assign(apiRef, api);
        const originalSetActive = apiRef.setActive.bind(apiRef);
        apiRef.setActive = (active: boolean) => {
            originalSetActive(active);
            if (active) {
                flushPendingAnalysisRefresh();
            }
        };
        owner.DashboardSpsa = apiRef;

        const tabsApi = owner.DashboardTabs;
        const initialDashboardTab = typeof tabsApi?.getActive === 'function' ? tabsApi.getActive() : null;
        const profile = owner.document?.body?.dataset?.dashboardProfile ?? '';
        const profileIsSpsa = profile.trim().toLowerCase() === 'spsa';
        const isSpsaActive = rootInitiallyActive || initialDashboardTab === 'spsa' || profileIsSpsa;
        apiRef.setActive(isSpsaActive);

        let liveViewSubscriptionReleased = false;
        const unsubscribeLiveView = liveViewStore.subscribe((snapshot) => {
            renderHero(state, snapshot);
        });
        const releaseLiveViewSubscription = (): void => {
            if (liveViewSubscriptionReleased) {
                return;
            }
            liveViewSubscriptionReleased = true;
            try {
                unsubscribeLiveView();
            } catch (error) {
                console.warn('[SPSA] Failed to release live view subscription', error);
            }
        };
        const handleWindowUnload = (): void => {
            releaseLiveViewSubscription();
            eventsBinding?.destroy();
            eventsBinding = null;
            try {
                apiRef.disconnect?.('window-unloading');
            } catch (error) {
                console.warn('[SPSA] Failed to disconnect SPSA API during unload', error);
            }
            owner.removeEventListener?.('beforeunload', handleWindowUnload);
        };
        owner.addEventListener?.('beforeunload', handleWindowUnload);

        eventsBinding = setupSpsaEvents({ root, api: apiRef, state });
        triggerParameterAnalysisRefresh = (options?: CorrelationRefreshOptions) =>
            eventsBinding?.refreshParameterAnalysis(options);
        refreshConvergenceCharts = () => eventsBinding?.refreshConvergence();
        notifyAnalysisStale = () => {
            queueAnalysisRefresh({ immediate: true, force: true });
        };
        flushPendingAnalysisRefresh();

        // Warm parameter analytics so the UI isn't stuck on the placeholder when first opened
        queueAnalysisRefresh({ immediate: true, force: true });

        apiRef.switchTab = (tab) => {
            eventsBinding?.switchTab(tab);
        };
        apiRef.focusUpdate = (updateIdx, options) => {
            eventsBinding?.focusUpdate(updateIdx, options);
        };

        console.info('[SPSA] Dashboard initialized (lazy connect)');
        return apiRef;
    };

    const finalizeInitialization = (): void => {
        if (initializedWindows.has(owner)) {
            return;
        }

        const tournament = owner.DashboardTournament;
        const normalized =
            tournament && typeof tournament.getNormalizedSummary === 'function'
                ? tournament.getNormalizedSummary()
                : null;

        if (!isSpsaMode(core, owner, normalized)) {
            console.info('[SPSA] Dashboard disabled (not an SPSA run).');
            return;
        }

        const run = () => {
            if (initializedWindows.has(owner)) {
                return;
            }
            apiRef = initialize();
            initializedWindows.add(owner);
        };

        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', run, { once: true });
        } else {
            run();
        }
    };

    finalizeInitialization();

    const eventsApi = core.events;
    if (
        !initializedWindows.has(owner) &&
        !pendingModeListeners.has(owner) &&
        eventsApi &&
        typeof eventsApi.on === 'function'
    ) {
        const unsubscribe = eventsApi.on<DashboardRuntimeMode>(DASHBOARD_MODE_CHANGED_EVENT, (mode) => {
            if (mode === 'spsa') {
                unsubscribe?.();
                pendingModeListeners.delete(owner);
                finalizeInitialization();
            }
        });
        pendingModeListeners.set(owner, unsubscribe);
    }

    return apiRef;
}
