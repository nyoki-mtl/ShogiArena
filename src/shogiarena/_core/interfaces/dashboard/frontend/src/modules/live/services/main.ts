import { installLiveDiagnosticsPanel } from '@/modules/live/services/diagnosticsPanel';
import {
    ensureLiveNamespace,
    recordLiveDiagnosticsMetric,
    requireLiveApi,
    type LiveNamespaceOwner,
} from '@/modules/live/utils/liveNamespace';
import { applyTabConfiguration, getDashboardMode, getModeConfig, setDashboardMode } from '@/modules/shared';
import { requestJson } from '@/modules/shared/services/api';
import { crash } from '@/modules/shared/utils/errors';
import { SPSA_SUMMARY_PROGRESS_EVENT } from '@/modules/spsa/constants';
import type { SpsaSummaryResponse } from '@/modules/spsa/types';
import type { DashboardCore, DashboardRuntimeMode } from '@/types/dashboard';
import type { DashboardTabsApi } from '@/types/globals';

interface LiveMainWindow extends LiveNamespaceOwner {
    DashboardCore?: DashboardCore;
    DashboardTabs?: DashboardTabsApi;
    ARENA_DISABLE_SPSA?: boolean;
    ARENA_RUNTIME_MODE?: DashboardRuntimeMode;
}

const defaultWindow = window as LiveMainWindow;

let initialized = false;

type SpsaSummarySnapshot = Partial<
    Pick<SpsaSummaryResponse, 'mode' | 'wins' | 'losses' | 'draws' | 'total' | 'completed'>
> & { isFinal?: boolean; unitLabel?: string };

const coerceNumber = (value: unknown): number | undefined => {
    if (typeof value === 'number' && Number.isFinite(value)) {
        return value;
    }
    return undefined;
};

function coerceSpsaSummarySnapshot(value: unknown): SpsaSummarySnapshot | null {
    if (!value || typeof value !== 'object' || Array.isArray(value)) {
        return null;
    }
    const record = value as Record<string, unknown>;
    const snapshot: SpsaSummarySnapshot = {};
    if (typeof record.mode === 'string') snapshot.mode = record.mode;
    const wins = coerceNumber(record.wins);
    if (wins !== undefined) snapshot.wins = wins;
    const losses = coerceNumber(record.losses);
    if (losses !== undefined) snapshot.losses = losses;
    const draws = coerceNumber(record.draws);
    if (draws !== undefined) snapshot.draws = draws;
    const total = coerceNumber(record.total);
    if (total !== undefined) snapshot.total = total;
    const completed = coerceNumber(record.completed);
    if (completed !== undefined) snapshot.completed = completed;
    if (typeof record.isFinal === 'boolean') snapshot.isFinal = record.isFinal;
    if (typeof record.unitLabel === 'string') snapshot.unitLabel = record.unitLabel;
    return snapshot;
}

function mergeSpsaSummarySnapshot(existing: SpsaSummarySnapshot | null, patch: unknown): SpsaSummarySnapshot | null {
    const normalizedPatch = coerceSpsaSummarySnapshot(patch);
    if (!existing && !normalizedPatch) {
        return null;
    }
    return { ...(existing ?? {}), ...(normalizedPatch ?? {}) } as SpsaSummarySnapshot;
}

export function initializeLiveMain(owner: LiveMainWindow = defaultWindow): void {
    if (initialized) {
        return;
    }

    const core = owner.DashboardCore;
    if (!core) {
        throw new Error('DashboardCore must be loaded before live module');
    }
    const dashboardCore: DashboardCore = core;

    ensureLiveNamespace(owner);
    installLiveDiagnosticsPanel(owner);
    requireLiveApi(owner, 'time', 'DashboardLiveTime must be loaded before live module');
    const cardsApi = requireLiveApi(owner, 'cards', 'DashboardLiveCards must be loaded before live module');
    const updatesApi = requireLiveApi(owner, 'updates', 'DashboardLiveUpdates must be loaded before live module');
    const summaryApi = requireLiveApi(owner, 'summary', 'DashboardLiveSummary must be loaded before live module');
    const tabsApi = owner.DashboardTabs;

    if (!tabsApi) {
        throw new Error('DashboardTabs must be loaded before live module');
    }

    if (typeof updatesApi.setupLiveUpdates !== 'function') {
        throw new Error('DashboardLiveUpdates.setupLiveUpdates must be a function');
    }

    if (typeof cardsApi.initializeCards !== 'function') {
        throw new Error('DashboardLiveCards.initializeCards must be a function');
    }

    if (typeof summaryApi.updateSummaryStats !== 'function') {
        throw new Error('DashboardLiveSummary.updateSummaryStats must be a function');
    }

    const { state, setDisplay, getApiBase } = dashboardCore;
    const spsaFeatureAvailable = owner.ARENA_DISABLE_SPSA !== true;

    // No periodic refresh: SSE updates and on-demand API fetches drive the UI.
    if (typeof PerformanceObserver !== 'undefined') {
        try {
            const observer = new PerformanceObserver((list) => {
                for (const entry of list.getEntries()) {
                    const duration =
                        typeof entry.duration === 'number' && Number.isFinite(entry.duration) ? entry.duration : 0;
                    const start =
                        typeof entry.startTime === 'number' && Number.isFinite(entry.startTime) ? entry.startTime : 0;
                    recordLiveDiagnosticsMetric('live.longtask', {
                        triggered: 1,
                        duration_ms: duration,
                        start_ms: start,
                    });
                }
            });
            observer.observe({ entryTypes: ['longtask'] });
        } catch (error) {
            console.debug?.('[Live] Longtask observer init failed', error);
        }
    }

    function applyRuntimeMode(mode: DashboardRuntimeMode): void {
        const normalizedMode: DashboardRuntimeMode =
            mode === 'spsa'
                ? 'spsa'
                : mode === 'tournament' || mode === 'match' || mode === 'sprt' || mode === 'generate'
                  ? mode
                  : 'unknown';
        owner.ARENA_RUNTIME_MODE = normalizedMode;
        state.spsaMode = normalizedMode === 'spsa';
        const config = getModeConfig(normalizedMode);
        owner.ARENA_DISABLE_SPSA = spsaFeatureAvailable ? config.disableSpsaModule : true;
        setDashboardMode(dashboardCore, normalizedMode);
        applyTabConfiguration(tabsApi, normalizedMode);

        const showSpsaLayout = normalizedMode === 'spsa';
        setDisplay('standingsSection', showSpsaLayout ? 'none' : 'block');
        setDisplay('spsaSection', showSpsaLayout ? 'block' : 'none');

        if (!showSpsaLayout) {
            const ratingChart = document.querySelector<HTMLElement>('.rating-chart');
            if (ratingChart) setDisplay(ratingChart, 'none');
        }
    }

    function getCurrentRuntimeMode(): DashboardRuntimeMode {
        const declared = owner.ARENA_RUNTIME_MODE;
        if (typeof declared === 'string') {
            return declared as DashboardRuntimeMode;
        }
        return getDashboardMode(dashboardCore);
    }

    // Initialize cards UI
    cardsApi.initializeCards();

    // Initialize live updates stream (after cards are ready so worker filters are available)
    if (state.liveStreamingEnabled !== false) {
        updatesApi.setupLiveUpdates();
    }

    primeLiveViewEssentials();
    dashboardCore.events?.on?.(SPSA_SUMMARY_PROGRESS_EVENT, (payload?: unknown) => {
        const existing = coerceSpsaSummarySnapshot(state.spsaSummary);
        const merged = mergeSpsaSummarySnapshot(existing, payload);
        state.spsaSummary = merged;
        renderSpsaOverview();
    });

    async function initializeDashboardMode(): Promise<void> {
        if (!spsaFeatureAvailable || owner.ARENA_DISABLE_SPSA) {
            const current = getCurrentRuntimeMode();
            if (current === 'match' || current === 'sprt' || current === 'generate') {
                applyRuntimeMode(current);
                summaryApi.updateSummaryStats();
                return;
            }
            applyRuntimeMode('tournament');
            summaryApi.updateSummaryStats();
            return;
        }

        const startingMode = getCurrentRuntimeMode();
        if (startingMode === 'spsa') {
            applyRuntimeMode('spsa');
            await enableSpsaUI();
            return;
        }
        if (startingMode === 'match' || startingMode === 'sprt') {
            applyRuntimeMode(startingMode);
            summaryApi.updateSummaryStats();
            return;
        }
        if (startingMode === 'tournament') {
            applyRuntimeMode('tournament');
            summaryApi.updateSummaryStats();
            return;
        }

        applyRuntimeMode(startingMode);

        const API_BASE = getApiBase();
        const data = await requestJson<SpsaSummaryResponse>(`${API_BASE}/api/spsa/summary`);
        const hasOutcomeFields =
            typeof data?.wins === 'number' && typeof data.losses === 'number' && typeof data.draws === 'number';
        if (data?.mode === 'spsa' || hasOutcomeFields) {
            applyRuntimeMode('spsa');
            await enableSpsaUI();
            return;
        }

        throw crash('Failed to determine SPSA mode from /api/spsa/summary', new Error('Unexpected payload shape'));
    }

    initializeDashboardMode().catch((error) => {
        throw crash('Failed to initialize live dashboard mode', error);
    });

    function primeLiveViewEssentials(): void {
        if (typeof cardsApi.bootstrapVisibleWorkerCards === 'function') {
            void cardsApi.bootstrapVisibleWorkerCards();
        }
    }

    async function enableSpsaUI(): Promise<void> {
        applyRuntimeMode('spsa');
        if (state.spsaTimerId) {
            clearInterval(state.spsaTimerId);
            state.spsaTimerId = null;
        }
    }

    function renderSpsaOverview(): void {
        const div = document.getElementById('spsaOverview');
        if (!div) return;
        // Clear the overview div - navigation link is already in the header
        div.innerHTML = '';
    }

    initialized = true;
}
