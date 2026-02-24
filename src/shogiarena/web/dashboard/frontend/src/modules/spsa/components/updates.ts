import type {
    SpsaDashboardState,
    NormalizedSpsaUpdateDetail,
    NormalizedSpsaUpdateEntry,
    NormalizedSpsaParams,
    NormalizedSpsaPerturbations,
    SpsaPhaseWdlEntry,
    NormalizedSpsaParameter,
} from '@/modules/spsa/types';
import { INITIAL_UPDATE_IDX } from '@/modules/spsa/types';
import { escapeHtml } from '@/modules/shared/utils/html';
import type { GameOutcomeInfo, GameOutcomeKind } from '@/modules/games/types';
import { evaluateOutcome, RESULT_LABELS } from '@/modules/games/utils';
import { resultCodeToKifJP } from '@/modules/live/utils';
import {
    MAX_UPDATE_VISIBLE_ROWS,
    cacheUpdateDetail,
    getCachedUpdateDetail,
    getInitialUpdateDetail,
    getState,
    setUpdateExpanded,
} from '@/modules/spsa/state';

const UPDATE_TABLE_BODY_ID = 'updatesTableBody';
const UPDATE_COLUMNS = 7;
const PARAMETER_TABLE_BODY_ID = 'parameterDetailTableBody';
const PARAMETER_STATUS_ID = 'parameterDetailStatus';
const PARAMETER_COLUMNS = 5;
const GAMES_TABLE_BODY_ID = 'variantGamesTableBody';
const GAMES_STATUS_ID = 'variantGamesStatus';
const GAMES_COLUMNS = 7;
const LTC_GAMES_SECTION_ID = 'ltcGamesSection';
const LTC_GAMES_TABLE_BODY_ID = 'ltcGamesTableBody';
const LTC_GAMES_STATUS_ID = 'ltcGamesStatus';
const NBSP = '\u00A0';
const MOVES_PLACEHOLDER = NBSP.repeat(3);

type UpdateRowContext = {
    readonly bestKey: number | null;
};

type ResultBadgeVariant = 'win' | 'draw' | 'paused' | 'running' | 'pending' | 'cancelled' | 'error';

const RESULT_DETAIL_MAP: Readonly<Record<number, { detail?: string; abbr?: string }>> = Object.freeze({
    2: { detail: 'repetition', abbr: 'R' },
    4: { detail: 'declaration' },
    5: { detail: 'declaration' },
    6: { detail: 'max plies', abbr: 'M' },
    10: { detail: 'impasse', abbr: 'I' },
    8: { detail: 'illegal move' },
    9: { detail: 'illegal move' },
    12: { detail: 'illegal move' },
    13: { detail: 'illegal move' },
    11: { detail: 'paused', abbr: 'P' },
    16: { detail: 'timeout', abbr: 'T' },
    17: { detail: 'timeout', abbr: 'T' },
});

export function renderUpdatesLoading(): void {
    const tbody = getUpdateTableBody();
    if (!tbody) return;
    tbody.innerHTML = `<tr><td colspan="${UPDATE_COLUMNS}" class="loading">Loading updates...</td></tr>`;
}

export function renderUpdatesTable(state: SpsaDashboardState): void {
    const tbody = getUpdateTableBody();
    if (!tbody) return;

    const effectiveLimit =
        Number.isFinite(state.updates.limit) && state.updates.limit > 0
            ? Math.min(state.updates.limit, MAX_UPDATE_VISIBLE_ROWS)
            : MAX_UPDATE_VISIBLE_ROWS;
    const rows = state.updates.entries.slice(0, effectiveLimit);
    const initialRow = state.updates.initialEntry;
    const displayRows = initialRow ? [...rows, initialRow] : rows;

    if (!displayRows.length) {
        tbody.innerHTML = `<tr><td colspan="${UPDATE_COLUMNS}">No updates available</td></tr>`;
        return;
    }

    const selected = state.updates.expanded;
    let bestKey: number | null = null;
    const acceptedEntries = displayRows
        .filter((entry) => entry.ltcRegression?.accepted === true)
        .sort((a, b) => b.updateIdx - a.updateIdx);
    if (acceptedEntries.length > 0) {
        const bestAccepted = acceptedEntries[0];
        bestKey = bestAccepted.isInitial ? INITIAL_UPDATE_IDX : bestAccepted.updateIdx;
    } else if (initialRow) {
        bestKey = INITIAL_UPDATE_IDX;
    }
    const existingRows = collectExistingUpdateRows(tbody);
    let lastInserted: HTMLTableRowElement | null = null;

    for (const entry of displayRows) {
        const markup = renderRow(entry, selected, { bestKey });
        const signature = computeRowSignature(markup);
        const rowKey = entry.isInitial ? INITIAL_UPDATE_IDX : entry.updateIdx;
        const rowElement = hydrateUpdateRow(rowKey, markup, signature, existingRows);
        rowElement.dataset.rowSignature = signature;
        lastInserted = insertRowInOrder(tbody, rowElement, lastInserted);
    }

    existingRows.forEach((row) => {
        row.remove();
    });
}

function collectExistingUpdateRows(tbody: HTMLTableSectionElement): Map<number, HTMLTableRowElement> {
    const rows = new Map<number, HTMLTableRowElement>();
    const children = Array.from(tbody.querySelectorAll<HTMLTableRowElement>('tr'));
    for (const row of children) {
        const idxRaw = row.dataset.updateIdx;
        const idx = typeof idxRaw === 'string' ? Number(idxRaw) : Number.NaN;
        if (!Number.isFinite(idx)) {
            row.remove();
            continue;
        }
        rows.set(idx, row);
    }
    return rows;
}

function hydrateUpdateRow(
    key: number,
    markup: string,
    signature: string,
    existingRows: Map<number, HTMLTableRowElement>,
): HTMLTableRowElement {
    const existing = existingRows.get(key);
    if (!existing) {
        return createRowElement(markup);
    }
    existingRows.delete(key);
    if (existing.dataset.rowSignature === signature) {
        return existing;
    }
    const replacement = createRowElement(markup);
    existing.replaceWith(replacement);
    return replacement;
}

function createRowElement(markup: string): HTMLTableRowElement {
    const template = document.createElement('template');
    template.innerHTML = markup.trim();
    const element = template.content.firstElementChild;
    if (!element || !(element instanceof HTMLTableRowElement)) {
        throw new Error('SPSA updates: failed to render update row');
    }
    return element;
}

function insertRowInOrder(
    tbody: HTMLTableSectionElement,
    row: HTMLTableRowElement,
    lastInserted: HTMLTableRowElement | null,
): HTMLTableRowElement {
    if (!lastInserted) {
        if (tbody.firstElementChild === row) {
            return row;
        }
        tbody.insertBefore(row, tbody.firstElementChild);
        return row;
    }
    if (lastInserted === row) {
        return row;
    }
    const nextSibling = lastInserted.nextSibling;
    if (nextSibling === row) {
        return row;
    }
    tbody.insertBefore(row, nextSibling);
    return row;
}

function computeRowSignature(markup: string): string {
    let hash = 0;
    for (let idx = 0; idx < markup.length; idx += 1) {
        hash = (hash * 31 + markup.charCodeAt(idx)) | 0;
    }
    return `${markup.length}:${hash}`;
}

export function renderParameterPanel(state: SpsaDashboardState): void {
    const paramsData = state.params.data ?? null;
    if (!paramsData) {
        setParameterStatus('Loading parameter information...');
        return;
    }

    ensureParameterRows(paramsData);
    setParameterStatus('Please select an update.');
    resetGamesTable();
    setGamesStatus('Select an update to view game list.');
    setLtcGamesVisibility(true);
    clearLtcGamesTable();
    setLtcGamesStatus('Select an update to view LTC games.');

    if (state.updates.expanded.size === 0) {
        const initialDetail = state.updates.initialDetail ?? getInitialUpdateDetail();
        if (initialDetail) {
            const key = initialDetail.isInitial ? INITIAL_UPDATE_IDX : initialDetail.updateIdx;
            setUpdateExpanded(key, true);
        }
    }

    refreshParameterSelection(state);
}

export function refreshParameterSelection(state: SpsaDashboardState): void {
    const selectedIdx = getSelectedUpdateIdx(state);
    if (selectedIdx === null) {
        clearParameterValues();
        setParameterStatus('Please select an update.');
        resetGamesTable();
        setGamesStatus('Select an update to view game list.');
        setLtcGamesVisibility(true);
        clearLtcGamesTable();
        setLtcGamesStatus('Select an update to view LTC games.');
        return;
    }

    const detail = getCachedUpdateDetail(selectedIdx);
    if (detail) {
        renderUpdateDetail(detail, state.params.data ?? null);
    }
}

export function showUpdateDetailLoading(updateIdx: number): void {
    const isInitial = updateIdx === INITIAL_UPDATE_IDX;
    const heading = formatUpdateHeading(updateIdx, isInitial);
    const paramsData = getState().params.data ?? null;
    ensureParameterRows(paramsData);
    if (!setParameterCellsLoading()) {
        const tbody = getParameterTableBody();
        if (tbody) {
            tbody.innerHTML = `<tr><td colspan="${PARAMETER_COLUMNS}" class="loading">Loading parameters...</td></tr>`;
        }
    }
    setParameterStatus(`Loading parameters for ${heading}...`);
    setGamesTableLoading();
    setGamesStatus(`Loading game information for ${heading}...`);
    setLtcGamesVisibility(true);
    clearLtcGamesTable();
    setLtcGamesStatus(`Loading LTC games for ${heading}...`);
}

export function renderUpdateDetail(
    detail: NormalizedSpsaUpdateDetail,
    paramsData: NormalizedSpsaParams | null = null,
): void {
    const paramsSource = paramsData ?? getState().params.data ?? null;
    const fallbackNames = paramsSource ? undefined : collectDetailParameterNames(detail);
    ensureParameterRows(paramsSource, fallbackNames);

    const enriched = enrichInitialDetail(detail, paramsSource);
    cacheUpdateDetail(enriched, 'rest');

    applyDetailToParameterTable(enriched, paramsSource);

    const heading = formatUpdateHeading(enriched.updateIdx, enriched.isInitial);
    const statusParts: string[] = [heading];
    if (enriched.variantId) {
        statusParts.push(`Variant ${enriched.variantId}`);
    }
    const state = getState();
    const matchingEntry = state.updates.entries.find((e) => e.updateIdx === enriched.updateIdx);
    const ltcInfo = matchingEntry?.ltcRegression ?? enriched.ltcRegression ?? null;
    if (ltcInfo) {
        const status = ltcInfo.status ? ltcInfo.status.trim() : 'result';
        statusParts.push(`LTC ${status}`);
        statusParts.push(`W-D-L ${ltcInfo.tunedWins}-${ltcInfo.draws}-${ltcInfo.baselineWins}`);
    } else if (matchingEntry?.hasLtcRegression || enriched.hasLtcRegression) {
        statusParts.push('LTC Regression pending');
    }
    if (enriched.isPending) {
        statusParts.push('Running');
    }
    setParameterStatus(statusParts.join(' · '));
    renderVariantGames(enriched, heading);
    renderLtcGames(enriched, heading);
}

export function renderUpdateDetailError(updateIdx: number, message: string): void {
    const isInitial = updateIdx === INITIAL_UPDATE_IDX;
    const heading = formatUpdateHeading(updateIdx, isInitial);
    clearParameterValues();
    setParameterStatus(`${message} (${heading})`);
    clearGamesTable();
    setGamesStatus(`${message} (${heading})`);
    clearLtcGamesTable();
    setLtcGamesStatus(`${message} (${heading})`);
    setLtcGamesVisibility(true);
}

function renderRow(
    entry: NormalizedSpsaUpdateEntry,
    selectedSet: ReadonlySet<number>,
    context: UpdateRowContext,
): string {
    const key = entry.isInitial ? INITIAL_UPDATE_IDX : entry.updateIdx;
    const isSelected = selectedSet.has(key);
    const entryVariantId = entry.variantId?.trim() ?? '';
    const isBestVariant = context.bestKey !== null && key === context.bestKey;
    const ltcAccepted = entry.ltcRegression?.accepted;
    const rowClass = [
        'update-row',
        entry.isInitial ? 'update-row--initial' : '',
        entry.isPending ? 'update-row--pending' : '',
        isSelected ? 'update-row--selected' : '',
        isBestVariant ? 'update-row--best' : '',
        ltcAccepted === true ? 'update-row--ltc-accepted' : '',
        ltcAccepted === false ? 'update-row--ltc-rejected' : '',
    ]
        .filter(Boolean)
        .join(' ');

    const startedAt = typeof entry.startedAt === 'number' && Number.isFinite(entry.startedAt) ? entry.startedAt : null;
    const endedAt =
        entry.hasLtcRegression || entry.ltcRegression
            ? typeof entry.ltcRegression?.completedAt === 'number' && Number.isFinite(entry.ltcRegression.completedAt)
                ? entry.ltcRegression.completedAt
                : null
            : typeof entry.endedAt === 'number' && Number.isFinite(entry.endedAt)
              ? entry.endedAt
              : null;
    const deltaNorm = Number.isFinite(entry.deltaNorm) ? (entry.deltaNorm as number) : null;
    const label = formatUpdateLabel(entry.updateIdx, entry.isInitial);
    const heading = formatUpdateHeading(entry.updateIdx, entry.isInitial);
    const plusWdl = formatPhaseWdlCell(entry.phaseWdl, 'plus');
    const minusWdl = formatPhaseWdlCell(entry.phaseWdl, 'minus');
    const ltcSummary = formatLtcRegressionCell(entry);
    const variantAttr = entryVariantId ? ` data-variant-id="${escapeHtml(entryVariantId)}"` : '';
    const rowTitleParts = [heading];
    if (entryVariantId) {
        rowTitleParts.push(`Variant ${entryVariantId}`);
    }

    return `
        <tr class="${rowClass}" data-update-idx="${entry.updateIdx}" data-action="spsa:update-detail" aria-selected="${isSelected}" title="${escapeHtml(rowTitleParts.join(' · '))}"${variantAttr}>
            <td>
                <button type="button" class="update-cell-button" data-action="spsa:update-detail" data-update-idx="${entry.updateIdx}">
                    <span class="update-label">${escapeHtml(label)}</span>
                </button>
            </td>
            <td class="update-phase-cell" data-phase="plus">${escapeHtml(plusWdl)}</td>
            <td class="update-phase-cell" data-phase="minus">${escapeHtml(minusWdl)}</td>
            <td class="update-ltc-cell">${ltcSummary}</td>
            <td>${formatDateTimeCell(startedAt)}</td>
            <td>${formatDateTimeCell(endedAt)}</td>
            <td>${deltaNorm !== null ? formatNumber(deltaNorm) : '-'}</td>
        </tr>
    `;
}

function formatDateTimeCell(value: number | null): string {
    if (typeof value === 'number' && Number.isFinite(value)) {
        return escapeHtml(new Date(value).toLocaleString());
    }
    return '-';
}

function resolvePhaseStats(
    phaseMap: Readonly<Record<string, SpsaPhaseWdlEntry>> | undefined,
    key: string,
): SpsaPhaseWdlEntry | null {
    if (!phaseMap) return null;
    const direct = phaseMap[key];
    if (direct) return direct;
    const lower = key.toLowerCase();
    const normalized = phaseMap[lower];
    if (normalized) return normalized;
    for (const [phaseKey, stats] of Object.entries(phaseMap)) {
        if (phaseKey.toLowerCase() === lower) {
            return stats;
        }
    }
    return null;
}

function formatPhaseWdlCell(phaseMap: Readonly<Record<string, SpsaPhaseWdlEntry>> | undefined, key: string): string {
    const stats = resolvePhaseStats(phaseMap, key);
    if (!stats) {
        return '—';
    }
    const wins = Math.max(0, Number(stats.wins ?? 0));
    const draws = Math.max(0, Number(stats.draws ?? 0));
    const losses = Math.max(0, Number(stats.losses ?? 0));
    return `${wins}-${draws}-${losses}`;
}

function sanitizeStatusSlug(status: string | null): string {
    if (!status) return '';
    return status
        .trim()
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, '-')
        .replace(/^-+|-+$/g, '');
}

function buildLtcTooltip(entry: NormalizedSpsaUpdateEntry): string {
    const ltc = entry.ltcRegression;
    if (!ltc) {
        return 'LTC regression pending';
    }
    const parts: string[] = [];
    if (ltc.status) {
        parts.push(`status: ${ltc.status}`);
    }
    parts.push(`W-D-L ${ltc.tunedWins}-${ltc.draws}-${ltc.baselineWins}`);
    if (ltc.winrate !== null) {
        parts.push(`winrate ${(ltc.winrate * 100).toFixed(1)}%`);
    }
    if (ltc.elo !== null) {
        parts.push(`elo ${ltc.elo.toFixed(1)}`);
    }
    if (ltc.accepted !== null) {
        parts.push(ltc.accepted ? 'accepted' : 'rejected');
    }
    if (ltc.failReasons.length > 0) {
        parts.push(`fail reasons: ${ltc.failReasons.join(', ')}`);
    }
    return parts.join(' · ');
}

function formatLtcRegressionCell(entry: NormalizedSpsaUpdateEntry): string {
    if (!entry.hasLtcRegression && !entry.ltcRegression) {
        return '—';
    }
    const tooltip = buildLtcTooltip(entry);
    const ltc = entry.ltcRegression;
    if (!ltc) {
        return `<span class="ltc-wdl ltc-wdl--pending" title="${escapeHtml(tooltip)}">Pending</span>`;
    }
    const statusSlug = sanitizeStatusSlug(ltc.status ?? null);
    const classes = ['ltc-wdl'];
    if (statusSlug) {
        classes.push(`ltc-wdl--${statusSlug}`);
    }
    const label = `${ltc.tunedWins}-${ltc.draws}-${ltc.baselineWins}`;
    return `<span class="${classes.join(' ')}" title="${escapeHtml(tooltip)}">${escapeHtml(label)}</span>`;
}

function isFiniteNumber(value: unknown): value is number {
    return typeof value === 'number' && Number.isFinite(value);
}

function formatPerturbationCell(perturbations: NormalizedSpsaPerturbations, name: string): string {
    const plus = perturbations.plus?.[name];
    if (isFiniteNumber(plus)) {
        return formatSigned(plus as number, 4);
    }
    const minus = perturbations.minus?.[name];
    if (isFiniteNumber(minus)) {
        return formatSigned(minus as number, 4);
    }
    return '-';
}

function applyDetailToParameterTable(
    detail: NormalizedSpsaUpdateDetail,
    paramsData: NormalizedSpsaParams | null,
): void {
    const tbody = getParameterTableBody();
    if (!tbody) return;

    const rows = tbody.querySelectorAll<HTMLTableRowElement>('tr[data-param-name]');
    if (!rows.length) return;

    const params = detail.params ?? {};
    const deltas = detail.deltas ?? {};
    const perturbations = detail.perturbations ?? { plus: {}, minus: {} };
    const isInitialDetail = Boolean(detail.isInitial);
    const isPendingDetail = Boolean(detail.isPending);

    rows.forEach((row) => {
        const name = row.dataset.paramName ?? '';
        const initialValue = paramsData ? resolveInitialValue(name, paramsData) : null;

        let value = Number.isFinite(params[name]) ? (params[name] as number) : null;
        if (isPendingDetail) {
            value = null;
        } else if (value === null) {
            value = initialValue;
        }

        let delta = Number.isFinite(deltas[name]) ? (deltas[name] as number) : null;

        if (isInitialDetail) {
            delta = 0;
        } else if (!isPendingDetail && delta === null && value !== null && initialValue !== null) {
            delta = value - initialValue;
        }

        const valueCell = row.querySelector<HTMLElement>('[data-param-cell="value"]');
        const perturbCell = row.querySelector<HTMLElement>('[data-param-cell="perturb"]');
        const deltaCell = row.querySelector<HTMLElement>('[data-param-cell="delta"]');
        const initialCell = row.querySelector<HTMLElement>('.param-initial');
        const perturbText = formatPerturbationCell(perturbations, name);

        if (valueCell) {
            valueCell.classList.remove('loading');
            valueCell.textContent = value !== null ? formatBaselineNumber(value) : '-';
        }
        if (perturbCell) {
            perturbCell.classList.remove('loading');
            perturbCell.textContent = perturbText;
        }
        if (deltaCell) {
            deltaCell.classList.remove('loading');
            deltaCell.textContent = delta !== null ? formatSigned(delta, 4) : '-';
        }
        if (initialCell) {
            initialCell.textContent = initialValue !== null ? formatBaselineNumber(initialValue) : '-';
        }
    });
}

function renderVariantGames(detail: NormalizedSpsaUpdateDetail, updateLabel: string): void {
    const tbody = getGamesTableBody();
    if (!tbody) return;
    const games = Array.isArray(detail.games) ? detail.games : [];
    const variantLabel = detail.variantId ? `Variant ${detail.variantId}` : updateLabel;
    const hasVariantGames = detail.availableIncludes.includes('variant_games') || (detail.gamesCount ?? 0) > 0;
    const isVariantLoaded = detail.loadedIncludes.includes('variant_games');
    if (!games.length) {
        if (hasVariantGames && !isVariantLoaded) {
            setGamesTableLoading();
            setGamesStatus(`${variantLabel} · Loading games...`);
            return;
        }
        clearGamesTable();
        setGamesStatus(`${variantLabel} · No games`);
        return;
    }

    const rows = games.map((game) => renderGameRow(game)).join('');
    tbody.innerHTML = rows;
    const statusSummary = formatVariantStatusSummary(games);
    const suffix = statusSummary ? `（${statusSummary}）` : '';
    const baseStatus = `${variantLabel} · Games ${games.length}${suffix}`;
    setGamesStatus(!isVariantLoaded && hasVariantGames ? `${baseStatus} · Updating...` : baseStatus);
}

function renderLtcGames(detail: NormalizedSpsaUpdateDetail, updateLabel: string): void {
    const games = Array.isArray(detail.ltcGames) ? detail.ltcGames : [];
    const variantLabel = detail.variantId ? `Variant ${detail.variantId}` : updateLabel;
    const ltcCount = Number.isFinite(detail.ltcGamesCount)
        ? (detail.ltcGamesCount as number)
        : Math.max(0, games.length);
    const hasLtcGames = detail.availableIncludes.includes('ltc_games') || ltcCount > 0 || games.length > 0;
    const isLtcLoaded = detail.loadedIncludes.includes('ltc_games');
    if (!games.length) {
        if (hasLtcGames && !isLtcLoaded) {
            setLtcGamesVisibility(true);
            setLtcGamesTableLoading();
            setLtcGamesStatus(`${variantLabel} · Loading LTC games...`);
            return;
        }
        setLtcGamesVisibility(true);
        clearLtcGamesTable();
        setLtcGamesStatus(`${variantLabel} · No LTC games`);
        return;
    }

    const tbody = getLtcGamesTableBody();
    if (!tbody) {
        setLtcGamesVisibility(false);
        return;
    }

    setLtcGamesVisibility(true);
    const rows = games.map((game) => renderGameRow(game)).join('');
    tbody.innerHTML = rows;
    const statusSummary = formatVariantStatusSummary(games);
    const suffix = statusSummary ? `（${statusSummary}）` : '';
    const baseStatus = `${variantLabel} · LTC Games ${games.length}${suffix}`;
    setLtcGamesStatus(!isLtcLoaded && hasLtcGames ? `${baseStatus} · Updating...` : baseStatus);
}

const VARIANT_STATUS_LABELS: Record<string, string> = {
    running: 'Running',
    pending: 'Pending',
    completed: 'Completed',
    cancelled: 'Cancelled',
};

const VARIANT_STATUS_ORDER = ['running', 'pending', 'completed', 'cancelled'] as const;

function formatVariantStatusSummary(games: NormalizedSpsaUpdateDetail['games']): string {
    if (!games.length) return '';
    const counts: Record<string, number> = {};
    games.forEach((game) => {
        const status = normalizeVariantGameStatus(game);
        counts[status] = (counts[status] ?? 0) + 1;
    });
    const parts: string[] = [];
    for (const key of VARIANT_STATUS_ORDER) {
        const count = counts[key];
        if (count) {
            parts.push(`${VARIANT_STATUS_LABELS[key] ?? key} ${count}`);
            delete counts[key];
        }
    }
    for (const [key, count] of Object.entries(counts)) {
        if (count) {
            parts.push(`${key} ${count}`);
        }
    }
    return parts.join(' / ');
}

function normalizeVariantGameStatus(game: NormalizedSpsaUpdateDetail['games'][number]): string {
    const raw = typeof game.status === 'string' ? game.status.trim().toLowerCase() : '';
    if (raw) {
        return raw;
    }
    return Number.isFinite(game.resultCode) ? 'completed' : 'pending';
}

function formatVariantTimestampCell(value: string | null, className: string): string {
    if (typeof value === 'string' && value.trim()) {
        const parsed = new Date(value);
        if (!Number.isNaN(parsed.valueOf())) {
            const label = parsed.toLocaleString();
            const iso = parsed.toISOString();
            return `<td class="${className}" title="${escapeHtml(iso)}">${escapeHtml(label)}</td>`;
        }
    }
    return `<td class="${className} games-time-empty">-</td>`;
}

function renderGameRow(game: NormalizedSpsaUpdateDetail['games'][number]): string {
    const gameId = typeof game.gameId === 'string' && game.gameId.trim() ? game.gameId.trim() : null;
    const safeGameId = gameId ? escapeHtml(gameId) : null;
    const black = typeof game.blackPlayer === 'string' && game.blackPlayer ? game.blackPlayer : '—';
    const white = typeof game.whitePlayer === 'string' && game.whitePlayer ? game.whitePlayer : '—';
    const movesDisplay =
        typeof game.numMoves === 'number' && Number.isFinite(game.numMoves) ? String(game.numMoves) : MOVES_PLACEHOLDER;
    const normalizedStatus = normalizeVariantGameStatus(game);
    const result = formatVariantResult(game.resultCode ?? null, normalizedStatus);
    const startCell = formatVariantTimestampCell(game.startTime ?? null, 'games-started-cell');
    const endCell = formatVariantTimestampCell(game.endTime ?? null, 'games-ended-cell');
    const rowDataAttr = safeGameId ? ` data-game-id="${safeGameId}"` : '';
    const liveLabel = safeGameId ? `Open Live View for game ${safeGameId}` : 'Open Live View';
    const gameCell = gameId
        ? `<button type="button" class="games-game-link" data-game-id="${safeGameId}" data-status="${escapeHtml(normalizedStatus)}" aria-label="${liveLabel}" title="${liveLabel}">${safeGameId}</button>`
        : '<span class="games-game-label">-</span>';

    return `
        <tr class="variant-game-row"${rowDataAttr}>
            <td class="games-game-cell table-group-id">
                <div class="games-game-wrapper">${gameCell}</div>
            </td>
            <td class="games-side table-group-engine" data-side="black">${escapeHtml(black)}</td>
            <td class="games-side table-group-engine" data-side="white">${escapeHtml(white)}</td>
            <td class="games-result-cell result-outcome-${escapeHtml(result.outcome)}" data-outcome="${escapeHtml(result.outcome)}" data-variant="${escapeHtml(result.variant)}" data-status="${escapeHtml(normalizedStatus)}"${result.detail ? ` data-detail="${escapeHtml(result.detail)}"` : ''} title="${escapeHtml(result.tooltip)}">
                ${result.badgeHtml}
            </td>
            <td class="games-moves-cell numeric">${escapeHtml(movesDisplay)}</td>
            ${startCell}
            ${endCell}
        </tr>
    `;
}

const RESULT_BADGE_BASE: Record<GameOutcomeKind, string> = {
    pending: '',
    'black-win': 'B',
    'white-win': 'W',
    draw: 'DR',
    cancelled: 'CN',
    paused: 'P',
    error: 'ERR',
    running: 'RUN',
};

function resolveVariantStatus(code: number | null, fallbackStatus?: string | null): string {
    const normalized = typeof fallbackStatus === 'string' ? fallbackStatus.trim().toLowerCase() : '';
    if (!Number.isFinite(code)) {
        if (normalized === 'running') return 'running';
        if (normalized === 'cancelled') return 'cancelled';
        if (normalized === 'error') return 'error';
        if (normalized === 'paused') return 'paused';
        if (normalized === 'completed') return 'completed';
        return 'pending';
    }
    if (normalized === 'cancelled') {
        return 'cancelled';
    }
    return 'completed';
}

function resolveVariantDetail(resultCode: number | null): {
    readonly detailKey: string | null;
    readonly abbr: string;
    readonly label: string;
} {
    if (typeof resultCode !== 'number' || !Number.isFinite(resultCode)) {
        return { detailKey: null, abbr: '', label: '—' };
    }
    const entry = RESULT_DETAIL_MAP[resultCode] ?? {};
    const detail = entry.detail ?? null;
    const abbr = entry.abbr ?? '';
    return { detailKey: detail, abbr, label: resultCodeToKifJP(resultCode) };
}

function resolveBadgeVariant(outcome: GameOutcomeKind, status: string): ResultBadgeVariant {
    const normalizedStatus = status.trim().toLowerCase();
    switch (outcome) {
        case 'running':
            return 'running';
        case 'pending':
            if (normalizedStatus === 'pending') return 'pending';
            return 'pending';
        case 'cancelled':
            return 'cancelled';
        case 'error':
            return 'error';
        case 'paused':
            return 'paused';
        case 'draw':
            return 'draw';
        default:
            return 'win';
    }
}

function resolveBadgeDisplay(variant: ResultBadgeVariant, base: string): string {
    const normalized = base.trim().toUpperCase();
    switch (variant) {
        case 'running':
            return '';
        case 'pending':
            return normalized;
        case 'cancelled':
            return '';
        case 'error':
            return normalized || 'ERR';
        case 'paused':
            return normalized || 'P';
        case 'draw':
            return normalized || 'DR';
        default:
            return normalized || '—';
    }
}

function createResultTooltip(outcomeInfo: GameOutcomeInfo, detailLabel: string | null): string {
    const base = RESULT_LABELS[outcomeInfo.outcome] ?? '';
    const detail = detailLabel?.trim() ?? '';
    if (detail && base && !detail.toLowerCase().includes(base.toLowerCase())) {
        return `${detail} (${base})`;
    }
    if (detail) return detail;
    if (base) return base;
    return '—';
}

function resolveOutcomeSymbol(outcome: GameOutcomeKind, detailKey: string, abbrRaw: string): string {
    const abbr = abbrRaw.trim().toUpperCase();
    if (outcome === 'black-win' || outcome === 'white-win') {
        const symbols: Record<string, string> = {
            declaration: 'D',
            timeout: 'T',
            forfeit: 'F',
            'illegal move': 'I',
        };
        return symbols[detailKey] ?? '';
    }
    if (outcome === 'draw') {
        if (abbr) return abbr.length === 1 ? abbr : abbr.charAt(0);
        if (detailKey === 'repetition') return 'R';
        if (detailKey === 'max plies') return 'M';
        if (detailKey === 'impasse') return 'I';
        if (detailKey === 'timeout') return 'T';
    }
    if (outcome === 'paused') {
        if (abbr) return abbr.length === 1 ? abbr : abbr.charAt(0);
        if (detailKey === 'paused') return 'P';
        return 'P';
    }
    if (abbr) return abbr.length === 1 ? abbr : abbr.charAt(0);
    return '';
}

function renderResultBadge(options: {
    readonly outcomeInfo: GameOutcomeInfo;
    readonly variant: ResultBadgeVariant;
    readonly detail: string | null;
    readonly abbr: string;
    readonly baseLabel: string;
    readonly tooltip: string;
}): string {
    const { outcomeInfo, variant, detail, abbr, baseLabel, tooltip } = options;
    const badgeClasses = ['games-result-badge'];
    const badgeAttrs = [
        `data-result-variant="${escapeHtml(variant)}"`,
        `data-outcome="${escapeHtml(outcomeInfo.outcome)}"`,
        'aria-hidden="true"',
    ];
    let display = '';

    if (variant === 'win') {
        badgeClasses.push('games-result-badge-disc');
        badgeAttrs.push('data-shape="disc"');
        const symbol = resolveOutcomeSymbol(outcomeInfo.outcome, detail ?? '', abbr || baseLabel);
        if (symbol) {
            display = symbol;
        } else {
            badgeAttrs.push('data-symbol-empty="true"');
        }
    } else if (variant === 'draw' || variant === 'paused') {
        badgeClasses.push('games-result-badge-triangle');
        badgeAttrs.push('data-shape="triangle"');
        const symbol = resolveOutcomeSymbol(outcomeInfo.outcome, detail ?? '', abbr || baseLabel);
        display = symbol || resolveBadgeDisplay(variant, abbr || baseLabel) || '—';
    } else {
        display = resolveBadgeDisplay(variant, abbr || baseLabel);
    }

    const label = display ? `<span class="games-result-badge-label">${escapeHtml(display)}</span>` : '';
    const badge = `<span class="${badgeClasses.join(' ')}" ${badgeAttrs.join(' ')}>${label}</span>`;
    const sr = `<span class="games-visually-hidden">${escapeHtml(tooltip)}</span>`;
    return `${badge}${sr}`;
}

function formatVariantResult(
    resultCode: number | null,
    rawStatus: string | null,
): {
    readonly badgeHtml: string;
    readonly outcome: GameOutcomeKind;
    readonly variant: ResultBadgeVariant;
    readonly tooltip: string;
    readonly detail: string | null;
} {
    const status = resolveVariantStatus(resultCode, rawStatus);
    const outcomeInfo = evaluateOutcome(status, resultCode);
    const outcome = outcomeInfo.outcome;
    const detailInfo = resolveVariantDetail(resultCode);
    const variant = resolveBadgeVariant(outcome, status);
    const baseLabel = RESULT_BADGE_BASE[outcome] ?? '';
    const tooltip = createResultTooltip(outcomeInfo, detailInfo.label);
    const badgeHtml = renderResultBadge({
        outcomeInfo,
        variant,
        detail: detailInfo.detailKey,
        abbr: detailInfo.abbr,
        baseLabel,
        tooltip,
    });
    return { badgeHtml, outcome, variant, tooltip, detail: detailInfo.detailKey ?? null };
}

function initialiseParameterRows(paramsData: NormalizedSpsaParams | null, names: readonly string[]): void {
    const tbody = getParameterTableBody();
    if (!tbody) return;
    const signature = names.join('|');
    if (!names.length) {
        tbody.dataset.paramSignature = signature;
        tbody.innerHTML = `<tr><td colspan="${PARAMETER_COLUMNS}" class="empty">No parameter information available.</td></tr>`;
        return;
    }

    tbody.innerHTML = '';
    const entriesByName = new Map<string, NormalizedSpsaParameter>();
    paramsData?.params?.forEach((entry) => {
        if (entry?.name) {
            entriesByName.set(entry.name, entry);
        }
    });

    for (const name of names) {
        const entry = entriesByName.get(name);
        if (entry?.notUsed) {
            continue;
        }
        const row = document.createElement('tr');
        row.dataset.paramName = name;

        const nameCell = document.createElement('td');
        nameCell.className = 'param-name';

        const nameButton = document.createElement('button');
        nameButton.type = 'button';
        nameButton.className = 'param-name-link';
        nameButton.dataset.action = 'spsa:focus-parameter';
        nameButton.dataset.paramName = name;
        nameButton.textContent = name;
        nameButton.title = 'View this parameter in Parameter Analysis';
        nameCell.appendChild(nameButton);

        const valueCell = document.createElement('td');
        valueCell.className = 'param-value';
        valueCell.dataset.paramCell = 'value';
        valueCell.textContent = '-';

        const initialCell = document.createElement('td');
        initialCell.className = 'param-initial';
        const initialValue = paramsData ? resolveInitialValue(name, paramsData) : null;
        initialCell.textContent = initialValue !== null ? formatBaselineNumber(initialValue) : '-';

        const deltaCell = document.createElement('td');
        deltaCell.className = 'param-delta';
        deltaCell.dataset.paramCell = 'delta';
        deltaCell.textContent = '-';

        const perturbCell = document.createElement('td');
        perturbCell.className = 'param-perturb';
        perturbCell.dataset.paramCell = 'perturb';
        perturbCell.textContent = '-';

        row.append(nameCell, valueCell, initialCell, deltaCell, perturbCell);
        tbody.appendChild(row);
    }
}

function ensureParameterRows(paramsData: NormalizedSpsaParams | null, fallbackNames?: readonly string[]): void {
    const tbody = getParameterTableBody();
    if (!tbody) return;
    const names = paramsData ? collectParameterNames(paramsData) : deriveDetailParameterNames(fallbackNames);
    if (!names.length) return;
    const signaturePrefix = paramsData ? 'meta' : 'detail';
    const expectedSignature = `${signaturePrefix}:${names.join('|')}`;
    const currentSignature = tbody.dataset.paramSignature ?? '';
    if (!tbody.querySelector('tr[data-param-name]') || currentSignature !== expectedSignature) {
        initialiseParameterRows(paramsData, names);
        tbody.dataset.paramSignature = expectedSignature;
    }
}

function collectParameterNames(paramsData: NormalizedSpsaParams): string[] {
    const names = new Set<string>();
    for (const param of paramsData.params ?? []) {
        if (!param?.name || param.notUsed) continue;
        names.add(param.name);
    }
    return Array.from(names).sort((a, b) => a.localeCompare(b));
}

function deriveDetailParameterNames(fallbackNames?: readonly string[]): string[] {
    if (!fallbackNames || fallbackNames.length === 0) {
        return [];
    }
    return Array.from(new Set(fallbackNames)).sort((a, b) => a.localeCompare(b));
}

function resolveInitialValue(name: string, paramsData: NormalizedSpsaParams): number | null {
    const initialValue = paramsData.initialParams?.[name];
    if (typeof initialValue === 'number' && Number.isFinite(initialValue)) {
        return initialValue;
    }
    return null;
}

function clearParameterValues(): void {
    const tbody = getParameterTableBody();
    if (!tbody) return;
    const rows = tbody.querySelectorAll<HTMLTableRowElement>('tr[data-param-name]');
    if (!rows.length) return;
    rows.forEach((row) => {
        row.querySelectorAll<HTMLElement>('[data-param-cell]').forEach((cell) => {
            cell.classList.remove('loading');
            cell.textContent = '-';
        });
    });
}

function setParameterCellsLoading(): boolean {
    const tbody = getParameterTableBody();
    if (!tbody) return false;
    const rows = tbody.querySelectorAll<HTMLTableRowElement>('tr[data-param-name]');
    if (!rows.length) return false;
    rows.forEach((row) => {
        row.querySelectorAll<HTMLElement>('[data-param-cell]').forEach((cell) => {
            cell.classList.add('loading');
            cell.innerHTML = '<span class="loading-spinner">⟳</span>';
        });
    });
    return true;
}

function getSelectedUpdateIdx(state: SpsaDashboardState): number | null {
    const entries = Array.from(state.updates.expanded);
    if (!entries.length) {
        return null;
    }
    const [first] = entries;
    return Number.isFinite(first) ? first : null;
}

function setParameterStatus(message: string): void {
    const statusNode = document.getElementById(PARAMETER_STATUS_ID);
    if (!statusNode) return;
    statusNode.textContent = message;
}

function setGamesStatus(message: string): void {
    const statusNode = document.getElementById(GAMES_STATUS_ID);
    if (!statusNode) return;
    statusNode.textContent = message;
}

function setLtcGamesStatus(message: string): void {
    const statusNode = document.getElementById(LTC_GAMES_STATUS_ID);
    if (!statusNode) return;
    statusNode.textContent = message;
}

function getUpdateTableBody(): HTMLTableSectionElement | null {
    const node = document.getElementById(UPDATE_TABLE_BODY_ID);
    return node instanceof HTMLTableSectionElement ? node : null;
}

function getParameterTableBody(): HTMLTableSectionElement | null {
    const node = document.getElementById(PARAMETER_TABLE_BODY_ID);
    return node instanceof HTMLTableSectionElement ? node : null;
}

function collectDetailParameterNames(detail: NormalizedSpsaUpdateDetail): readonly string[] {
    const names = new Set<string>();
    const addRecord = (record?: Record<string, unknown>) => {
        if (!record) return;
        Object.keys(record).forEach((key) => {
            if (key) {
                names.add(key);
            }
        });
    };
    addRecord(detail.params as Record<string, unknown> | undefined);
    addRecord(detail.deltas as Record<string, unknown> | undefined);
    addRecord(detail.gradients as Record<string, unknown> | undefined);
    addRecord(detail.perturbations?.plus as Record<string, unknown> | undefined);
    addRecord(detail.perturbations?.minus as Record<string, unknown> | undefined);
    return Array.from(names).sort((a, b) => a.localeCompare(b));
}

function getGamesTableBody(): HTMLTableSectionElement | null {
    const node = document.getElementById(GAMES_TABLE_BODY_ID);
    return node instanceof HTMLTableSectionElement ? node : null;
}

function getLtcGamesSection(): HTMLElement | null {
    const node = document.getElementById(LTC_GAMES_SECTION_ID);
    return node instanceof HTMLElement ? node : null;
}

function getLtcGamesTableBody(): HTMLTableSectionElement | null {
    const node = document.getElementById(LTC_GAMES_TABLE_BODY_ID);
    return node instanceof HTMLTableSectionElement ? node : null;
}

function resetGamesTable(): void {
    const tbody = getGamesTableBody();
    if (!tbody) return;
    tbody.innerHTML = `<tr><td colspan="${GAMES_COLUMNS}" class="empty">No update selected.</td></tr>`;
}

function clearGamesTable(): void {
    const tbody = getGamesTableBody();
    if (!tbody) return;
    tbody.innerHTML = `<tr><td colspan="${GAMES_COLUMNS}" class="empty">No game data available.</td></tr>`;
}

function clearLtcGamesTable(): void {
    const tbody = getLtcGamesTableBody();
    if (!tbody) return;
    tbody.innerHTML = '';
}

function setGamesTableLoading(): void {
    const tbody = getGamesTableBody();
    if (!tbody) return;
    tbody.innerHTML = `<tr><td colspan="${GAMES_COLUMNS}" class="loading"><span class="loading-spinner">⟳</span> Loading game information...</td></tr>`;
}

function setLtcGamesTableLoading(): void {
    const tbody = getLtcGamesTableBody();
    if (!tbody) return;
    tbody.innerHTML = `<tr><td colspan="${GAMES_COLUMNS}" class="loading"><span class="loading-spinner">⟳</span> Loading LTC game information...</td></tr>`;
}

function setLtcGamesVisibility(visible: boolean): void {
    const section = getLtcGamesSection();
    if (!section) return;
    if (visible) {
        section.hidden = false;
        section.removeAttribute('aria-hidden');
    } else {
        section.hidden = true;
        section.setAttribute('aria-hidden', 'true');
    }
}

function formatSigned(value: number, precision = 2): string {
    const rounded = value.toFixed(precision);
    if (value > 0) {
        return `+${rounded}`;
    }
    return rounded;
}

function formatNumber(value: number): string {
    if (Number.isInteger(value)) {
        return value.toString();
    }
    return value.toFixed(6).replace(/(?:\.0+|(\.[0-9]*[1-9])0+)$/, '$1');
}

function formatBaselineNumber(value: number): string {
    // Always show sufficient precision for baseline values to distinguish
    // internal float values from quantized integer values.
    // Uses the same precision as formatNumber for consistency.
    const formatted = value.toFixed(6).replace(/(?:\.0+|(\.[0-9]*[1-9])0+)$/, '$1');
    // Ensure at least .0 is shown for integer values (e.g., 5 becomes 5.0)
    return formatted.includes('.') ? formatted : `${formatted}.0`;
}

function formatUpdateLabel(updateIdx: number, isInitial: boolean): string {
    if (isInitial || updateIdx === INITIAL_UPDATE_IDX) {
        return 'Initial';
    }
    return String(updateIdx);
}

function formatUpdateHeading(updateIdx: number, isInitial: boolean): string {
    if (isInitial || updateIdx === INITIAL_UPDATE_IDX) {
        return 'Initial Params';
    }
    const label = formatUpdateLabel(updateIdx, isInitial);
    return `Update #${label}`;
}

function enrichInitialDetail(
    detail: NormalizedSpsaUpdateDetail,
    paramsData: NormalizedSpsaParams | null,
): NormalizedSpsaUpdateDetail {
    if (!detail.isInitial) {
        return detail;
    }

    const paramsSource = paramsData ?? getState().params.data ?? null;
    if (!paramsSource) {
        return detail;
    }

    const existingParams = detail.params ?? {};
    if (Object.keys(existingParams).length > 0) {
        return detail;
    }

    const fallbackParams: Record<string, number> = {};
    const fallbackDeltas: Record<string, number> = { ...(detail.deltas ?? {}) };

    for (const param of paramsSource.params ?? []) {
        const name = param.name;
        if (!name || param.notUsed) continue;
        if (typeof param.value === 'number' && Number.isFinite(param.value)) {
            fallbackParams[name] = param.value;
        }
        if (typeof param.delta === 'number' && Number.isFinite(param.delta)) {
            fallbackDeltas[name] = param.delta;
        }
    }

    const paramCount = Object.keys(fallbackParams).length;
    if (!paramCount) {
        return detail;
    }

    if (Object.keys(fallbackDeltas).length === 0 && paramsSource.initialParams) {
        for (const [name, value] of Object.entries(fallbackParams)) {
            const initialValue = paramsSource.initialParams[name];
            if (typeof initialValue === 'number' && Number.isFinite(initialValue)) {
                fallbackDeltas[name] = value - initialValue;
            }
        }
    }

    return {
        ...detail,
        params: fallbackParams,
        deltas: fallbackDeltas,
    };
}
