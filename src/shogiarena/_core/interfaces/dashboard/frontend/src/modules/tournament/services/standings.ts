import { requestJson } from '@/modules/shared/services/api';
import { getLiveViewSnapshotStore } from '@/modules/shared';
import type {
    NormalizedTournamentSummary,
    NormalizedTournamentEngineMeta,
    RatingDeltaInfo,
    StandingsSortDirection,
    StandingsSortKey,
    TournamentState,
} from '@/modules/tournament/types';
import type { JsonObject, RequestError } from '@/types/shared';
import type { DashboardNavigationApi, DashboardTabsApi } from '@/types/globals';
import type { DashboardEnginesApi } from '@/modules/engines/types/public';
import type { TournamentDashboardAPI } from '@/modules/tournament/types';
import {
    renderEngineOptionsPane,
    renderFullOptionsTable as renderFullOptionsTableMarkup,
    renderStandingsTableMarkup,
    type StandingsRowView,
} from '@/modules/tournament/components/standings';
import type { DashboardCore } from '@/types/dashboard';

interface StandingsWindow extends Window {
    DashboardTournament?: TournamentDashboardAPI;
    DashboardTabs?: DashboardTabsApi;
    DashboardNavigation?: DashboardNavigationApi;
    DashboardEngines?: DashboardEnginesApi;
    DashboardCore?: DashboardCore;
}

const defaultWindow = window as StandingsWindow;

let installedTournamentStandings = false;

export function installTournamentStandings(owner: StandingsWindow = defaultWindow): TournamentDashboardAPI {
    if (installedTournamentStandings) {
        return owner.DashboardTournament as TournamentDashboardAPI;
    }

    const arenaWindow = owner;
    const { document, setTimeout: setWindowTimeout, clearTimeout: clearWindowTimeout } = owner;
    type WindowTimerId = ReturnType<StandingsWindow['setTimeout']>;
    type InlineDetailOptions = {
        preserveExisting?: boolean;
    };
    const STANDINGS_SORT_KEYS: readonly StandingsSortKey[] = [
        'engine',
        'games',
        'wins',
        'draws',
        'losses',
        'winrate',
        'rating',
    ];

    const isStandingsSortKey = (value: string): value is StandingsSortKey =>
        STANDINGS_SORT_KEYS.includes(value as StandingsSortKey);

    if (!arenaWindow.DashboardTournament) {
        arenaWindow.DashboardTournament = {} as TournamentDashboardAPI;
    }
    const Dashboard = arenaWindow.DashboardTournament as TournamentDashboardAPI;
    if (!Dashboard.state) {
        throw new Error('DashboardTournament.state must be initialized before standings module loads.');
    }
    const state = Dashboard.state as TournamentState;
    const requireMethod = <K extends keyof TournamentDashboardAPI, T extends TournamentDashboardAPI[K]>(
        method: T,
        name: K,
    ): Exclude<T, undefined> => {
        if (typeof method !== 'function') {
            throw new Error(`Dashboard.${String(name)} must be provided before standings module loads.`);
        }
        return method.bind(Dashboard) as Exclude<T, undefined>;
    };

    const escapeHtml = requireMethod(Dashboard.escapeHtml, 'escapeHtml');
    const formatOptionValue = requireMethod(Dashboard.formatOptionValue, 'formatOptionValue');
    const getApiBase = requireMethod(Dashboard.getApiBase, 'getApiBase');
    const getFinalRatingsFromSummary = requireMethod(
        Dashboard.getFinalRatingsFromSummary,
        'getFinalRatingsFromSummary',
    );

    let previousStandingsOrder: string[] = [];
    // Held at installer scope (not per-call) so the once-bound click/keydown delegation always reads
    // the latest engine metadata; a per-call const would freeze the first render's map into the listener.
    let standingsMetaMap = new Map<string, NormalizedTournamentEngineMeta>();

    const core = arenaWindow.DashboardCore;
    const liveViewStore = core ? getLiveViewSnapshotStore(core) : null;
    if (liveViewStore) {
        state.liveViewSnapshot = liveViewStore.getSnapshot();
        liveViewStore.subscribe((snapshot) => {
            state.liveViewSnapshot = snapshot;
        });
    }

    type StandingsRow = StandingsRowView;

    // Return the most recent tournament summary object; fail fast if missing.
    function getSummarySnapshot(): NormalizedTournamentSummary {
        if (typeof Dashboard.getNormalizedSummary !== 'function') {
            throw new Error('Dashboard.getNormalizedSummary must be defined before standings module loads.');
        }
        const snapshot = Dashboard.getNormalizedSummary();
        if (!snapshot) {
            throw new Error('Dashboard.getNormalizedSummary returned null; tournament summary is unavailable');
        }
        return snapshot;
    }

    // Maintain short-lived rating delta hints so recent changes remain visible.
    function refreshRatingDeltaState(ratings: Map<string, number>, ratingInitial: number): void {
        const now = Date.now();
        const carry: Map<string, RatingDeltaInfo> = new Map();

        if (state.ratingDeltas instanceof Map) {
            for (const [key, info] of state.ratingDeltas.entries()) {
                if (info && typeof info.until === 'number' && info.until > now) {
                    carry.set(key, info);
                }
            }
        }

        const prevRatings = state.prevRatings instanceof Map ? state.prevRatings : new Map<string, number>();
        for (const [name, current] of ratings.entries()) {
            if (!prevRatings.has(name)) continue;
            const before = Number(prevRatings.get(name) ?? ratingInitial);
            const after = Number(current ?? ratingInitial);
            const diff = Math.round(after - before);
            if (diff !== 0) {
                carry.set(String(name), { delta: diff, until: now + 3000 });
            }
        }

        if (state.pendingDeltaHint instanceof Map) {
            const nextHints = new Map<string, number>();
            for (const [name, until] of state.pendingDeltaHint.entries()) {
                if (typeof until !== 'number' || until <= now) continue;
                nextHints.set(name, until);
                if (!carry.has(name)) {
                    carry.set(name, { delta: 0, until });
                }
            }
            state.pendingDeltaHint = nextHints;
        }

        state.ratingDeltas = carry;
        state.prevRatings = new Map(ratings);

        if (state.ratingDeltaTimerId != null) {
            clearWindowTimeout(state.ratingDeltaTimerId as unknown as WindowTimerId);
            state.ratingDeltaTimerId = null;
        }

        if (!carry.size) {
            return;
        }

        let minRemain = Infinity;
        for (const info of carry.values()) {
            const remaining = Math.max(0, Number(info.until) - now);
            if (remaining < minRemain) minRemain = remaining;
        }
        if (!Number.isFinite(minRemain) || minRemain <= 0) {
            return;
        }

        const timerId: WindowTimerId = setWindowTimeout(
            () => {
                updateStandings();
            },
            Math.min(minRemain + 10, 3500),
        );
        state.ratingDeltaTimerId = timerId as unknown as ReturnType<typeof setTimeout>;
    }

    function collectEngineNames(normalized: NormalizedTournamentSummary, ratings: Map<string, number>): Set<string> {
        const names = new Set<string>();
        for (const name of normalized.engines) {
            names.add(name);
        }
        for (const name of Object.keys(normalized.engineStats)) {
            names.add(name);
        }
        for (const key of ratings.keys()) {
            names.add(String(key));
        }
        return names;
    }

    function updateStandings(): void {
        const container = document.getElementById('standingsTable');
        if (!container) return;
        // Flag ownership so other modules know this table is managed by the
        // tournament renderer (prevents live_summary.js from overwriting it).
        container.dataset.owner = 'tournament';

        const existingTable = container.querySelector<HTMLTableElement>('table.standings-table');
        const normalized = getSummarySnapshot();
        const ratingInitial = normalized.ratingInitial;
        const ratings = getFinalRatingsFromSummary();
        const btd = normalized.btd;
        const btdRatings = btd?.ratings ?? null;
        const usingBTD = Boolean(btdRatings && Object.keys(btdRatings).length);
        const anchorName = btd?.anchor ?? null;

        const ciMap = new Map<string, number>();
        if (btdRatings) {
            for (const [name, value] of Object.entries(btdRatings)) {
                const se = Number(value?.standardError ?? Number.NaN);
                if (Number.isFinite(se) && se > 0) {
                    ciMap.set(String(name), 1.96 * se);
                }
            }
        }

        refreshRatingDeltaState(ratings, ratingInitial);

        const names = collectEngineNames(normalized, ratings);

        const statsSource = normalized.engineStats;

        const list: StandingsRow[] = Array.from(names).map((name) => {
            const ratingValue = ratings.has(name) ? Number(ratings.get(name)) : ratingInitial;
            const stats = statsSource[name] as
                | { wins?: number; draws?: number; losses?: number; games?: number; score?: number | null }
                | undefined;
            const wins = Number(stats?.wins ?? 0) || 0;
            const draws = Number(stats?.draws ?? 0) || 0;
            const losses = Number(stats?.losses ?? 0) || 0;
            let games = Number(stats?.games ?? Number.NaN);
            if (!Number.isFinite(games) || games <= 0) {
                const total = wins + draws + losses;
                games = total > 0 ? total : 0;
            }
            const scoreCandidate = stats?.score;
            const score =
                typeof scoreCandidate === 'number' && Number.isFinite(scoreCandidate)
                    ? scoreCandidate
                    : games > 0
                      ? (wins + draws * 0.5) / games
                      : Number.NaN;
            return {
                name,
                rating: Number.isFinite(ratingValue) ? ratingValue : ratingInitial,
                wins,
                draws,
                losses,
                games,
                score,
            } satisfies StandingsRow;
        });

        const sortState = state.standingsSort ?? {
            key: null as StandingsSortKey | null,
            direction: null as StandingsSortDirection | null,
        };
        const sortKey = sortState.key;
        const sortDir = sortState.direction;

        const sorted = list.slice();
        const applyDefaultSort = () =>
            sorted.sort((a, b) => b.rating - a.rating || b.games - a.games || a.name.localeCompare(b.name));

        if (sortKey && sortDir) {
            const factor = sortDir === 'asc' ? 1 : -1;
            switch (sortKey) {
                case 'engine':
                    sorted.sort((a, b) => factor * a.name.localeCompare(b.name));
                    break;
                case 'games':
                    sorted.sort((a, b) => factor * (a.games - b.games) || a.name.localeCompare(b.name));
                    break;
                case 'wins':
                    sorted.sort((a, b) => factor * (a.wins - b.wins) || a.name.localeCompare(b.name));
                    break;
                case 'draws':
                    sorted.sort((a, b) => factor * (a.draws - b.draws) || a.name.localeCompare(b.name));
                    break;
                case 'losses':
                    sorted.sort((a, b) => factor * (a.losses - b.losses) || a.name.localeCompare(b.name));
                    break;
                case 'winrate':
                    sorted.sort((a, b) => {
                        const aScore = Number.isFinite(a.score) ? a.score : -Infinity;
                        const bScore = Number.isFinite(b.score) ? b.score : -Infinity;
                        if (aScore === bScore) return a.name.localeCompare(b.name);
                        return factor * (aScore - bScore);
                    });
                    break;
                case 'rating':
                    sorted.sort((a, b) => factor * (a.rating - b.rating) || a.name.localeCompare(b.name));
                    break;
                default:
                    applyDefaultSort();
                    break;
            }
        } else {
            applyDefaultSort();
        }

        const headerSortConfig = state.standingsSort ?? {
            key: null as StandingsSortKey | null,
            direction: null as StandingsSortDirection | null,
        };

        const matchupTitle = 'Expand matchup summary';
        const engineTitle = 'Show engine options';

        const { headerHtml, rowsHtml } = renderStandingsTableMarkup({
            rows: sorted,
            previousOrder: previousStandingsOrder,
            anchorName,
            usingBTD,
            ciMap,
            sortState: headerSortConfig,
            escapeHtml,
            formatRatingDisplay,
            engineTitle,
            matchupTitle,
        });

        if (!existingTable) {
            container.innerHTML =
                '<table class="standings-table" aria-label="Tournament standings table">' +
                `<thead>${headerHtml}</thead>` +
                `<tbody>${rowsHtml}</tbody>` +
                '</table>';
        } else {
            const thead = existingTable.querySelector('thead');
            if (thead && thead.innerHTML !== headerHtml) {
                thead.innerHTML = headerHtml;
            }
            updateStandingsInPlace(
                existingTable,
                sorted,
                ciMap,
                usingBTD,
                anchorName,
                previousStandingsOrder,
                engineTitle,
                matchupTitle,
            );
        }

        previousStandingsOrder = sorted.map((r) => r.name);
        setupStandingsClickHandlers(container as HTMLElement);
    }

    function updateStandingsInPlace(
        table: HTMLTableElement,
        rows: StandingsRow[],
        ciMap: Map<string, number>,
        usingBTD: boolean,
        anchorName: string | null,
        priorOrder: string[],
        engineTitle: string,
        matchupTitle: string,
    ): void {
        const tbody = table.querySelector('tbody');
        if (!tbody) return;

        const rowNodes = Array.from(tbody.querySelectorAll<HTMLTableRowElement>('tr[data-key]'));
        const rowMap = new Map<string, HTMLTableRowElement>();
        rowNodes.forEach((node) => {
            const key = node.getAttribute('data-key');
            if (key) rowMap.set(key, node);
        });

        const detailMap = new Map<string, HTMLTableRowElement>();
        tbody.querySelectorAll<HTMLTableRowElement>('tr.details-inline[data-for]').forEach((detail) => {
            const key = detail.getAttribute('data-for');
            if (key) detailMap.set(key, detail);
        });

        const priorIndex = new Map<string, number>();
        for (const [idx, name] of priorOrder.entries()) {
            priorIndex.set(name, idx);
        }

        let cursor: ChildNode | null = tbody.firstChild;
        const seen = new Set<string>();
        rows.forEach((row, idx) => {
            let node = rowMap.get(row.name);
            if (!node) {
                node = buildStandingsRowElement(row, idx, usingBTD, anchorName, ciMap, engineTitle, matchupTitle);
                rowMap.set(row.name, node);
            } else {
                refreshStandingsRow(node, row, idx, usingBTD, anchorName, ciMap, priorIndex, engineTitle, matchupTitle);
            }

            if (node !== cursor) {
                tbody.insertBefore(node, cursor);
            } else {
                cursor = cursor?.nextSibling ?? null;
            }
            cursor = node.nextSibling;

            const detail = detailMap.get(row.name);
            if (detail) {
                if (detail !== cursor) {
                    tbody.insertBefore(detail, cursor);
                } else {
                    cursor = cursor?.nextSibling ?? null;
                }
                cursor = detail.nextSibling;
            }
            seen.add(row.name);
        });

        while (cursor) {
            const next = cursor.nextSibling;
            if (
                cursor instanceof HTMLTableRowElement &&
                (cursor.hasAttribute('data-key') || cursor.hasAttribute('data-for'))
            ) {
                const key = cursor.getAttribute('data-key') || cursor.getAttribute('data-for');
                if (!key || !seen.has(key)) {
                    if (state.expanded && state.expanded.name === key) {
                        state.expanded = null;
                    }
                    cursor.remove();
                }
            }
            cursor = next;
        }

        if (typeof Dashboard.refreshMatchupInline === 'function') {
            Dashboard.refreshMatchupInline();
        }
    }

    function buildStandingsRowElement(
        row: StandingsRow,
        idx: number,
        usingBTD: boolean,
        anchorName: string | null,
        ciMap: Map<string, number>,
        engineTitle: string,
        matchupTitle: string,
    ): HTMLTableRowElement {
        const placement = idx + 1;
        const scoreValue = Number.isFinite(row.score) ? row.score.toFixed(3) : '-';
        const isAnchor = usingBTD && anchorName === row.name;
        const ratingDisplay = formatRatingDisplay(row.rating, isAnchor);
        const anchorBadge =
            anchorName && anchorName === row.name
                ? '<span class="anchor-badge" title="Anchor (reference)">⚓</span>'
                : '';
        const ratingExtra = ciMap.has(row.name)
            ? `<span class="rating-ci">±${Math.round(Number(ciMap.get(row.name)))}</span>`
            : '';
        const ratingSegments = [escapeHtml(ratingDisplay), ratingExtra, anchorBadge].filter(Boolean).join('');

        const rowEl = document.createElement('tr');
        rowEl.className = 'standings-row';
        rowEl.setAttribute('data-key', row.name);

        rowEl.innerHTML =
            `<td class="placement standings-rank group-engine">${placement}</td>` +
            `<td class="standings-name group-engine" data-action="options" role="button" tabindex="0" title="${escapeHtml(
                engineTitle,
            )}">${escapeHtml(row.name)}</td>` +
            `<td class="standings-games numeric group-stats" data-action="matchup" role="button" tabindex="0" title="${escapeHtml(
                matchupTitle,
            )}">${escapeHtml(row.games)}</td>` +
            `<td class="standings-wins numeric group-stats" data-action="matchup" role="button" tabindex="0" title="${escapeHtml(
                matchupTitle,
            )}">${escapeHtml(row.wins)}</td>` +
            `<td class="standings-draws numeric group-stats" data-action="matchup" role="button" tabindex="0" title="${escapeHtml(
                matchupTitle,
            )}">${escapeHtml(row.draws)}</td>` +
            `<td class="standings-losses numeric group-stats" data-action="matchup" role="button" tabindex="0" title="${escapeHtml(
                matchupTitle,
            )}">${escapeHtml(row.losses)}</td>` +
            `<td class="standings-winrate numeric group-stats" data-action="matchup" role="button" tabindex="0" title="${escapeHtml(
                matchupTitle,
            )}">${escapeHtml(scoreValue)}</td>` +
            `<td class="standings-rating group-stats rating-left" data-action="matchup" role="button" tabindex="0" title="${escapeHtml(
                matchupTitle,
            )}">${ratingSegments}</td>`;

        return rowEl;
    }

    function refreshStandingsRow(
        node: HTMLTableRowElement,
        row: StandingsRow,
        idx: number,
        usingBTD: boolean,
        anchorName: string | null,
        ciMap: Map<string, number>,
        _priorIndex: Map<string, number>,
        engineTitle: string,
        matchupTitle: string,
    ): void {
        const cells = node.querySelectorAll<HTMLTableCellElement>('td');
        if (cells.length < 8) {
            return;
        }
        const placement = idx + 1;
        const scoreValue = Number.isFinite(row.score) ? row.score.toFixed(3) : '-';
        const isAnchor = usingBTD && anchorName === row.name;
        const ratingDisplay = formatRatingDisplay(row.rating, isAnchor);
        const anchorBadge =
            anchorName && anchorName === row.name
                ? '<span class="anchor-badge" title="Anchor (reference)">⚓</span>'
                : '';
        const ratingExtra = ciMap.has(row.name)
            ? `<span class="rating-ci">±${Math.round(Number(ciMap.get(row.name)))}</span>`
            : '';
        const ratingSegments = [escapeHtml(ratingDisplay), ratingExtra, anchorBadge].filter(Boolean).join('');

        node.className = 'standings-row';
        node.setAttribute('data-key', row.name);
        cells[0].textContent = String(placement);
        cells[1].textContent = row.name;
        cells[1].setAttribute('title', engineTitle);
        cells[2].textContent = String(row.games);
        cells[2].setAttribute('title', matchupTitle);
        cells[3].textContent = String(row.wins);
        cells[3].setAttribute('title', matchupTitle);
        cells[4].textContent = String(row.draws);
        cells[4].setAttribute('title', matchupTitle);
        cells[5].textContent = String(row.losses);
        cells[5].setAttribute('title', matchupTitle);
        cells[6].textContent = scoreValue;
        cells[6].setAttribute('title', matchupTitle);
        cells[7].innerHTML = ratingSegments;
        cells[7].setAttribute('title', matchupTitle);
    }

    function cycleStandingsSort(sortKey: StandingsSortKey | null): void {
        if (!sortKey) return;
        const current = state.standingsSort ?? {
            key: null as StandingsSortKey | null,
            direction: null as StandingsSortDirection | null,
        };
        let nextDirection: StandingsSortDirection | null;

        if (current.key === sortKey) {
            if (current.direction === 'asc') nextDirection = 'desc';
            else if (current.direction === 'desc') nextDirection = null;
            else nextDirection = 'asc';
        } else {
            nextDirection = 'asc';
        }

        state.standingsSort = nextDirection
            ? { key: sortKey, direction: nextDirection }
            : { key: null, direction: null };
        updateStandings();
    }

    // _isAnchor is kept for call-site symmetry (BTD anchor rows) but does not yet alter
    // formatting; the previous `isAnchor ? base : base` no-op ternary has been collapsed.
    function formatRatingDisplay(ratingValue: number, _isAnchor: boolean): string {
        const value = Math.round(Number(ratingValue) || 0);
        const absStr = String(Math.abs(value));
        const sign = value < 0 ? '-' : ' ';
        const padded = absStr.padStart(4, ' ');
        return `${sign}${padded}`;
    }

    function setupStandingsClickHandlers(container: HTMLElement): void {
        const normalizedSummary = state.normalizedSummary;
        if (!normalizedSummary) {
            throw new Error('Tournament standings require normalized summary before binding handlers');
        }
        standingsMetaMap = new Map<string, NormalizedTournamentEngineMeta>(
            Object.entries(normalizedSummary?.engineMeta ?? {}).map(([name, meta]) => [
                name,
                meta as NormalizedTournamentEngineMeta,
            ]),
        );
        if (!container.dataset.sortDelegationBound) {
            container.addEventListener('click', (event) => {
                const target = event.target as HTMLElement | null;
                const header = target?.closest<HTMLTableCellElement>('th[data-sort-key]');
                if (!header || !container.contains(header)) {
                    return;
                }
                const sortKey = header.getAttribute('data-sort-key');
                if (!sortKey || !isStandingsSortKey(sortKey)) return;
                event.preventDefault();
                cycleStandingsSort(sortKey);
            });
            container.dataset.sortDelegationBound = '1';
        }

        const activateCell = (cell: HTMLTableCellElement, event: Event): void => {
            const action = cell.dataset.action;
            if (!action) return;
            const row = cell.closest<HTMLTableRowElement>('tr[data-key]');
            if (!row) return;
            const engineKey = row.getAttribute('data-key');
            if (!engineKey) return;
            event.preventDefault();
            if (action === 'options') {
                const navigated = navigateToEngineOptions(engineKey);
                if (!navigated) {
                    toggleOptionsInline(engineKey, row);
                }
                return;
            }
            if (action === 'matchup') {
                const preferredOpponent =
                    state.expanded && state.expanded.mode === 'matchup' && state.expanded.name === engineKey
                        ? (state.expanded.opponent ?? null)
                        : null;
                toggleMatchupInline(engineKey, row, preferredOpponent);
            }
        };

        if (!container.dataset.rowDelegationBound) {
            container.addEventListener('click', (event) => {
                const target = event.target as HTMLElement | null;
                const actionable = target?.closest<HTMLElement>('[data-action]');
                if (!actionable || !container.contains(actionable)) return;
                handleDelegatedAction(actionable, event);
            });
            container.addEventListener('keydown', (event) => {
                if (!(event instanceof KeyboardEvent)) return;
                if (event.key !== 'Enter' && event.key !== ' ') return;
                const target = event.target as HTMLElement | null;
                const cell = target?.closest<HTMLTableCellElement>('td[data-action]');
                if (!cell || !container.contains(cell)) return;
                handleDelegatedAction(cell, event);
            });
            container.dataset.rowDelegationBound = '1';
        }

        function renderOptionsHTML(name: string): string {
            return renderEngineOptionsPane({
                engineName: name,
                meta: standingsMetaMap.get(name),
                escapeHtml,
                formatOptionValue,
            });
        }

        function closeAllDetails() {
            container.querySelectorAll('tr.details-inline').forEach((tr) => {
                tr.remove();
            });
        }

        function navigateToEngineOptions(engine: string): boolean {
            if (!engine) return false;
            const navigation = arenaWindow.DashboardNavigation;
            if (navigation && typeof navigation.focusEngine === 'function') {
                navigation.focusEngine(engine, { tab: 'engines', detail: 'options', scroll: true });
                return true;
            }
            const tabsApi = arenaWindow.DashboardTabs;
            if (tabsApi && typeof tabsApi.setActive === 'function') {
                tabsApi.setActive('engines');
            }
            const enginesApi = arenaWindow.DashboardEngines;
            if (enginesApi && typeof enginesApi.focusEngine === 'function') {
                return Boolean(enginesApi.focusEngine(engine, { detail: 'options', scroll: true }));
            }
            return false;
        }

        function openOptionsInline(
            name: string,
            anchorTr: HTMLTableRowElement,
            options: InlineDetailOptions = {},
        ): void {
            const { preserveExisting = false } = options;
            if (!preserveExisting) {
                closeAllDetails();
            } else {
                const existing = container.querySelector(`tr.details-inline[data-for="${CSS.escape(name)}"]`);
                if (existing) existing.remove();
            }
            const details = document.createElement('tr');
            details.className = 'details-inline';
            details.setAttribute('data-for', name);
            details.setAttribute('data-mode', 'options');
            const td = document.createElement('td');
            td.colSpan = 8;
            const body = document.createElement('div');
            body.className = 'details-body theme-engine';
            body.innerHTML = `<div class="details-pane theme-engine">${renderOptionsHTML(name)}</div>`;
            td.appendChild(body);
            details.appendChild(td);
            anchorTr.insertAdjacentElement('afterend', details);
            state.expanded = { name, mode: 'options' };
        }

        function toggleOptionsInline(name: string, anchorTr: HTMLTableRowElement): void {
            const existing = container.querySelector(`tr.details-inline[data-for="${CSS.escape(name)}"]`);
            if (existing) {
                existing.remove();
                state.expanded = null;
                return;
            }
            openOptionsInline(name, anchorTr);
        }

        async function handleFullOptionsToggle(button: HTMLButtonElement, engineName: string): Promise<void> {
            const containerNode = button.closest<HTMLElement>('.engine-options-container');
            if (!containerNode) return;
            const panel = containerNode.querySelector<HTMLElement>('.engine-options-full');
            if (!panel) return;

            const expanded = containerNode.getAttribute('data-expanded') === '1';
            if (expanded) {
                containerNode.setAttribute('data-expanded', '0');
                panel.innerHTML = '';
                panel.classList.add('hidden');
                button.textContent = 'Show full USI options';
                return;
            }

            const meta = standingsMetaMap.get(engineName);
            if (!meta) {
                throw new Error(`Missing normalized engine metadata for ${engineName}`);
            }
            button.disabled = true;
            button.textContent = 'Loading...';
            try {
                const payload = await loadFullOptions(engineName);
                renderFullOptionsTable(panel, payload, meta);
                panel.classList.remove('hidden');
                containerNode.setAttribute('data-expanded', '1');
                button.textContent = 'Hide full USI options';
            } catch (error) {
                const message =
                    error instanceof Error && error.message ? error.message : 'Failed to load full USI options';
                panel.innerHTML = `<div class="subtle text-rose-400">${escapeHtml(message)}</div>`;
                panel.classList.remove('hidden');
                containerNode.setAttribute('data-expanded', '1');
                button.textContent = 'Hide full USI options';
            } finally {
                button.disabled = false;
            }
        }

        const handleDelegatedAction = (element: HTMLElement, event: Event): void => {
            const action = element.dataset.action || '';
            if (element instanceof HTMLTableCellElement) {
                activateCell(element, event);
                return;
            }

            if (action === 'toggle_full_options') {
                event.preventDefault();
                event.stopPropagation();
                const engineName =
                    element.getAttribute('data-engine') ||
                    element.closest<HTMLElement>('.engine-options-container')?.getAttribute('data-engine');
                if (!engineName) return;
                void handleFullOptionsToggle(element as HTMLButtonElement, engineName);
                return;
            }

            if (action === 'matchup_select') {
                event.preventDefault();
                event.stopPropagation();
                const detailRow = element.closest<HTMLTableRowElement>('tr.details-inline');
                if (!detailRow) return;
                const engineKey = detailRow.getAttribute('data-for');
                if (!engineKey) return;
                const opponentEncoded = element.getAttribute('data-opponent');
                const opponent = opponentEncoded ? decodeURIComponent(opponentEncoded) : null;
                const host = detailRow.querySelector<HTMLElement>('.details-body');
                state.expanded = { name: engineKey, mode: 'matchup', opponent: opponent ?? undefined };
                if (host && typeof Dashboard.focusMatchupOpponent === 'function') {
                    void Dashboard.focusMatchupOpponent(engineKey, host, opponent);
                } else if (host && typeof Dashboard.renderMatchupInline === 'function') {
                    void Dashboard.renderMatchupInline(
                        engineKey,
                        host,
                        opponent ? { preferredOpponent: opponent } : undefined,
                    );
                }
            }
        };

        function openMatchupInline(
            name: string,
            anchorTr: HTMLTableRowElement,
            preferredOpponent: string | null,
            options: InlineDetailOptions = {},
        ): void {
            const { preserveExisting = false } = options;
            if (!preserveExisting) {
                closeAllDetails();
            } else {
                const existing = container.querySelector(
                    `tr.details-inline[data-for="${CSS.escape(name)}"][data-mode="matchup"]`,
                );
                if (existing) existing.remove();
            }
            const details = document.createElement('tr');
            details.className = 'details-inline';
            details.setAttribute('data-for', name);
            details.setAttribute('data-mode', 'matchup');
            const td = document.createElement('td');
            td.colSpan = 8;
            const body = document.createElement('div');
            body.className = 'details-body theme-stats';
            td.appendChild(body);
            details.appendChild(td);
            anchorTr.insertAdjacentElement('afterend', details);
            state.expanded = { name, mode: 'matchup', opponent: preferredOpponent || undefined };
            if (typeof Dashboard.renderMatchupInline === 'function') {
                void Dashboard.renderMatchupInline(name, body, preferredOpponent ? { preferredOpponent } : undefined);
            }
        }

        function toggleMatchupInline(
            name: string,
            anchorTr: HTMLTableRowElement,
            preferredOpponent: string | null,
        ): void {
            const existing = container.querySelector(
                `tr.details-inline[data-for="${CSS.escape(name)}"][data-mode="matchup"]`,
            );
            if (existing) {
                existing.remove();
                state.expanded = null;
                return;
            }
            openMatchupInline(name, anchorTr, preferredOpponent);
        }
    }

    async function loadFullOptions(engineName: string): Promise<unknown> {
        if (state.engineFullOptions.has(engineName)) {
            return state.engineFullOptions.get(engineName);
        }
        const API_BASE = getApiBase();
        try {
            const data = await requestJson<JsonObject>(
                `${API_BASE}/api/engine_options/${encodeURIComponent(engineName)}`,
            );
            state.engineFullOptions.set(engineName, data);
            return data;
        } catch (error) {
            const requestError = error as RequestError | undefined;
            if (requestError?.status === 404) {
                throw new Error('Full USI options not available yet for this engine.');
            }
            const statusInfo = requestError?.status ? ` (${requestError.status})` : '';
            throw new Error(`Failed to load full USI options${statusInfo}`, { cause: error });
        }
    }

    function renderFullOptionsTable(
        container: HTMLElement,
        payload: unknown,
        meta: NormalizedTournamentEngineMeta,
    ): void {
        container.innerHTML = renderFullOptionsTableMarkup({
            payload,
            meta,
            escapeHtml,
            formatOptionValue,
        });
    }

    function focusEngineMatchups(
        engineName: string,
        options: {
            scroll?: boolean;
            detail?: 'matchup' | 'options';
            preferredOpponent?: string;
        } = {},
    ): boolean {
        // Programmatically expand the matchup inline panel for the requested engine.
        // Called by other tabs (e.g., schedule/instances) to unify navigation behaviour.
        const name = typeof engineName === 'string' ? engineName.trim() : '';
        if (!name) return false;
        const container = document.getElementById('standingsTable');
        if (!container) return false;

        const safeName = typeof CSS !== 'undefined' && typeof CSS.escape === 'function' ? CSS.escape(name) : name;

        let table = container.querySelector('table.standings-table');
        if (!table) {
            updateStandings();
            table = container.querySelector('table.standings-table');
            if (!table) return false;
        }

        let row = table.querySelector(`tbody tr[data-key="${safeName}"]`);
        if (!row && (typeof CSS === 'undefined' || typeof CSS.escape !== 'function')) {
            row =
                Array.from(table.querySelectorAll('tbody tr[data-key]')).find(
                    (tr) => tr.getAttribute('data-key') === name,
                ) || null;
        }
        if (!row) {
            updateStandings();
            table = container.querySelector('table.standings-table');
            if (!table) return false;
            row = table.querySelector(`tbody tr[data-key="${safeName}"]`);
            if (!row && (typeof CSS === 'undefined' || typeof CSS.escape !== 'function')) {
                row =
                    Array.from(table.querySelectorAll('tbody tr[data-key]')).find(
                        (tr) => tr.getAttribute('data-key') === name,
                    ) || null;
            }
            if (!row) return false;
        }

        const detailMode = options.detail === 'options' ? 'options' : 'matchup';
        const detailSelector = `tr.details-inline[data-for="${safeName}"][data-mode="${detailMode}"]`;
        let detailRow = table.querySelector(detailSelector);
        if (!detailRow && (typeof CSS === 'undefined' || typeof CSS.escape !== 'function')) {
            detailRow =
                Array.from(table.querySelectorAll(`tr.details-inline[data-mode="${detailMode}"]`)).find(
                    (tr) => tr.getAttribute('data-for') === name,
                ) || null;
        }
        if (!detailRow) {
            let triggerCell: HTMLElement | null = null;
            if (detailMode === 'matchup') {
                triggerCell =
                    row.querySelector('td.standings-games') ||
                    row.querySelector('td.standings-wins') ||
                    row.querySelector('td.standings-draws') ||
                    row.querySelector('td.standings-losses') ||
                    row.querySelector('td.standings-winrate') ||
                    row.querySelector('td.standings-rating') ||
                    row.querySelector('td[data-action="matchup"]');
            } else if (detailMode === 'options') {
                triggerCell = row.querySelector('td[data-action="options"]');
            }
            if (triggerCell) {
                triggerCell.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
                detailRow = table.querySelector(detailSelector);
            }
        }

        if (detailMode === 'matchup') {
            const opponent = options.preferredOpponent ?? null;
            const host = detailRow?.querySelector<HTMLElement>('.details-body');
            state.expanded = { name, mode: 'matchup', opponent: opponent ?? undefined };
            if (host && typeof Dashboard.focusMatchupOpponent === 'function') {
                void Dashboard.focusMatchupOpponent(name, host, opponent);
            }
        } else if (detailMode === 'options') {
            state.expanded = { name, mode: 'options' };
        }

        if (options.scroll !== false) {
            const target = detailRow || row;
            if (target && typeof target.scrollIntoView === 'function') {
                try {
                    target.scrollIntoView({ behavior: 'smooth', block: 'center' });
                } catch (_error) {
                    target.scrollIntoView();
                }
            }
        }
        return true;
    }

    Object.assign(Dashboard, {
        updateStandings,
        cycleStandingsSort,
        loadFullOptions,
        renderFullOptionsTable,
        focusEngineMatchups,
    });

    installedTournamentStandings = true;
    return Dashboard;
}
