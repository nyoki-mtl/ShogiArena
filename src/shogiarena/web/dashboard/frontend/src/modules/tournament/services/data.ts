import { requestJson } from '@/modules/shared/services/api';
import { crash, reportDashboardRecoverableFailure } from '@/modules/shared/utils/errors';
import { getLiveViewSnapshotStore } from '@/modules/shared';
import { startDiagnosticsStopwatch } from '@/modules/live/utils/liveNamespace';
import type { LiveViewSnapshot } from '@/modules/live/types';
import type { DashboardCore } from '@/types/dashboard';
import type { DashboardTabId, DashboardTabsApi } from '@/types/globals';
import type {
    NormalizedTournamentGame,
    NormalizedTournamentSummary,
    TournamentDashboardAPI,
    TournamentState,
    TournamentSummary,
} from '@/modules/tournament/types';
import { deriveSprtBanner, renderApiErrorBanner } from '@/modules/tournament/components/data';
import { normalizeTournamentSummary } from './normalizers';
import type { JsonObject, MutableJsonObject } from '@/types/shared';
import { summaryStore } from '@/store';

const DEFAULT_MAX_MOVES_TO_DRAW = 512;
const TOURNAMENT_STANDINGS_METRIC = 'tournament:hydration:standings';
const TOURNAMENT_PROGRESS_METRIC = 'tournament:hydration:progress';
const TOURNAMENT_SUMMARY_SSE_RECONNECT_MS = 3_000;
const TOURNAMENT_SUMMARY_FALLBACK_MS = 1_200;
const TOURNAMENT_SUMMARY_ACTIVE_TABS = new Set<DashboardTabId>(['tournament', 'openings', 'engines', 'rules', 'games']);

interface TournamentWindow extends Window {
    DashboardTournament?: TournamentDashboardAPI;
    DashboardTabs?: DashboardTabsApi;
    ARENA_SUMMARY?: TournamentSummary;
    ARENA_DASHBOARD_STOPPED?: boolean;
    notifyDashboardServerStopped?: () => void;
}

interface TournamentRuntimeState extends TournamentState {
    offlineNotified: boolean;
    pentaCache: Map<string, unknown>;
    matchupCache: Map<string, unknown>;
    gamesList: NormalizedTournamentGame[] | null;
    gamesListCache: NormalizedTournamentGame[] | null;
    openingStatsRendered: boolean;
    lastOpeningStatsCompletedGames: number | null;
}

const defaultWindow = window as TournamentWindow;

let installedTournamentData = false;

function ensureDependencies(owner: TournamentWindow): {
    dashboard: TournamentDashboardAPI;
    state: TournamentRuntimeState;
    getApiBase: () => string;
    core: DashboardCore | undefined;
} {
    const dashboard = owner.DashboardTournament;
    if (!dashboard || !dashboard.state) {
        throw new Error(
            'DashboardTournament.state is undefined. Ensure tournament state module loads before data module.',
        );
    }
    const state = dashboard.state as TournamentRuntimeState;
    if (typeof dashboard.getApiBase !== 'function') {
        throw new Error('Dashboard.getApiBase must be provided by the tournament state module.');
    }
    return {
        dashboard,
        state,
        getApiBase: dashboard.getApiBase.bind(dashboard),
        core: owner.DashboardCore,
    };
}

export function installTournamentData(owner: TournamentWindow = defaultWindow): TournamentDashboardAPI {
    if (installedTournamentData) {
        return owner.DashboardTournament as TournamentDashboardAPI;
    }

    const { dashboard, state, getApiBase, core } = ensureDependencies(owner);
    const doc = owner.document;
    const liveViewStore = core ? getLiveViewSnapshotStore(core) : null;

    const reportRecoverable = (scope: string, error: unknown, userMessage?: string): void => {
        reportDashboardRecoverableFailure(error, {
            scope: scope.startsWith('Tournament.') ? scope : `Tournament.${scope}`,
            userMessage,
        });
    };

    let tournamentSummaryStream: EventSource | null = null;
    let tournamentSummaryReconnectTimer: number | null = null;
    let tournamentSummaryFallbackTimer: number | null = null;
    let tournamentSummarySnapshotReceived = false;
    let summaryStreamActive = false;

    if (liveViewStore) {
        state.liveViewSnapshot = liveViewStore.getSnapshot();
        liveViewStore.subscribe((snapshot) => {
            state.liveViewSnapshot = snapshot;
            updateSummaryStats();
        });
    }

    async function fetchJson(url: string, metric?: string): Promise<JsonObject> {
        const attempt = metric ? startDiagnosticsStopwatch(metric) : null;
        try {
            const payload = await requestJson<JsonObject>(url);
            attempt?.succeed();
            return payload;
        } catch (error) {
            attempt?.fail();
            throw new Error(`Failed to fetch JSON response for ${url}`, {
                cause: error instanceof Error ? error : undefined,
            });
        }
    }

    const hasSummarySnapshot = (payload: MutableJsonObject): boolean =>
        Object.hasOwn(payload, 'standings') || Object.hasOwn(payload, 'enginesMeta');

    const clearSummaryFallback = (): void => {
        if (tournamentSummaryFallbackTimer !== null) {
            window.clearTimeout(tournamentSummaryFallbackTimer);
            tournamentSummaryFallbackTimer = null;
        }
    };

    const scheduleSummaryFallback = (reason: 'initial' | 'reconnect'): void => {
        if (tournamentSummaryFallbackTimer !== null) {
            return;
        }
        tournamentSummaryFallbackTimer = window.setTimeout(() => {
            tournamentSummaryFallbackTimer = null;
            if (tournamentSummarySnapshotReceived) {
                return;
            }
            void fetchTournamentSnapshot(reason);
        }, TOURNAMENT_SUMMARY_FALLBACK_MS);
    };

    const clearSummaryReconnect = (): void => {
        if (tournamentSummaryReconnectTimer !== null) {
            window.clearTimeout(tournamentSummaryReconnectTimer);
            tournamentSummaryReconnectTimer = null;
        }
    };

    const closeSummaryStream = (): void => {
        if (tournamentSummaryStream) {
            try {
                tournamentSummaryStream.close();
            } catch (error) {
                reportRecoverable('SummaryStream.Close', error, 'Failed to close tournament summary stream');
            }
        }
        tournamentSummaryStream = null;
    };

    const setSummaryStreamActive = (active: boolean): void => {
        if (summaryStreamActive === active) {
            return;
        }
        summaryStreamActive = active;
        if (summaryStreamActive) {
            openSummaryStream();
            if (!tournamentSummarySnapshotReceived) {
                scheduleSummaryFallback('initial');
            }
            return;
        }
        clearSummaryFallback();
        clearSummaryReconnect();
        closeSummaryStream();
    };

    const shouldStreamSummaryForTab = (tab: DashboardTabId | null): boolean => {
        if (!tab) return false;
        return TOURNAMENT_SUMMARY_ACTIVE_TABS.has(tab);
    };

    const fetchTournamentSnapshot = async (reason: 'initial' | 'reconnect'): Promise<void> => {
        try {
            const apiBase = getApiBase();
            const standingsData = await fetchJson(`${apiBase}/api/standings`, TOURNAMENT_STANDINGS_METRIC);
            const progressData = await fetchJson(`${apiBase}/api/progress`, TOURNAMENT_PROGRESS_METRIC);
            const base =
                owner.ARENA_SUMMARY && typeof owner.ARENA_SUMMARY === 'object'
                    ? { ...(owner.ARENA_SUMMARY as MutableJsonObject) }
                    : ({} as MutableJsonObject);
            const combined = {
                ...base,
                ...standingsData,
                ...progressData,
            } as TournamentSummary;
            onSummaryUpdate(combined);
        } catch (error) {
            showApiError('Failed to load tournament data', error);
            reportRecoverable(`Hydration.Rest.${reason}`, error, 'Failed to load tournament data');
        }
    };

    const openSummaryStream = (): void => {
        if (typeof EventSource !== 'function') {
            scheduleSummaryFallback('initial');
            return;
        }
        if (tournamentSummaryStream) {
            return;
        }
        const apiBase = getApiBase();
        const url = `${apiBase}/api/tournament/summary/stream?send_initial=true`;
        try {
            const source = new EventSource(url);
            tournamentSummaryStream = source;

            source.onmessage = (event) => {
                try {
                    const payload = JSON.parse(event.data) as { type?: string; data?: unknown };
                    if (payload.type === 'heartbeat') {
                        return;
                    }
                    if (!payload.data || typeof payload.data !== 'object') {
                        return;
                    }
                    onSummaryUpdate(payload.data);
                    if (payload.data && typeof payload.data === 'object') {
                        const snapshotFlag = hasSummarySnapshot(payload.data as MutableJsonObject);
                        if (snapshotFlag) {
                            tournamentSummarySnapshotReceived = true;
                            clearSummaryFallback();
                        }
                    }
                } catch (error) {
                    reportRecoverable(
                        'SummaryStream.Parse',
                        error,
                        'Failed to parse tournament summary stream payload',
                    );
                }
            };

            source.onerror = () => {
                closeSummaryStream();
                clearSummaryReconnect();
                tournamentSummaryReconnectTimer = window.setTimeout(() => {
                    tournamentSummaryReconnectTimer = null;
                    openSummaryStream();
                    scheduleSummaryFallback('reconnect');
                }, TOURNAMENT_SUMMARY_SSE_RECONNECT_MS);
            };
        } catch (error) {
            reportRecoverable('SummaryStream.Open', error, 'Failed to open tournament summary stream');
            scheduleSummaryFallback('initial');
        }
    };

    function showApiError(title: string, detail: unknown): void {
        const container = doc.querySelector('header') || doc.body;
        let banner = doc.getElementById('apiErrorBanner');
        if (!banner) {
            banner = doc.createElement('div');
            banner.id = 'apiErrorBanner';
            banner.className = 'api-error-banner';
            container.insertBefore(banner, container.firstChild || null);
        }

        const detailsString = typeof detail === 'string' ? detail : JSON.stringify(detail, null, 2);
        banner.innerHTML = renderApiErrorBanner({
            title,
            detail: detailsString,
            timestamp: new Date(),
        });
        crash(title, detail);
    }

    function requireNormalizedSummary(): NormalizedTournamentSummary {
        const normalized = dashboard.getNormalizedSummary?.() ?? state.normalizedSummary ?? null;
        if (!normalized) {
            throw new Error('Normalized tournament summary is unavailable');
        }
        return normalized;
    }

    function resolveLiveViewSnapshot(): LiveViewSnapshot | null {
        const snapshot = state.liveViewSnapshot;
        if (!snapshot) return null;
        return snapshot.mode === 'tournament' || snapshot.mode === 'unknown' ? snapshot : null;
    }

    function updateSummaryStats(): void {
        const normalized = requireNormalizedSummary();
        const liveView = resolveLiveViewSnapshot();
        const liveProgress =
            liveView?.progress && (liveView.mode === 'tournament' || liveView.progress?.kind === 'games')
                ? liveView.progress
                : null;
        const isFinal =
            typeof liveProgress?.isFinal === 'boolean' ? liveProgress.isFinal : normalized.tournamentFinished;
        const sprtConclusion = normalized.sprt?.decision ?? normalized.sprtConclusion ?? null;
        const isSprt = sprtConclusion !== null && sprtConclusion !== undefined;

        const sprtBanner = doc.getElementById('sprtBanner');
        if (sprtBanner) {
            const bannerModel = deriveSprtBanner({
                isSprt,
                isFinal,
                conclusion: sprtConclusion ?? undefined,
            });
            sprtBanner.toggleAttribute('hidden', bannerModel.hidden);
            if (!bannerModel.hidden && bannerModel.text) {
                sprtBanner.textContent = bannerModel.text;
            }
        }

        const sprtBox = doc.getElementById('sprtBox');
        if (sprtBox) {
            const profile = doc.body?.dataset?.dashboardProfile ?? '';
            if (profile === 'sprt') {
                sprtBox.textContent = '';
            } else {
                const sprt = normalized.sprt;
                if (sprt) {
                    const llr = Number(sprt.llr ?? 0).toFixed(3);
                    const lo = Number(sprt.lower ?? 0).toFixed(3);
                    const up = Number(sprt.upper ?? 0).toFixed(3);
                    const decision = sprt.decision ?? 'continue';
                    const games = Number(sprt.games ?? 0);
                    sprtBox.textContent = `SPRT: LLR=${llr} [${lo}, ${up}] decision=${decision} games=${games}`;
                } else {
                    sprtBox.textContent = '';
                }
            }
        }

        const pentaBox = doc.getElementById('pentaBox');
        if (pentaBox) pentaBox.textContent = '';

        const extraStats = doc.getElementById('extraStats');
        if (extraStats) {
            const sprtText = sprtBox?.textContent?.trim() ?? '';
            const pentaText = pentaBox?.textContent?.trim() ?? '';
            extraStats.hidden = !(sprtText || pentaText);
        }
    }

    function getFinalRatingsFromSummary(): Map<string, number> {
        const normalized = requireNormalizedSummary();
        const ratingInitial = normalized.ratingInitial;
        const out = new Map<string, number>();
        const btdRatings = normalized.btd?.ratings ?? null;
        if (btdRatings) {
            for (const [name, entry] of Object.entries(btdRatings)) {
                const mean = entry?.mean;
                if (typeof mean === 'number' && Number.isFinite(mean)) {
                    out.set(name, mean + ratingInitial);
                }
            }
            if (out.size) {
                return out;
            }
        }

        for (const engine of normalized.engines) {
            const stats = normalized.engineStats[engine];
            const ratingValue =
                stats && typeof stats.rating === 'number' && Number.isFinite(stats.rating)
                    ? Number(stats.rating)
                    : ratingInitial;
            out.set(engine, ratingValue);
        }

        if (!out.size) {
            normalized.engines.forEach((engine) => {
                out.set(engine, ratingInitial);
            });
        }

        return out;
    }

    function resolveMaxMovesToDraw(): number {
        const normalized = requireNormalizedSummary();
        const summary = normalized.raw as TournamentSummary;

        const candidateOrder: unknown[] = [
            summary?.maxMovesToDraw,
            summary?.rules?.maxMovesToDraw,
            summary?.rules?.adjudication?.maxMovesToDraw,
            summary?.tournamentConfig?.rules?.maxMovesToDraw,
        ];

        const normalizeCandidate = (value: unknown): number | null => {
            const num = Number(value);
            return Number.isFinite(num) && num > 0 ? num : null;
        };

        for (const candidate of candidateOrder) {
            const value = normalizeCandidate(candidate);
            if (value !== null) {
                return value;
            }
        }

        let bestFromMeta: number | null = null;
        for (const meta of Object.values(normalized.engineMeta)) {
            if (!meta || typeof meta !== 'object') {
                continue;
            }
            const resolved = (meta.resolved_options ?? meta.raw.resolved_options) as MutableJsonObject | undefined;
            const merged = (meta.merged_options ?? meta.raw.merged_options) as MutableJsonObject | undefined;
            const extra = meta.raw.extra_options as MutableJsonObject | undefined;
            const metaCandidates = [resolved?.MaxMovesToDraw, merged?.MaxMovesToDraw, extra?.MaxMovesToDraw];
            for (const candidate of metaCandidates) {
                const value = normalizeCandidate(candidate);
                if (value !== null && (bestFromMeta === null || value > bestFromMeta)) {
                    bestFromMeta = value;
                }
            }
        }

        return bestFromMeta ?? DEFAULT_MAX_MOVES_TO_DRAW;
    }

    function onSummaryUpdate(payload: unknown): void {
        if (!payload || typeof payload !== 'object') {
            reportRecoverable(
                'Live.Summary.PayloadType',
                new Error('Tournament summary update payload must be an object'),
                'Tournament summary update payload must be an object',
            );
            return;
        }

        const mutable = { ...(payload as MutableJsonObject) };
        if (Object.hasOwn(mutable, 'rating_series')) {
            delete mutable.rating_series;
        }
        if (hasSummarySnapshot(mutable)) {
            tournamentSummarySnapshotReceived = true;
            clearSummaryFallback();
        }

        // `summary:update` delivers partial diffs. Merge with the last known snapshot so
        // missing fields (e.g. engines list) don't get dropped on every update.
        const base =
            owner.ARENA_SUMMARY && typeof owner.ARENA_SUMMARY === 'object'
                ? ({ ...(owner.ARENA_SUMMARY as MutableJsonObject) } as TournamentSummary)
                : ({} as TournamentSummary);
        // `summary:update` is a shared cross-tab event. Non-tournament pages (e.g. SPSA/Convergence) may emit
        // summary payloads that do not match the tournament schema. Treat validation failures as recoverable and
        // keep the last known-good summary instead of crashing the whole dashboard.
        const nextSummary = { ...base, ...(mutable as TournamentSummary) } as TournamentSummary;
        try {
            state.normalizedSummary = normalizeTournamentSummary(nextSummary);
        } catch (error) {
            reportRecoverable('Live.Summary.Validation', error, 'Tournament summary validation failed');
            return;
        }
        owner.ARENA_SUMMARY = nextSummary;

        // Push to unified store for cross-module consistency
        // NOTE: We do NOT call setActiveSource here - that should only be called
        // when the tab becomes active, not on every data update.
        const normalized = state.normalizedSummary;
        summaryStore.applyTournamentSummary({
            engines: normalized.engines,
            engineMeta: normalized.engineMeta as unknown as JsonObject,
            engineTimeControls: normalized.engineTimeControls,
            engineInstances: normalized.engineInstances,
            defaultTimeControl: normalized.defaultTimeControl,
        });
        state.gamesList = null;
        state.gamesListCache = null;
        state.pentaCache = new Map();
        state.matchupCache = new Map();
        updateSummaryStats();
        if (typeof dashboard.updateStandings === 'function') {
            dashboard.updateStandings();
        }
        if (typeof dashboard.refreshMatchupInline === 'function') {
            dashboard.refreshMatchupInline();
        }
        const completedGames = state.normalizedSummary?.completedGames ?? null;
        const shouldUpdate =
            !state.openingStatsRendered ||
            (completedGames !== null && completedGames !== state.lastOpeningStatsCompletedGames);
        if (shouldUpdate && typeof dashboard.renderOpeningStats === 'function') {
            Promise.resolve(
                dashboard.renderOpeningStats({
                    force: state.openingStatsRendered,
                    completedGames,
                    trigger: 'openings:summary',
                }),
            ).catch((error) => reportRecoverable('Openings.Refresh.Summary', error, 'Failed to refresh opening stats'));
        }
        state.lastOpeningStatsCompletedGames = completedGames;
        state.openingStatsRendered ||= shouldUpdate;
    }

    function setupLiveUpdates(): void {
        if (!core?.events?.on) {
            return;
        }
        if (state.offlineNotified) {
            return;
        }
        // Live summary diffs are delivered by the shared Live Updates module (WS) as `summary:update`.
        core.events.on('summary:update', (payload?: unknown) => {
            try {
                const summary =
                    payload && typeof payload === 'object' ? (payload as { summary?: unknown }).summary : null;
                onSummaryUpdate(summary);
            } catch (error) {
                reportRecoverable('Live.Summary.PayloadParse', error, 'Failed to apply summary update payload');
            }
        });
    }

    async function initializeTournament(): Promise<void> {
        console.info('[Tournament] Initializing dashboard');
        setupLiveUpdates();

        if (owner.ARENA_SUMMARY && typeof owner.ARENA_SUMMARY === 'object') {
            try {
                onSummaryUpdate(owner.ARENA_SUMMARY);
            } catch (error) {
                showApiError('Tournament summary validation failed', error);
                reportRecoverable('Hydration.InitialSummaryValidation', error, 'Tournament summary validation failed');
                return;
            }
        }

        const initialTab = owner.DashboardTabs?.getActive?.() ?? null;
        if (initialTab) {
            setSummaryStreamActive(shouldStreamSummaryForTab(initialTab));
        } else {
            setSummaryStreamActive(true);
        }

        const normalizedSnapshot = state.normalizedSummary;
        const completedGames = normalizedSnapshot?.completedGames ?? null;

        if (typeof dashboard.renderOpeningStats === 'function') {
            try {
                await Promise.resolve(dashboard.renderOpeningStats({ completedGames, trigger: 'openings:bootstrap' }));
            } catch (error) {
                reportRecoverable('Openings.Render.Initial', error, 'Failed to render opening stats');
            }
        }

        console.info('[Tournament] Dashboard initialized');
    }

    Object.assign(dashboard, {
        DEFAULT_MAX_MOVES_TO_DRAW,
        showApiError,
        updateSummaryStats,
        getFinalRatingsFromSummary,
        resolveMaxMovesToDraw,
        onSummaryUpdate,
        setupLiveUpdates,
        initializeTournament,
        notifyTabChange: (tab: DashboardTabId) => {
            setSummaryStreamActive(shouldStreamSummaryForTab(tab));
        },
    });

    installedTournamentData = true;
    owner.DashboardTournament = dashboard;
    return dashboard;
}
