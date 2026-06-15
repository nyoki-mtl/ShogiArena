import type { ShogiBoardAdapter } from '@/modules/shared/components/shogi-board';
import { requestJson } from '@/modules/shared/services/api';
import { reportDashboardRecoverableFailure } from '@/modules/shared/utils/errors';
import { getLiveViewSnapshotStore } from '@/modules/shared';
import type { GamesScheduleSnapshot } from '@/modules/games/types';
import type {
    NormalizedTournamentGame,
    NormalizedTournamentSummary,
    TournamentState,
    TournamentDashboardAPI,
} from '@/modules/tournament/types';
import {
    renderOpeningEngineStatsPane,
    renderOpeningPlyStatsPane,
    renderOpeningStatsTableMarkup,
} from '@/modules/tournament/components/openings';
import { computeOpeningRows, type OpeningRow } from './opening-stats';
import { normalizeTournamentGame } from './normalizers';
import { recordLiveDiagnosticsMetric, startDiagnosticsStopwatch } from '@/modules/live/utils/liveNamespace';
import type { HydrationTrigger } from '@/modules/shared/utils/hydration';
import type { LiveViewSnapshot } from '@/modules/live/types';
import type { DashboardCore } from '@/types/dashboard';

type OpeningDetailMode = 'engine-stats' | 'ply-stats' | 'preview' | null;

interface RenderOpeningStatsOptions {
    force?: boolean;
    completedGames?: number | null;
    trigger?: HydrationTrigger;
}

const OPENING_STATS_METRIC = 'tournament:hydration:openingStats';
const OPENING_STATS_DEFAULT_TRIGGER: HydrationTrigger = 'openings:manual';

type OpeningDashboardState = TournamentState & {
    openingStatsRendered: boolean;
    openingStatsData: OpeningRow[] | null;
    lastOpeningStatsCompletedGames: number | null;
    selectedOpeningKey: string | null;
    openingSelectedRow: HTMLTableRowElement | null;
    openingDetailRow: HTMLTableRowElement | null;
    openingDetailMode: OpeningDetailMode;
    openingBoardAdapter: ShogiBoardAdapter | null;
    openingBoardElement: HTMLElement | null;
    openingBoardInfo: unknown;
    openingBoardObserver: ResizeObserver | null;
    openingRowLookup: Map<string, OpeningRow> | null;
    pendingOpeningFocus: string | null;
};

type DashboardModule = Partial<TournamentDashboardAPI> & { state: TournamentState };

type ArenaWindow = Window & {
    DashboardTournament?: DashboardModule;
    ShogiBoardAdapter?: new () => ShogiBoardAdapter;
    ResizeObserver?: typeof ResizeObserver;
    DashboardCore?: DashboardCore;
};

const defaultWindow = window as ArenaWindow;

let installedTournamentOpenings = false;

export function installTournamentOpenings(owner: ArenaWindow = defaultWindow): TournamentDashboardAPI {
    if (installedTournamentOpenings) {
        return owner.DashboardTournament as unknown as TournamentDashboardAPI;
    }

    const arenaWindow = owner;
    const { document } = owner;

    // Builds the openings stats view (tables + mini charts). Shares the central Dashboard
    // state so cached matchup data and API helpers stay consistent across tabs.

    const existingTournament = arenaWindow.DashboardTournament;
    if (!existingTournament) {
        throw new Error('DashboardTournament must be initialized before openings module loads.');
    }
    const Dashboard = existingTournament as DashboardModule;
    if (!Dashboard.state) {
        throw new Error(
            'DashboardTournament.state is undefined. Ensure tournament state module loads before openings module.',
        );
    }
    const state = Dashboard.state as OpeningDashboardState;

    state.openingBoardObserver ??= null;
    state.openingRowLookup ??= null;
    state.pendingOpeningFocus ??= null;

    const core = arenaWindow.DashboardCore;
    const liveViewStore = core ? getLiveViewSnapshotStore(core) : null;
    if (liveViewStore) {
        state.liveViewSnapshot = liveViewStore.getSnapshot();
        handleLiveViewSnapshot(state.liveViewSnapshot ?? null);
        liveViewStore.subscribe((snapshot) => {
            state.liveViewSnapshot = snapshot;
            handleLiveViewSnapshot(snapshot);
        });
    }

    if (!Dashboard.escapeHtml) {
        throw new Error('Dashboard.escapeHtml is undefined. Ensure shared helpers load before openings module.');
    }
    if (!Dashboard.formatDuration) {
        throw new Error('Dashboard.formatDuration is undefined.');
    }
    if (!Dashboard.formatNodesCountDetail) {
        throw new Error('Dashboard.formatNodesCountDetail is undefined.');
    }
    if (!Dashboard.hashString) {
        throw new Error('Dashboard.hashString is undefined.');
    }
    if (!Dashboard.formatWinRatio) {
        throw new Error('Dashboard.formatWinRatio is undefined. Ensure matchup module loads first.');
    }
    if (!Dashboard.formatRecordTriplet) {
        throw new Error('Dashboard.formatRecordTriplet is undefined.');
    }
    if (!Dashboard.gaussianDensity) {
        throw new Error('Dashboard.gaussianDensity is undefined.');
    }
    if (!Dashboard.resolveMaxMovesToDraw) {
        throw new Error('Dashboard.resolveMaxMovesToDraw is undefined.');
    }
    if (typeof Dashboard.getApiBase !== 'function') {
        throw new Error('Dashboard.getApiBase is undefined.');
    }

    const escapeHtml = Dashboard.escapeHtml;
    const hashString = Dashboard.hashString;
    const formatWinRatio = Dashboard.formatWinRatio;
    const formatRecordTriplet = Dashboard.formatRecordTriplet;
    const gaussianDensity = Dashboard.gaussianDensity;
    const resolveMaxMovesToDraw = Dashboard.resolveMaxMovesToDraw;
    const DEFAULT_MAX_MOVES_TO_DRAW = Dashboard.DEFAULT_MAX_MOVES_TO_DRAW ?? 512;
    const getApiBase = Dashboard.getApiBase.bind(Dashboard);
    const reportRecoverable = (scope: string, error: unknown, userMessage?: string): void => {
        reportDashboardRecoverableFailure(error, {
            scope: scope.startsWith('Tournament.Openings.') ? scope : `Tournament.Openings.${scope}`,
            userMessage,
        });
    };

    const getSummarySnapshot = (): NormalizedTournamentSummary | null => {
        if (typeof Dashboard.getNormalizedSummary !== 'function') {
            return null;
        }
        return Dashboard.getNormalizedSummary() ?? null;
    };

    async function fetchScheduleSnapshotGames(): Promise<NormalizedTournamentGame[]> {
        try {
            const snapshot = await requestJson<GamesScheduleSnapshot>(`${getApiBase()}/api/schedule`);
            const rows = Array.isArray(snapshot?.schedule) ? snapshot.schedule : [];
            const normalized: NormalizedTournamentGame[] = [];
            rows.forEach((entry) => {
                if (!entry) return;
                const normalizedGame = normalizeTournamentGame({
                    game_id: entry.game_id ?? null,
                    initial_sfen: entry.initial_sfen ?? null,
                    black_player: entry.black ?? null,
                    white_player: entry.white ?? null,
                    game_result: entry.game_result ?? null,
                    total_plies: entry.total_plies ?? null,
                    start_time: entry.start_time ?? null,
                    end_time: entry.end_time ?? null,
                });
                if (normalizedGame) {
                    normalized.push(normalizedGame);
                }
            });
            return normalized;
        } catch (error) {
            reportRecoverable('Schedule.SnapshotFetch', error, 'Failed to fetch schedule snapshot for openings stats');
            return [];
        }
    }

    async function fetchCompletedGamesList(trigger: HydrationTrigger): Promise<NormalizedTournamentGame[]> {
        if (typeof Dashboard.fetchGamesList !== 'function') {
            reportRecoverable('Schedule.CompletedGamesUnavailable', trigger, 'Completed games list API unavailable');
            return [];
        }
        try {
            const result = await Promise.resolve(Dashboard.fetchGamesList(trigger));
            return Array.isArray(result) ? result : [];
        } catch (error) {
            reportRecoverable('Schedule.CompletedGamesFetch', error, 'Failed to fetch completed games list');
            return [];
        }
    }

    function mergeGameLists(
        scheduleGames: readonly NormalizedTournamentGame[],
        completedGames: readonly NormalizedTournamentGame[],
    ): NormalizedTournamentGame[] {
        const merged = new Map<string, NormalizedTournamentGame>();
        let fallbackCounter = 0;

        const deriveKey = (game: NormalizedTournamentGame): string => {
            const trimmedId = typeof game.gameId === 'string' ? game.gameId.trim() : '';
            if (trimmedId) {
                return trimmedId;
            }
            fallbackCounter += 1;
            const black = game.players.black?.name ?? '';
            const white = game.players.white?.name ?? '';
            const start = game.startTime ?? '';
            return `${game.initialSfen}:${black}:${white}:${start}:${fallbackCounter}`;
        };

        const addGame = (game: NormalizedTournamentGame | null | undefined, overwrite: boolean): void => {
            if (!game) return;
            const key = deriveKey(game);
            if (!merged.has(key) || overwrite) {
                merged.set(key, game);
            }
        };

        scheduleGames.forEach((game) => {
            addGame(game, false);
        });
        completedGames.forEach((game) => {
            addGame(game, true);
        });

        return Array.from(merged.values());
    }

    function extractCompletedGames(snapshot: LiveViewSnapshot | null): number | null {
        const completed = snapshot?.progress?.completed;
        if (typeof completed === 'number' && Number.isFinite(completed)) {
            return completed;
        }
        return null;
    }

    function markOpeningStatsDirty(): void {
        state.openingStatsRendered = false;
        state.openingStatsData = null;
    }

    function handleLiveViewSnapshot(snapshot: LiveViewSnapshot | null): void {
        if (!snapshot) {
            return;
        }
        const completedGames = extractCompletedGames(snapshot);
        const progressChanged =
            completedGames !== null && completedGames !== state.lastOpeningStatsCompletedGames && completedGames >= 0;
        if (!progressChanged) {
            return;
        }
        markOpeningStatsDirty();
        const effectiveCompleted = completedGames ?? state.lastOpeningStatsCompletedGames ?? null;
        if (isOpeningsActive()) {
            Promise.resolve(
                renderOpeningStats({ force: true, completedGames: effectiveCompleted, trigger: 'openings:liveView' }),
            ).catch((error) => {
                reportRecoverable('LiveView.Refresh', error, 'Failed to refresh opening stats after LiveView update');
            });
        }
    }

    async function loadOpeningRows(trigger: HydrationTrigger): Promise<OpeningRow[]> {
        const [scheduleGames, completedGames] = await Promise.all([
            fetchScheduleSnapshotGames(),
            fetchCompletedGamesList(trigger),
        ]);

        if (!scheduleGames.length && !completedGames.length) {
            return [];
        }

        const mergedGames = mergeGameLists(scheduleGames, completedGames);
        if (!mergedGames.length) {
            return [];
        }

        recordLiveDiagnosticsMetric(OPENING_STATS_METRIC, {
            scheduleRows: scheduleGames.length,
            completedRows: completedGames.length,
        });

        return computeOpeningRows(mergedGames);
    }

    async function renderOpeningStats(options: RenderOpeningStatsOptions = {}): Promise<void> {
        const { force = false, completedGames = null } = options;
        const trigger = options.trigger ?? OPENING_STATS_DEFAULT_TRIGGER;
        const container = document.getElementById('openingStatsTable');
        if (!container) return;
        if (state.openingStatsRendered && !force) {
            recordLiveDiagnosticsMetric(OPENING_STATS_METRIC, { cacheHit: 1 });
            renderOpeningStatsTable();
            return;
        }

        const hadRows = Array.isArray(state.openingStatsData) && state.openingStatsData.length > 0;
        if (!state.openingStatsRendered) {
            container.innerHTML = '<div class="muted">Loading opening statistics...</div>';
        } else if (hadRows) {
            container.setAttribute('data-refreshing', '1');
        }

        const attempt = startDiagnosticsStopwatch(OPENING_STATS_METRIC);
        try {
            const rows = await loadOpeningRows(trigger);
            attempt.succeed();
            const hasRowsNow = rows.length > 0;
            recordLiveDiagnosticsMetric(OPENING_STATS_METRIC, hasRowsNow ? { hydrated: 1 } : { empty: 1 });
            if (!hasRowsNow) {
                state.openingStatsData = [];
                state.openingStatsRendered = true;
                if (completedGames !== null) state.lastOpeningStatsCompletedGames = completedGames;
                removeOpeningDetailRow();
                clearOpeningSelection();
                state.selectedOpeningKey = null;
                container.innerHTML = '<div class="muted">No game data available yet.</div>';
                return;
            }
            state.openingStatsData = rows;
            state.openingStatsRendered = true;
            if (completedGames !== null) {
                state.lastOpeningStatsCompletedGames = completedGames;
            }
            renderOpeningStatsTable();
        } catch (error) {
            attempt.fail();
            if (!hadRows) {
                container.innerHTML = '<div class="muted">Failed to load opening statistics.</div>';
            }
            reportRecoverable('Render.Load', error, 'Failed to gather opening stats');
        } finally {
            container.removeAttribute('data-refreshing');
        }
    }

    function renderOpeningStatsTable(): void {
        const container = document.getElementById('openingStatsTable');
        if (!container) return;
        const data: OpeningRow[] = state.openingStatsData ?? [];
        if (!data.length) {
            container.dataset.owner = '';
            removeOpeningDetailRow();
            clearOpeningSelection();
            state.selectedOpeningKey = null;
            state.openingRowLookup = null;
            container.innerHTML = '<div class="muted">No opening statistics available.</div>';
            return;
        }
        // Mark this table as managed by the tournament openings renderer so
        // other modules do not overwrite the per-row styling when they refresh
        // summary data in the background.
        container.dataset.owner = 'tournament-openings';

        const sortState = (state.openingStatsSort ?? { key: null, direction: null }) as {
            key: string | null;
            direction: string | null;
        };
        const sortKey = sortState.key;
        const sortDir = sortState.direction;
        const sorted = data.slice();
        const applyDefaultSort = () => sorted.sort((a, b) => b.total - a.total || a.sfen.localeCompare(b.sfen));

        if (sortKey && sortDir) {
            const factor = sortDir === 'asc' ? 1 : -1;
            switch (sortKey) {
                case 'opening':
                    sorted.sort((a, b) => factor * a.sfen.localeCompare(b.sfen));
                    break;
                case 'games':
                    sorted.sort((a, b) => factor * (a.total - b.total));
                    break;
                case 'wins':
                    sorted.sort((a, b) => factor * (a.black - b.black));
                    break;
                case 'draws':
                    sorted.sort((a, b) => factor * (a.draw - b.draw));
                    break;
                case 'losses':
                case 'whiteWins':
                    sorted.sort((a, b) => factor * (a.white - b.white));
                    break;
                case 'blackWin':
                    sorted.sort((a, b) => {
                        const aScore = Number.isFinite(a.blackRatio) ? a.blackRatio : -Infinity;
                        const bScore = Number.isFinite(b.blackRatio) ? b.blackRatio : -Infinity;
                        return factor * (aScore - bScore);
                    });
                    break;
                case 'whiteWin':
                    sorted.sort((a, b) => {
                        const aScore = Number.isFinite(a.whiteRatio) ? a.whiteRatio : -Infinity;
                        const bScore = Number.isFinite(b.whiteRatio) ? b.whiteRatio : -Infinity;
                        return factor * (aScore - bScore);
                    });
                    break;
                case 'plies':
                    sorted.sort((a, b) => {
                        const aPlies =
                            typeof a.avgPlies === 'number' && Number.isFinite(a.avgPlies) ? a.avgPlies : -Infinity;
                        const bPlies =
                            typeof b.avgPlies === 'number' && Number.isFinite(b.avgPlies) ? b.avgPlies : -Infinity;
                        return factor * (aPlies - bPlies);
                    });
                    break;
                default:
                    applyDefaultSort();
                    break;
            }
        } else {
            applyDefaultSort();
        }

        const wantsFullList = Boolean(state.pendingOpeningFocus || state.selectedOpeningKey);
        const { html, limitedRows } = renderOpeningStatsTableMarkup({
            rows: sorted,
            sortState,
            escapeHtml,
            limit: wantsFullList ? undefined : 50,
        });

        container.innerHTML = html;

        const limited = limitedRows as unknown as OpeningRow[];

        const rowLookup = new Map<string, OpeningRow>();
        const tableRows = container.querySelectorAll<HTMLTableRowElement>('tbody tr');
        tableRows.forEach((tr, idx) => {
            const rowData = limited[idx];
            if (!rowData) return;
            rowData.rowElement = tr;
            rowLookup.set(rowData.sfen, rowData);
            const cell = tr.querySelector<HTMLElement>('.opening-name');
            if (cell) {
                cell.style.cursor = 'pointer';
                cell.title = 'Preview position';
                cell.addEventListener('click', () => {
                    showOpeningPreview(rowData);
                });
            }
            const actionCells = tr.querySelectorAll<HTMLElement>('[data-action]');
            actionCells.forEach((actionCell) => {
                const action = actionCell.getAttribute('data-action');
                if (!action) return;
                actionCell.style.cursor = 'pointer';
                actionCell.addEventListener('click', (event) => {
                    event.preventDefault();
                    event.stopPropagation();
                    if (action === 'opening-engine-stats') {
                        showOpeningEngineStats(rowData);
                    } else if (action === 'opening-ply-stats') {
                        showOpeningPlyStats(rowData);
                    }
                });
            });
        });

        state.openingRowLookup = rowLookup;

        const pendingKey = state.pendingOpeningFocus;
        if (pendingKey && rowLookup.has(pendingKey)) {
            state.pendingOpeningFocus = null;
            const pendingRow = rowLookup.get(pendingKey);
            if (pendingRow) {
                showOpeningPreview(pendingRow);
                const rowEl = pendingRow.rowElement;
                if (rowEl && typeof rowEl.scrollIntoView === 'function') {
                    try {
                        rowEl.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
                    } catch (_error) {
                        rowEl.scrollIntoView();
                    }
                }
            }
        } else if (state.selectedOpeningKey && rowLookup.has(state.selectedOpeningKey)) {
            const selectedRow = rowLookup.get(state.selectedOpeningKey);
            if (state.openingDetailMode === 'engine-stats') {
                showOpeningEngineStats(selectedRow);
            } else if (state.openingDetailMode === 'ply-stats') {
                showOpeningPlyStats(selectedRow);
            } else {
                showOpeningPreview(selectedRow);
            }
        } else if (state.selectedOpeningKey) {
            removeOpeningDetailRow();
            clearOpeningSelection();
            state.selectedOpeningKey = null;
        }

        const headerCells = container.querySelectorAll<HTMLTableCellElement>('th[data-sort-key]');
        headerCells.forEach((th) => {
            const sortKey = th.getAttribute('data-sort-key');
            if (!sortKey) return;
            th.style.cursor = 'pointer';
            th.addEventListener('click', (event) => {
                event.preventDefault();
                cycleOpeningStatsSort(sortKey);
            });
        });
    }

    function cycleOpeningStatsSort(sortKey: string | null): void {
        if (!sortKey) return;
        const current = (state.openingStatsSort ?? { key: null, direction: null }) as {
            key: string | null;
            direction: string | null;
        };
        let nextDirection: 'asc' | 'desc' | null = 'asc';
        if (current.key === sortKey) {
            if (current.direction === 'asc') nextDirection = 'desc';
            else if (current.direction === 'desc') nextDirection = null;
        }
        state.openingStatsSort = nextDirection
            ? ({ key: sortKey, direction: nextDirection } as { key: string | null; direction: string | null })
            : ({ key: null, direction: null } as { key: string | null; direction: string | null });
        renderOpeningStatsTable();
    }

    function setTournamentTabActive(active: boolean): void {
        if (!active) {
            clearOpeningSelection();
            removeOpeningDetailRow();
        }
    }

    function isOpeningsActive(): boolean {
        const section = document.getElementById('openingStatsSection');
        if (!section) return false;
        return section.getAttribute('data-active') === '1';
    }

    function setOpeningsActive(active: boolean): void {
        const section = document.getElementById('openingStatsSection');
        if (!section) return;
        section.setAttribute('data-active', active ? '1' : '0');
        if (active) {
            const snapshot = getSummarySnapshot();
            const completedGames = snapshot?.completedGames ?? null;
            Promise.resolve(renderOpeningStats({ completedGames, trigger: 'openings:activate' })).catch((error) => {
                reportRecoverable('Activation.Refresh', error, 'Failed to refresh openings on activation');
            });
        } else {
            setTournamentTabActive(false);
        }
    }

    function clearOpeningSelection(): void {
        state.openingSelectedRow = null;
        state.selectedOpeningKey = null;
    }

    function removeOpeningDetailRow(): void {
        if (state.openingDetailRow?.isConnected) {
            state.openingDetailRow.remove();
        }
        state.openingDetailRow = null;
        state.openingDetailMode = null;
        if (state.openingBoardObserver) {
            state.openingBoardObserver.disconnect();
            state.openingBoardObserver = null;
        }
        if (state.openingBoardAdapter && typeof state.openingBoardAdapter.destroy === 'function') {
            state.openingBoardAdapter.destroy();
        }
        state.openingBoardAdapter = null;
        state.openingBoardElement = null;
        state.openingBoardInfo = null;
    }

    function showOpeningEngineStats(row: OpeningRow | undefined | null): void {
        if (!row || !row.rowElement) return;
        const isSameSelection =
            state.selectedOpeningKey === row.sfen &&
            state.openingDetailRow &&
            state.openingDetailRow.isConnected &&
            state.openingDetailMode === 'engine-stats';
        removeOpeningDetailRow();
        clearOpeningSelection();
        if (isSameSelection) {
            state.selectedOpeningKey = null;
            return;
        }

        const detailRow = document.createElement('tr');
        detailRow.className = 'details-inline opening-details opening-engine-stats';
        detailRow.setAttribute('data-sfen', row.sfen);
        detailRow.setAttribute('data-mode', 'engine-stats');
        const td = document.createElement('td');
        td.colSpan = 9;

        const paneHtml = renderOpeningEngineStatsPane({
            row,
            escapeHtml,
            formatRecordTriplet,
            formatWinRatio,
        });

        td.innerHTML = paneHtml;
        detailRow.appendChild(td);
        row.rowElement.insertAdjacentElement('afterend', detailRow);

        state.openingDetailRow = detailRow;
        state.openingDetailMode = 'engine-stats';
        state.openingSelectedRow = row.rowElement;
        state.selectedOpeningKey = row.sfen;
    }

    function showOpeningPlyStats(row: OpeningRow | undefined | null): void {
        if (!row || !row.rowElement) return;
        const isSameSelection =
            state.selectedOpeningKey === row.sfen &&
            state.openingDetailRow &&
            state.openingDetailRow.isConnected &&
            state.openingDetailMode === 'ply-stats';
        removeOpeningDetailRow();
        clearOpeningSelection();
        if (isSameSelection) {
            state.selectedOpeningKey = null;
            return;
        }

        const detailRow = document.createElement('tr');
        detailRow.className = 'details-inline opening-details opening-ply-stats';
        detailRow.setAttribute('data-sfen', row.sfen);
        detailRow.setAttribute('data-mode', 'ply-stats');
        const td = document.createElement('td');
        td.colSpan = 9;

        const paneHtml = renderOpeningPlyStatsPane({
            row,
            escapeHtml,
            resolveMaxMovesToDraw,
            defaultMaxMovesToDraw: DEFAULT_MAX_MOVES_TO_DRAW,
            gaussianDensity,
            hashString,
        });

        td.innerHTML = paneHtml;
        detailRow.appendChild(td);
        row.rowElement.insertAdjacentElement('afterend', detailRow);

        state.openingDetailRow = detailRow;
        state.openingDetailMode = 'ply-stats';
        state.openingSelectedRow = row.rowElement;
        state.selectedOpeningKey = row.sfen;
    }

    function showOpeningPreview(row: OpeningRow | undefined | null): void {
        if (!row || !row.rowElement) return;
        const adapterCtor = arenaWindow.ShogiBoardAdapter ?? null;
        if (typeof adapterCtor !== 'function') return;

        const isSameSelection =
            state.selectedOpeningKey === row.sfen &&
            state.openingDetailRow &&
            state.openingDetailRow.isConnected &&
            state.openingDetailMode === 'preview';
        removeOpeningDetailRow();
        clearOpeningSelection();

        if (isSameSelection) {
            state.selectedOpeningKey = null;
            return;
        }

        const detailRow = document.createElement('tr');
        detailRow.className = 'details-inline opening-details';
        detailRow.setAttribute('data-sfen', row.sfen);
        const td = document.createElement('td');
        td.colSpan = 9;
        const pane = document.createElement('div');
        pane.className = 'details-pane';
        const wrapper = document.createElement('div');
        wrapper.className = 'opening-board-wrapper';
        const boardEl = document.createElement('div');
        boardEl.className = 'opening-board';
        wrapper.append(boardEl);
        pane.appendChild(wrapper);
        td.appendChild(pane);
        detailRow.appendChild(td);
        row.rowElement.insertAdjacentElement('afterend', detailRow);

        state.openingDetailRow = detailRow;
        state.openingBoardElement = boardEl;
        const adapter = new adapterCtor();
        if (state.openingBoardObserver) {
            state.openingBoardObserver.disconnect();
            state.openingBoardObserver = null;
        }
        adapter.mount(boardEl);
        const sfen = row.sfen === 'startpos' ? 'startpos' : row.sfen;
        adapter.setPositionFromSFEN(sfen);
        state.openingBoardAdapter = adapter;
        const ResizeObserverCtor = arenaWindow.ResizeObserver ?? window.ResizeObserver;
        if (typeof ResizeObserverCtor === 'function') {
            const observer = new ResizeObserverCtor(() => {
                adapter.resize?.();
            });
            observer.observe(boardEl);
            state.openingBoardObserver = observer;
        } else {
            state.openingBoardObserver = null;
        }
        arenaWindow.requestAnimationFrame?.(() => adapter.resize?.());
        state.openingDetailMode = 'preview';

        state.openingSelectedRow = row.rowElement;
        state.selectedOpeningKey = row.sfen;
    }

    function normalizeOpeningKey(value: string | null | undefined): string | null {
        if (typeof value !== 'string') {
            return null;
        }
        const trimmed = value.trim();
        if (!trimmed) {
            return 'startpos';
        }
        return trimmed;
    }

    function focusOpeningBySfen(sfen: string): void {
        const key = normalizeOpeningKey(sfen);
        if (!key) {
            return;
        }

        state.pendingOpeningFocus = key;

        const attemptImmediateFocus = (): boolean => {
            const lookup = state.openingRowLookup;
            if (!lookup) {
                return false;
            }
            const target = lookup.get(key);
            if (!target || !target.rowElement) {
                return false;
            }
            state.pendingOpeningFocus = null;
            showOpeningPreview(target);
            const rowEl = target.rowElement;
            if (rowEl && typeof rowEl.scrollIntoView === 'function') {
                try {
                    rowEl.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
                } catch (_error) {
                    rowEl.scrollIntoView();
                }
            }
            return true;
        };

        if (attemptImmediateFocus()) {
            return;
        }

        const snapshot = getSummarySnapshot();
        const completedGames = snapshot?.completedGames ?? null;
        Promise.resolve(renderOpeningStats({ force: true, completedGames, trigger: 'openings:focus' })).catch(
            (error) => {
                state.pendingOpeningFocus = key;
                reportRecoverable('Focus.BySfen', error, 'Failed to focus opening by SFEN');
            },
        );
    }

    function refresh(): void {
        if (typeof Dashboard.updateStandings === 'function') {
            Dashboard.updateStandings();
        } else {
            throw new Error('Dashboard.updateStandings must be available for openings refresh');
        }
        if (typeof Dashboard.refreshMatchupInline === 'function') {
            Dashboard.refreshMatchupInline();
        } else {
            throw new Error('Dashboard.refreshMatchupInline must be available for openings refresh');
        }
        const snapshot = getSummarySnapshot();
        const completedGames = snapshot?.completedGames ?? null;
        Promise.resolve(renderOpeningStats({ force: true, completedGames, trigger: 'openings:refresh' })).catch(
            (error) => {
                reportRecoverable('Refresh.Manual', error, 'Failed to refresh opening statistics');
            },
        );
    }

    Object.assign(Dashboard, {
        renderOpeningStats,
        renderOpeningStatsTable,
        showOpeningEngineStats,
        showOpeningPlyStats,
        showOpeningPreview,
        focusOpeningBySfen,
        cycleOpeningStatsSort,
        setOpeningsActive,
        setActive: setTournamentTabActive,
        refresh,
        clearOpeningSelection,
        removeOpeningDetailRow,
    });

    installedTournamentOpenings = true;
    return Dashboard as unknown as TournamentDashboardAPI;
}
