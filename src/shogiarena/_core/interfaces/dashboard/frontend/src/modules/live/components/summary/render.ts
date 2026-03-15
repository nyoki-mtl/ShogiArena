import { createDetailsPane } from '@/modules/shared/components/elements';
import type { RatingDeltaInfo, TournamentEngineMeta } from '@/modules/tournament/types';
import type { JsonObject } from '@/types/shared';
import type { ExpandedState, ProgressViewModel, StandingsRow, StandingsViewOptions, PairwiseCell } from './state';

export function renderSpsaUnavailable(doc: Document): void {
    const message = 'Not available for gauntlet runs.';

    const movers = doc.getElementById('moversContainer');
    if (movers) {
        movers.classList.remove('loading');
        movers.innerHTML = '';
        const note = doc.createElement('p');
        note.className = 'muted';
        note.textContent = message;
        movers.appendChild(note);
    }

    const updatesBody = doc.getElementById('updatesTableBody');
    if (updatesBody) {
        updatesBody.innerHTML = '';
        const row = doc.createElement('tr');
        const cell = doc.createElement('td');
        cell.colSpan = 5;
        cell.className = 'muted';
        cell.textContent = message;
        row.appendChild(cell);
        updatesBody.appendChild(row);
    }

    const trendCanvas = doc.getElementById('convergenceTrendCanvas');
    if (trendCanvas) {
        const host = trendCanvas.parentElement ?? trendCanvas;
        host.innerHTML = '';
        const placeholder = doc.createElement('div');
        placeholder.className = 'muted';
        placeholder.textContent = message;
        host.appendChild(placeholder);
    }
}

export function renderSummaryStats(doc: Document, model: ProgressViewModel): void {
    const sprt = model.sprt ?? null;
    const banner = doc.getElementById('sprtBanner');
    if (banner) {
        if (sprt && (sprt.decision ?? 'continue') !== 'continue') {
            const d = sprt.decision ?? 'continue';
            const reason =
                d === 'accept_h1'
                    ? 'ACCEPT H1 (improvement detected)'
                    : d === 'accept_h0'
                      ? 'ACCEPT H0 (no improvement)'
                      : d;
            const text = `SPRT finished: ${reason}. New games are halted.`;
            if (banner.textContent !== text) {
                banner.textContent = text;
            }
            if (banner.style.display !== 'block') {
                banner.style.display = 'block';
            }
        } else {
            if (banner.style.display !== 'none') {
                banner.style.display = 'none';
            }
        }
    }

    const sprtBox = doc.getElementById('sprtBox');
    if (sprtBox) {
        const profile = doc.body?.dataset?.dashboardProfile ?? '';
        if (profile === 'sprt') {
            if (sprtBox.textContent !== '') {
                sprtBox.textContent = '';
            }
        } else if (sprt) {
            const llr = Number(sprt.llr ?? 0).toFixed(3);
            const lo = Number(sprt.lower ?? 0).toFixed(3);
            const up = Number(sprt.upper ?? 0).toFixed(3);
            const decision = sprt.decision ?? 'continue';
            const games = Number(sprt.games ?? 0);
            const text = `SPRT: LLR=${llr} [${lo}, ${up}] decision=${decision} games=${games}`;
            if (sprtBox.textContent !== text) {
                sprtBox.textContent = text;
            }
        } else if (sprtBox.textContent !== '') {
            sprtBox.textContent = '';
        }
    }

    const pentaBox = doc.getElementById('pentaBox');
    if (pentaBox) {
        if (pentaBox.textContent !== '') {
            pentaBox.textContent = '';
        }
    }

    const extraStats = doc.getElementById('extraStats');
    if (extraStats) {
        const sprtText = sprtBox?.textContent?.trim() ?? '';
        const pentaText = pentaBox?.textContent?.trim() ?? '';
        const nextHidden = !(sprtText || pentaText || model.hasPentaData);
        if (extraStats.hidden !== nextHidden) {
            extraStats.hidden = nextHidden;
        }
    }
}

function buildStandingsRow(
    doc: Document,
    row: StandingsRow,
    ratingDeltas: Map<string, RatingDeltaInfo>,
    usingBTD: boolean,
    ciMap: Map<string, number>,
    nowTs: number,
): HTMLTableRowElement {
    const tr = doc.createElement('tr');
    tr.className = 'standings-row';
    tr.dataset.key = row.name;

    const rankCell = doc.createElement('td');
    rankCell.className = 'standings-rank';
    rankCell.textContent = '';
    // rank text is filled in after rows are appended

    const nameCell = doc.createElement('td');
    nameCell.className = 'standings-name';
    nameCell.append(row.name);
    if (row.isAnchor) {
        const anchorBadge = doc.createElement('span');
        anchorBadge.className = 'anchor-badge';
        anchorBadge.title = 'Anchor (reference)';
        anchorBadge.textContent = '⚓';
        nameCell.appendChild(anchorBadge);
    }

    const tcCell = doc.createElement('td');
    tcCell.className = 'standings-tc';
    tcCell.textContent = row.tcShort;

    const wdlCell = doc.createElement('td');
    wdlCell.className = 'standings-wdl';
    wdlCell.textContent = row.wdl;

    const ratingCell = doc.createElement('td');
    ratingCell.className = 'standings-rating';
    populateRatingCell(doc, ratingCell, row, ratingDeltas, usingBTD, ciMap, nowTs);

    tr.append(rankCell, nameCell, tcCell, wdlCell, ratingCell);
    return tr;
}

function populateRatingCell(
    doc: Document,
    cell: HTMLTableCellElement,
    row: StandingsRow,
    ratingDeltas: Map<string, RatingDeltaInfo>,
    usingBTD: boolean,
    ciMap: Map<string, number>,
    nowTs: number,
): void {
    const ratingValue = Math.round(row.rating);
    let deltaText = '';
    let deltaClass = '';
    if (!usingBTD) {
        const info = ratingDeltas.get(row.name);
        if (info && typeof info.until === 'number' && info.until > nowTs) {
            const d = Number(info.delta || 0);
            if (d > 0) {
                deltaClass = 'rating-delta pos';
                deltaText = `(+${d})`;
            } else if (d < 0) {
                deltaClass = 'rating-delta neg';
                deltaText = `(${d})`;
            } else {
                deltaClass = 'rating-delta neu';
                deltaText = '(±0)';
            }
        }
    }
    const ciValue = ciMap.has(row.name) ? Math.round(Number(ciMap.get(row.name))) : null;
    const signature = `${ratingValue}|${deltaText}|${deltaClass}|${ciValue ?? ''}`;
    if (cell.dataset.signature === signature) {
        return;
    }
    cell.dataset.signature = signature;
    cell.replaceChildren();
    if (!usingBTD) {
        if (deltaText) {
            const span = doc.createElement('span');
            span.className = deltaClass;
            span.textContent = deltaText;
            cell.appendChild(span);
        }
    }
    cell.append(String(ratingValue));
    if (ciValue != null) {
        const ciSpan = doc.createElement('span');
        ciSpan.className = 'rating-ci';
        ciSpan.textContent = `±${ciValue}`;
        cell.appendChild(ciSpan);
    }
}

function attachRankNumbers(tbody: HTMLTableSectionElement): void {
    let rank = 1;
    tbody.querySelectorAll<HTMLTableRowElement>('tr').forEach((row) => {
        const cell = row.querySelector<HTMLTableCellElement>('td.standings-rank');
        if (cell) cell.textContent = String(rank);
        rank += 1;
    });
}

function closeAllDetails(container: HTMLElement): void {
    container.querySelectorAll<HTMLTableRowElement>('tr.details-inline').forEach((tr) => {
        tr.remove();
    });
}

function toggleInlineDetails(
    container: HTMLElement | null,
    name: string,
    mode: 'options' | 'penta',
    anchorTr: HTMLTableRowElement | null,
    renderOptionsPane: (name: string) => HTMLElement,
    renderPentaPane: (name: string, initialOpponent?: string | null) => HTMLElement,
    onExpandedChange: (state: ExpandedState | null) => void,
    initialOpponent?: string | null,
): void {
    if (!container) {
        onExpandedChange(null);
        return;
    }
    if (!anchorTr) {
        onExpandedChange(null);
        return;
    }
    const existing = container.querySelector<HTMLTableRowElement>(`tr.details-inline[data-for="${CSS.escape(name)}"]`);
    if (existing) {
        existing.remove();
        onExpandedChange(null);
        return;
    }
    closeAllDetails(container);
    const details = container.ownerDocument.createElement('tr');
    details.className = 'details-inline';
    details.setAttribute('data-for', name);
    details.setAttribute('data-mode', mode);
    const td = container.ownerDocument.createElement('td');
    td.colSpan = 4;
    const body = container.ownerDocument.createElement('div');
    body.className = 'details-body';
    td.appendChild(body);
    details.appendChild(td);
    anchorTr.insertAdjacentElement('afterend', details);
    if (mode === 'options') {
        body.replaceChildren(renderOptionsPane(name));
    } else if (mode === 'penta') {
        body.replaceChildren(renderPentaPane(name, initialOpponent ?? undefined));
    }
    onExpandedChange({ name, mode, opponent: initialOpponent ?? undefined });
}

export function renderStandingsView(options: StandingsViewOptions): string[] {
    const {
        doc,
        rows,
        ratingDeltas,
        usingBTD,
        ciMap,
        previousOrder,
        expanded,
        renderOptionsPane,
        renderPentaPane,
        onExpandedChange,
        onHighlightChange,
    } = options;

    const container = doc.getElementById('standingsTable') as HTMLElement | null;
    if (!container) return previousOrder;
    container.classList.add('table-panel', 'overflow-x-auto');
    const tournamentTab = doc.getElementById('tournamentTab');
    if (tournamentTab?.classList.contains('active') || container.dataset.owner === 'tournament') {
        return previousOrder;
    }
    container.dataset.owner = 'summary';

    let table = container.querySelector<HTMLTableElement>('table.standings-table');
    let tbody = table?.querySelector<HTMLTableSectionElement>('tbody') ?? null;
    if (!table) {
        table = doc.createElement('table');
        table.className = 'standings-table';
        table.setAttribute('aria-label', 'Standings table');

        const thead = doc.createElement('thead');
        const headerRow = doc.createElement('tr');
        const headers = ['#', 'Engine', 'TimeControl', 'W-D-L', 'Rating'];
        headers.forEach((label, index) => {
            const th = doc.createElement('th');
            th.textContent = label;
            if (index === headers.length - 1) {
                th.classList.add('standings-rating');
            }
            headerRow.appendChild(th);
        });
        thead.appendChild(headerRow);
        table.appendChild(thead);
        tbody = doc.createElement('tbody');
        table.appendChild(tbody);
        container.replaceChildren(table);
    }

    if (!tbody) return previousOrder;

    const nowTs = Date.now();
    const existingRows = Array.from(tbody.querySelectorAll<HTMLTableRowElement>('tr.standings-row'));
    const sameOrder =
        existingRows.length === rows.length &&
        existingRows.every((rowEl, idx) => rowEl.dataset.key === rows[idx]?.name);

    if (sameOrder) {
        existingRows.forEach((rowEl, idx) => {
            const row = rows[idx];
            rowEl.dataset.key = row.name;
            const rankCell = rowEl.querySelector<HTMLTableCellElement>('td.standings-rank');
            if (rankCell) rankCell.textContent = String(idx + 1);

            const nameCell = rowEl.querySelector<HTMLTableCellElement>('td.standings-name');
            if (nameCell) {
                nameCell.textContent = row.name;
                if (row.isAnchor) {
                    const anchorBadge = doc.createElement('span');
                    anchorBadge.className = 'anchor-badge';
                    anchorBadge.title = 'Anchor (reference)';
                    anchorBadge.textContent = '⚓';
                    nameCell.appendChild(anchorBadge);
                }
                nameCell.style.cursor = 'pointer';
                nameCell.title = 'Click to show engine options';
            }

            const tcCell = rowEl.querySelector<HTMLTableCellElement>('td.standings-tc');
            if (tcCell) tcCell.textContent = row.tcShort;
            const wdlCell = rowEl.querySelector<HTMLTableCellElement>('td.standings-wdl');
            if (wdlCell) {
                wdlCell.textContent = row.wdl;
                wdlCell.style.cursor = 'pointer';
                wdlCell.title = 'Click to show pentanomial vs...';
            }
            const ratingCell = rowEl.querySelector<HTMLTableCellElement>('td.standings-rating');
            if (ratingCell) {
                populateRatingCell(doc, ratingCell, row, ratingDeltas, usingBTD, ciMap, nowTs);
            }
        });
    } else {
        const fragment = doc.createDocumentFragment();
        rows.forEach((row) => {
            const tr = buildStandingsRow(doc, row, ratingDeltas, usingBTD, ciMap, nowTs);
            fragment.appendChild(tr);
        });
        tbody.replaceChildren(fragment);
        attachRankNumbers(tbody);
        tbody.querySelectorAll<HTMLTableCellElement>('td.standings-name').forEach((cell) => {
            cell.style.cursor = 'pointer';
            cell.title = 'Click to show engine options';
        });
        tbody.querySelectorAll<HTMLTableCellElement>('td.standings-wdl').forEach((cell) => {
            cell.style.cursor = 'pointer';
            cell.title = 'Click to show pentanomial vs...';
        });
    }

    function handleMouseOver(event: MouseEvent): void {
        const target = event.target as Element | null;
        const row = target?.closest<HTMLTableRowElement>('tr[data-key]');
        if (!row) return;
        const key = row.getAttribute('data-key');
        if (key) onHighlightChange(key);
    }

    function handleMouseOut(event: MouseEvent): void {
        const target = event.target as Element | null;
        const row = target?.closest<HTMLTableRowElement>('tr[data-key]');
        if (!row) return;
        const related = event.relatedTarget as Element | null;
        if (related && row.contains(related)) return;
        onHighlightChange(null);
    }

    function handleTableClick(event: MouseEvent): void {
        const target = event.target as HTMLElement | null;
        if (!target) return;
        const row = target.closest<HTMLTableRowElement>('tr[data-key]');
        if (!row) return;
        const key = row.getAttribute('data-key');
        if (!key) return;

        const nameCell = target.closest<HTMLTableCellElement>('td.standings-name');
        const wdlCell = target.closest<HTMLTableCellElement>('td.standings-wdl');
        if (nameCell) {
            toggleInlineDetails(container, key, 'options', row, renderOptionsPane, renderPentaPane, onExpandedChange);
        } else if (wdlCell) {
            toggleInlineDetails(container, key, 'penta', row, renderOptionsPane, renderPentaPane, onExpandedChange);
        }
    }

    if (!table.hasAttribute('data-bindings')) {
        table.setAttribute('data-bindings', 'true');
        table.addEventListener('mouseenter', handleMouseOver, true);
        table.addEventListener('mouseleave', handleMouseOut, true);
        table.addEventListener('click', handleTableClick);
    }

    if (!expanded) {
        closeAllDetails(container);
    }

    if (expanded) {
        const tr = container.querySelector<HTMLTableRowElement>(`tr[data-key="${CSS.escape(expanded.name)}"]`);
        if (tr) {
            const existing = container.querySelector<HTMLTableRowElement>(
                `tr.details-inline[data-for="${CSS.escape(expanded.name)}"][data-mode="${expanded.mode}"]`,
            );
            if (!existing) {
                toggleInlineDetails(
                    container,
                    expanded.name,
                    expanded.mode,
                    tr,
                    renderOptionsPane,
                    renderPentaPane,
                    onExpandedChange,
                    expanded.opponent,
                );
            }
        }
    }

    return rows.map((r) => r.name);
}

export function renderOptionsPane(
    doc: Document,
    metaMap: Map<string, TournamentEngineMeta>,
    name: string,
): HTMLElement {
    const meta = metaMap.get(name);
    const enginePath = typeof meta?.engine_path === 'string' ? meta.engine_path : '-';
    const merged: JsonObject = meta?.merged_options ?? {};
    const keys = Object.keys(merged).sort();

    const pane = createDetailsPane(doc);

    const pathRow = doc.createElement('div');
    pathRow.className = 'path';
    const pathLabel = doc.createElement('b');
    pathLabel.textContent = 'engine_path:';
    pathRow.append(pathLabel, doc.createTextNode(` ${enginePath}`));
    pane.appendChild(pathRow);

    const tableWrapper = doc.createElement('div');
    tableWrapper.className = 'mt-2';
    const table = doc.createElement('table');
    table.className = 'opt-table w-full';

    const thead = doc.createElement('thead');
    const headerRow = doc.createElement('tr');
    ['Option', 'Value'].forEach((label) => {
        const th = doc.createElement('th');
        th.textContent = label;
        headerRow.appendChild(th);
    });
    thead.appendChild(headerRow);
    table.appendChild(thead);

    const tbody = doc.createElement('tbody');
    if (keys.length === 0) {
        const row = doc.createElement('tr');
        const cell = doc.createElement('td');
        cell.colSpan = 2;
        cell.className = 'subtle';
        cell.textContent = 'No option overrides configured.';
        row.appendChild(cell);
        tbody.appendChild(row);
    } else {
        keys.forEach((keyName) => {
            const row = doc.createElement('tr');
            const optionCell = doc.createElement('td');
            optionCell.textContent = keyName;
            const valueCell = doc.createElement('td');
            valueCell.textContent = String(merged[keyName] ?? '');
            row.append(optionCell, valueCell);
            tbody.appendChild(row);
        });
    }
    table.appendChild(tbody);
    tableWrapper.appendChild(table);
    pane.appendChild(tableWrapper);

    return pane;
}

export function renderPairwiseMatrix(doc: Document, engines: string[], data: Map<string, PairwiseCell>): void {
    if (!engines.length) return;
    const container = doc.getElementById('pairwiseMatrix');
    if (!container) return;

    const table = doc.createElement('table');
    table.className = 'matrix';

    const thead = doc.createElement('thead');
    const headerRow = doc.createElement('tr');
    headerRow.appendChild(doc.createElement('th'));
    engines.forEach((engine) => {
        const th = doc.createElement('th');
        th.textContent = engine;
        headerRow.appendChild(th);
    });
    thead.appendChild(headerRow);
    table.appendChild(thead);

    const tbody = doc.createElement('tbody');
    engines.forEach((row) => {
        const tr = doc.createElement('tr');
        const rowHeader = doc.createElement('th');
        rowHeader.textContent = row;
        tr.appendChild(rowHeader);

        engines.forEach((col) => {
            const td = doc.createElement('td');
            if (row === col) {
                td.className = 'diagonal';
                td.textContent = '-';
            } else {
                const key = [row, col].sort().join('||');
                const cell = data.get(key) || {};
                const w = Number(cell[row] || 0);
                const l = Number(cell[col] || 0);
                const d = Number(cell.d || 0);
                const n = w + l + d;
                let deltaStr = '-';
                if (n > 0) {
                    const s = (w + 0.5 * d) / n;
                    const eps = 1e-6;
                    const sc = Math.max(eps, Math.min(1 - eps, s));
                    const delta = Math.round(400 * Math.log10(sc / (1 - sc)));
                    deltaStr = delta >= 0 ? `+${delta}` : String(delta);
                }
                td.textContent = `${w}-${d}-${l} (${deltaStr})`;
            }
            tr.appendChild(td);
        });

        tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    container.replaceChildren(table);
}
