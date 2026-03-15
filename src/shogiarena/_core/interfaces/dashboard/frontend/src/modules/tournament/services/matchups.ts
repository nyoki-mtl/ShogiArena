import { requestJson } from '@/modules/shared/services/api';
import { createDeferredHydrator } from '@/modules/shared';
import type { HydrationTrigger } from '@/modules/shared/utils/hydration';
import { recordLiveDiagnosticsMetric, startDiagnosticsStopwatch } from '@/modules/live/utils/liveNamespace';
import type { DashboardCore } from '@/types/dashboard';
import type {
    NormalizedTournamentGame,
    NormalizedTournamentSummary,
    TournamentDashboardAPI,
    TournamentEngineStats,
    TournamentState,
} from '@/modules/tournament/types';
import { buildMatchupPanelHtml, type MatchupRowView } from '@/modules/tournament/components/matchups';
import { escapeHtml } from '@/modules/shared/utils/html';
import { buildMatchupRowViewModel } from '@/modules/tournament/viewmodels/matchups';
import * as sharedFormat from '@/modules/shared/viewmodels/format';
import { normalizeTournamentGamesList } from './normalizers';

interface MatchupEntry {
    opponent: string;
    wins: number;
    draws: number;
    losses: number;
    total: number;
    winRatio: number;
    delta: number | null;
    deltaStr: string;
    los: number | null;
    losStr: string;
}

interface MatchupTotals {
    wins: number;
    draws: number;
    losses: number;
    games: number;
}

interface MatchupResult {
    engine: string;
    entries: MatchupEntry[];
    totals: MatchupTotals;
}

const MATCHUPS_GAMES_TTL_MS = 90_000;
const MATCHUPS_GAMES_METRIC = 'tournament:hydration:matchups';

interface TournamentRuntimeState extends TournamentState {
    matchupCache: Map<string, MatchupResult>;
    gamesList: NormalizedTournamentGame[] | null;
    gamesListCache: NormalizedTournamentGame[] | null;
}

interface TournamentWindow extends Window {
    DashboardTournament?: TournamentDashboardAPI;
    DashboardCore?: DashboardCore;
}

const defaultWindow = window as TournamentWindow;

let installedTournamentMatchups = false;

function ensureDependencies(owner: TournamentWindow): {
    dashboard: TournamentDashboardAPI;
    state: TournamentRuntimeState;
    getApiBase: () => string;
    core: DashboardCore | undefined;
} {
    const dashboard = owner.DashboardTournament;
    if (!dashboard || !dashboard.state) {
        throw new Error('DashboardTournament.state is undefined. Ensure tournament state module loads first.');
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

function requireNormalizedSummary(dashboard: TournamentDashboardAPI): NormalizedTournamentSummary {
    if (typeof dashboard.getNormalizedSummary !== 'function') {
        throw new Error('Dashboard.getNormalizedSummary must be defined before matchups module loads.');
    }
    const snapshot = dashboard.getNormalizedSummary();
    if (!snapshot) {
        throw new Error('DashboardTournament.getNormalizedSummary returned null; tournament summary is unavailable');
    }
    return snapshot;
}

export function installTournamentMatchups(owner: TournamentWindow = defaultWindow): TournamentDashboardAPI {
    if (installedTournamentMatchups) {
        return owner.DashboardTournament as TournamentDashboardAPI;
    }

    const { dashboard, state, getApiBase, core } = ensureDependencies(owner);
    const doc = owner.document;

    void core;

    let gamesCacheExpiresAt = 0;
    const matchupRenderSignatures = new Map<string, string>();
    let gamesHydrationPromise: Promise<void> | null = null;

    const runGamesFetch = async (_trigger?: HydrationTrigger): Promise<void> => {
        const attempt = startDiagnosticsStopwatch(MATCHUPS_GAMES_METRIC);
        try {
            const data = await requestJson<{ games?: unknown[] }>(`${getApiBase()}/api/games`);
            const normalizedGames = normalizeTournamentGamesList(data.games ?? []);
            state.gamesList = normalizedGames;
            state.gamesListCache = normalizedGames;
            gamesCacheExpiresAt = Date.now() + MATCHUPS_GAMES_TTL_MS;
            attempt.succeed();
        } catch (error) {
            gamesCacheExpiresAt = 0;
            attempt.fail();
            throw error instanceof Error ? error : new Error(String(error));
        }
    };

    const gamesHydrator = createDeferredHydrator({
        hydrate: (trigger) => {
            const promise = runGamesFetch(trigger);
            gamesHydrationPromise = promise;
            return promise.finally(() => {
                if (gamesHydrationPromise === promise) {
                    gamesHydrationPromise = null;
                }
            });
        },
        onError: (error, trigger) => {
            const cause = error instanceof Error ? error : new Error(String(error));
            throw new Error(`Failed to hydrate games list (${trigger ?? 'matchups'})`, { cause });
        },
    });

    const NORMAL_DISTRIBUTION_DENOMINATOR = Math.sqrt(2 * Math.PI);

    async function waitForGamesHydration(): Promise<void> {
        if (gamesHydrationPromise) {
            await gamesHydrationPromise;
            return;
        }
        await new Promise((resolve) => {
            window.setTimeout(resolve, 0);
        });
        if (gamesHydrationPromise) {
            await gamesHydrationPromise;
        }
    }

    function isGamesCacheFresh(): boolean {
        if (!gamesCacheExpiresAt) {
            return false;
        }
        const hasList = Array.isArray(state.gamesList) && state.gamesList.length > 0;
        const hasCache = Array.isArray(state.gamesListCache) && state.gamesListCache.length > 0;
        if (!hasList && !hasCache) {
            return false;
        }
        return Date.now() < gamesCacheExpiresAt;
    }

    async function ensureGamesList(trigger: HydrationTrigger): Promise<void> {
        if (isGamesCacheFresh()) {
            if (!state.gamesList || !state.gamesList.length) {
                state.gamesList = state.gamesListCache;
            }
            recordLiveDiagnosticsMetric(MATCHUPS_GAMES_METRIC, { cacheHit: 1 });
            return;
        }
        gamesHydrator.markDirty();
        gamesHydrator.ensure(trigger);
        await waitForGamesHydration();
    }

    function normalCdf(z: number): number {
        if (Number.isNaN(z)) return 0.5;
        if (typeof Math.erf === 'function') {
            return 0.5 * (1 + Math.erf(z / Math.SQRT2));
        }
        const sign = z >= 0 ? 1 : -1;
        const x = Math.abs(z) / Math.SQRT2;
        const a1 = 0.254_829_592;
        const a2 = -0.284_496_736;
        const a3 = 1.421_413_741;
        const a4 = -1.453_152_027;
        const a5 = 1.061_405_429;
        const p = 0.327_591_1;
        const t = 1 / (1 + p * x);
        const y = 1 - ((((a5 * t + a4) * t + a3) * t + a2) * t + a1) * t * Math.exp(-x * x);
        return 0.5 * (1 + sign * y);
    }

    function formatRecordTriplet(stats: TournamentEngineStats | null | undefined): string {
        if (!stats) return '0-0-0';
        const wins = Number(stats.wins ?? 0);
        const draws = Number(stats.draws ?? 0);
        const losses = Number(stats.losses ?? 0);
        return `${wins}-${draws}-${losses}`;
    }

    function gaussianDensity(x: number, mean: number, stdev: number): number {
        if (!Number.isFinite(mean) || !Number.isFinite(stdev) || stdev <= 0) return 0;
        const z = (x - mean) / stdev;
        const coeff = 1 / (stdev * NORMAL_DISTRIBUTION_DENOMINATOR);
        return coeff * Math.exp(-0.5 * z * z);
    }

    function getMatchupData(engineName: string): MatchupResult {
        const cached = state.matchupCache.get(engineName);
        if (cached) {
            return cached;
        }

        const normalizedSummary = requireNormalizedSummary(dashboard);
        const entries: MatchupEntry[] = [];
        const totals: MatchupTotals = { wins: 0, draws: 0, losses: 0, games: 0 };

        const normalizedPairs = normalizedSummary.pairResults?.[engineName];
        if (normalizedPairs && Object.keys(normalizedPairs).length) {
            for (const [target, record] of Object.entries(normalizedPairs)) {
                const wins = Math.max(0, Number(record.wins ?? 0));
                const losses = Math.max(0, Number(record.losses ?? 0));
                const draws = Math.max(0, Number(record.draws ?? 0));
                const total = wins + losses + draws;
                totals.wins += wins;
                totals.losses += losses;
                totals.draws += draws;
                totals.games += total;

                const winRatio = total > 0 ? (wins + draws * 0.5) / total : 0;
                const delta = computeRatingDelta(record);
                const los = computeLOSFromNormalized(record);
                entries.push({
                    opponent: target,
                    wins,
                    draws,
                    losses,
                    total,
                    winRatio,
                    delta: delta.value,
                    deltaStr: delta.text,
                    los: los.value,
                    losStr: los.text,
                });
            }
        }

        entries.sort((a, b) => b.total - a.total || a.opponent.localeCompare(b.opponent));

        const result: MatchupResult = { engine: engineName, entries, totals };
        state.matchupCache.set(engineName, result);
        return result;
    }

    function computeRatingDelta(payload: { wins?: number; losses?: number; draws?: number }): {
        value: number | null;
        text: string;
    } {
        const wins = Math.max(0, Number(payload.wins ?? 0));
        const losses = Math.max(0, Number(payload.losses ?? 0));
        const draws = Math.max(0, Number(payload.draws ?? 0));
        const total = wins + losses + draws;
        if (!Number.isFinite(total) || total <= 0) {
            return { value: null, text: '-' };
        }
        const score = wins + 0.5 * draws;
        const proportion = score / total;
        const epsilon = 1e-6;
        const clamped = Math.min(1 - epsilon, Math.max(epsilon, proportion));
        const delta = 400 * Math.log10(clamped / (1 - clamped));
        const bounded = Math.max(-999, Math.min(999, delta));
        const rounded = Math.round(bounded);
        if (!Number.isFinite(rounded)) {
            return { value: null, text: '-' };
        }
        if (rounded === 0) {
            return { value: 0, text: '±0' };
        }
        return { value: rounded, text: `${rounded > 0 ? '+' : ''}${rounded}` };
    }

    function computeLOSFromNormalized(payload: { wins: number; losses: number; draws: number }): {
        value: number | null;
        text: string;
    } {
        const wins = Math.max(0, Number(payload.wins ?? 0));
        const losses = Math.max(0, Number(payload.losses ?? 0));
        const draws = Math.max(0, Number(payload.draws ?? 0));
        const total = wins + losses + draws;
        if (!Number.isFinite(total) || total <= 0) {
            return { value: null, text: '-' };
        }
        const mean = wins + 0.5 * draws;
        const variance = total / 4;
        const stdev = Math.sqrt(variance);
        if (stdev <= 0) return { value: null, text: '-' };
        const z = (mean - total / 2) / stdev;
        const los = normalCdf(z);
        return { value: los, text: `${(los * 100).toFixed(1)}%` };
    }

    function renderMatchupInline(
        engineName: string,
        host: Element | null,
        options?: { preferredOpponent?: string },
    ): void {
        if (!host) return;
        host.setAttribute('data-matchup-engine', engineName);
        let panel = host.querySelector<HTMLElement>(`.matchup-panel[data-engine="${CSS.escape(engineName)}"]`);
        if (!panel) {
            host.innerHTML = buildMatchupPanelHtml(engineName);
            panel = host.querySelector('.matchup-panel');
            if (!panel) return;
            hydrateMatchupPanel(panel, engineName, options);
        } else {
            hydrateMatchupPanel(panel, engineName, options);
        }
    }

    function hydrateMatchupPanel(
        panelRoot: Element | null,
        engineName: string,
        options?: { preferredOpponent?: string },
    ): void {
        if (!panelRoot) return;
        const data = getMatchupData(engineName);
        const { wins, draws, losses, games } = data.totals;
        const entriesSig = data.entries
            .map((entry) => {
                const opponent = String(entry.opponent ?? '');
                const delta = entry.delta ?? '';
                const los = entry.los ?? '';
                return `${opponent}|${entry.wins}|${entry.draws}|${entry.losses}|${entry.winRatio}|${delta}|${los}`;
            })
            .join('~');
        const summarySig = `${games}|${wins}|${draws}|${losses}||${entriesSig}`;
        const previousSig = matchupRenderSignatures.get(engineName);
        const hasChanges = summarySig !== previousSig;
        if (hasChanges) {
            matchupRenderSignatures.set(engineName, summarySig);
            const overview = panelRoot.querySelector<HTMLElement>('.matchup-overview');
            if (overview) {
                overview.textContent = `Total ${games} games · ${wins}-${draws}-${losses}`;
            }
        }

        const tbody = panelRoot.querySelector<HTMLTableSectionElement>('tbody');
        if (tbody && hasChanges) {
            const rows: MatchupRowView[] = data.entries.map((entry) =>
                buildMatchupRowViewModel({
                    opponent: entry.opponent,
                    wins: entry.wins,
                    draws: entry.draws,
                    losses: entry.losses,
                    win_ratio: entry.winRatio,
                    delta: entry.delta,
                    los: entry.los,
                }),
            );
            updateMatchupRows(tbody, rows);
        }

        const preferred = options?.preferredOpponent ?? null;
        highlightOpponent(panelRoot, preferred);
    }

    function highlightOpponent(panelRoot: Element, opponent: string | null): void {
        panelRoot.querySelectorAll('.matchup-table tr').forEach((row) => {
            if (!opponent) {
                row.classList.remove('is-selected');
                return;
            }
            const encoded = encodeURIComponent(opponent);
            row.classList.toggle('is-selected', row.getAttribute('data-opponent') === encoded);
        });
    }

    function updateMatchupRows(tbody: HTMLTableSectionElement, rows: MatchupRowView[]): void {
        const existing = Array.from(tbody.querySelectorAll<HTMLTableRowElement>('tr[data-opponent]'));
        const rowMap = new Map<string, HTMLTableRowElement>();
        existing.forEach((row) => {
            const key = row.getAttribute('data-opponent');
            if (key) rowMap.set(key, row);
        });

        let cursor = tbody.querySelector<HTMLTableRowElement>('tr[data-opponent]');
        const seen = new Set<string>();

        rows.forEach((entry) => {
            const opponentRaw = String(entry.opponent ?? '');
            const opponentEncoded = encodeURIComponent(opponentRaw);
            let row = rowMap.get(opponentEncoded);
            if (!row) {
                row = buildMatchupRowElement(entry);
                rowMap.set(opponentEncoded, row);
            } else {
                refreshMatchupRow(row, entry);
            }

            if (row !== cursor) {
                tbody.insertBefore(row, cursor);
            }
            cursor = row.nextElementSibling as HTMLTableRowElement | null;
            seen.add(opponentEncoded);
        });

        Array.from(tbody.querySelectorAll<HTMLTableRowElement>('tr[data-opponent]')).forEach((row) => {
            const key = row.getAttribute('data-opponent');
            if (key && !seen.has(key)) {
                row.remove();
            }
        });
    }

    function buildMatchupRowElement(entry: MatchupRowView): HTMLTableRowElement {
        const opponentRaw = String(entry.opponent ?? '');
        const opponentEncoded = encodeURIComponent(opponentRaw);
        const opponentEscaped = escapeHtml(opponentRaw);
        const row = doc.createElement('tr');
        row.setAttribute('data-opponent', opponentEncoded);
        row.innerHTML =
            `<td>` +
            `<button class="matchup-opponent" type="button" data-action="matchup_select" data-engine="${escapeHtml(
                opponentRaw,
            )}" data-opponent="${opponentEncoded}">` +
            `${opponentEscaped}` +
            `</button>` +
            `</td>` +
            `<td>${escapeHtml(`${entry.wins}-${entry.draws}-${entry.losses}`)}</td>` +
            `<td>${escapeHtml(entry.winRatio)}</td>` +
            `<td>${escapeHtml(entry.delta)}</td>` +
            `<td>${escapeHtml(entry.los)}</td>`;
        return row;
    }

    function refreshMatchupRow(row: HTMLTableRowElement, entry: MatchupRowView): void {
        const cells = row.querySelectorAll<HTMLTableCellElement>('td');
        if (cells.length < 5) {
            return;
        }
        const opponentRaw = String(entry.opponent ?? '');
        const opponentEncoded = encodeURIComponent(opponentRaw);
        row.setAttribute('data-opponent', opponentEncoded);
        const button = cells[0].querySelector<HTMLButtonElement>('button.matchup-opponent');
        if (button) {
            button.textContent = opponentRaw;
            button.setAttribute('data-engine', opponentRaw);
            button.setAttribute('data-opponent', opponentEncoded);
        }
        cells[1].textContent = `${entry.wins}-${entry.draws}-${entry.losses}`;
        cells[2].textContent = String(entry.winRatio ?? '');
        cells[3].textContent = String(entry.delta ?? '');
        cells[4].textContent = String(entry.los ?? '');
    }

    async function focusMatchupOpponent(
        engineName: string,
        host: Element | null,
        opponent: string | null,
    ): Promise<void> {
        if (!host) return;
        const panel = host.querySelector('.matchup-panel');
        if (!panel) {
            renderMatchupInline(engineName, host, opponent ? { preferredOpponent: opponent } : undefined);
            return;
        }
        hydrateMatchupPanel(panel, engineName, opponent ? { preferredOpponent: opponent } : undefined);
    }

    function refreshMatchupInline(): void {
        doc.querySelectorAll<HTMLElement>('[data-matchup-engine]').forEach((host) => {
            const engineName = host.getAttribute('data-matchup-engine');
            if (!engineName) return;
            const panel = host.querySelector<HTMLElement>(`.matchup-panel[data-engine="${CSS.escape(engineName)}"]`);
            if (!panel) return;
            const preferredOpponent =
                state.expanded && state.expanded.mode === 'matchup' && state.expanded.name === engineName
                    ? (state.expanded.opponent ?? null)
                    : null;
            hydrateMatchupPanel(panel, engineName, preferredOpponent ? { preferredOpponent } : undefined);
        });
    }

    async function fetchGamesList(trigger: HydrationTrigger = 'matchups:manual'): Promise<NormalizedTournamentGame[]> {
        await ensureGamesList(trigger);
        if (Array.isArray(state.gamesList) && state.gamesList.length) {
            return state.gamesList;
        }
        if (Array.isArray(state.gamesListCache) && state.gamesListCache.length) {
            return state.gamesListCache;
        }
        return [];
    }

    Object.assign(dashboard, {
        normalCdf,
        formatWinRatio: (value: number | null | undefined, digits = 3) => sharedFormat.formatWinRatio(value, digits),
        formatRecordTriplet,
        gaussianDensity,
        renderMatchupInline,
        hydrateMatchupPanel,
        focusMatchupOpponent,
        refreshMatchupInline,
        fetchGamesList,
    });

    installedTournamentMatchups = true;
    owner.DashboardTournament = dashboard;
    return dashboard;
}
