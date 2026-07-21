import { requestJson } from '@/modules/shared/services/api';
import type {
    BookEntry,
    BookModuleView,
    BookPairEntry,
    BookPairPayload,
    BookPairSide,
    BookSummaryPayload,
    BookWdl,
    BookWindow,
    DashboardBookApi,
} from '../types';

const defaultWindow = window as BookWindow;

const BOOK_REFRESH_INTERVAL_MS = 8000;
const DEFAULT_BOOK_VIEW: BookModuleView = 'summary';

function escapeHtml(value: string): string {
    return value.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function formatPercent(value: number | null | undefined): string {
    if (value == null || !Number.isFinite(value)) return '-';
    return `${(value * 100).toFixed(1)}%`;
}

function formatWdl(counts: BookWdl | undefined | null): string {
    if (!counts) return '-';
    return `${counts.wins}-${counts.losses}-${counts.draws} (${counts.games})`;
}

function formatReason(value: string | null | undefined): string {
    if (!value) return '-';
    return value;
}

function formatFirstDiff(pair: BookPairEntry): string {
    if (pair.first_diff_ply == null) {
        return formatReason(pair.first_diff_reason);
    }
    return `ply ${pair.first_diff_ply} · ${formatReason(pair.first_diff_reason)}`;
}

function formatSide(side: BookPairSide | null | undefined, label: string): string {
    if (!side) {
        return '<span class="book-pair-side-empty">-</span>';
    }
    const prefix = side.book_prefix_usi.join(' ');
    const title = prefix ? ` title="${escapeHtml(prefix)}"` : '';
    return `
        <div class="book-pair-side"${title}>
            <span class="book-pair-side-title">${escapeHtml(label)} #${side.order} · ${escapeHtml(side.game_id)}</span>
            <span>${escapeHtml(side.black)} vs ${escapeHtml(side.white)}</span>
            <span>prefix ${side.book_prefix_length} · ${escapeHtml(side.measurement_source)} · ${escapeHtml(side.measurement_status)}</span>
        </div>`;
}

function statusBadge(status: string): string {
    return `<span class="book-status" data-status="${escapeHtml(status)}">${escapeHtml(status)}</span>`;
}

function renderHistogram(histogram: Record<string, number>): string {
    const entries = Object.entries(histogram)
        .map(([ply, count]) => [Number(ply), count] as const)
        .filter(([ply]) => Number.isFinite(ply))
        .sort((a, b) => a[0] - b[0]);
    if (!entries.length) {
        return '<div class="book-oob-empty">no out-of-book samples (use ?out_of_book=1)</div>';
    }
    const max = entries.reduce((acc, [, count]) => Math.max(acc, count), 0) || 1;
    const bars = entries
        .map(([ply, count]) => {
            const pct = Math.round((count / max) * 100);
            return (
                `<div class="book-oob-row"><span class="book-oob-ply">ply ${ply}</span>` +
                `<span class="book-oob-bar" style="width:${pct}%"></span>` +
                `<span class="book-oob-count">${count}</span></div>`
            );
        })
        .join('');
    return `<div class="book-oob-hist">${bars}</div>`;
}

function renderEngines(engines: Record<string, BookWdl>): string {
    const rows = Object.entries(engines)
        .map(([name, counts]) => {
            return (
                `<tr><td>${escapeHtml(name)}</td><td>${formatWdl(counts)}</td>` +
                `<td>${formatPercent(counts.win_rate)}</td></tr>`
            );
        })
        .join('');
    return rows || '<tr><td colspan="3">-</td></tr>';
}

function renderBookCard(entry: BookEntry): string {
    const label = escapeHtml(entry.label ?? entry.key);
    const path = entry.resolved_path ? escapeHtml(entry.resolved_path) : '';
    const validation = escapeHtml(entry.validation ?? 'unknown');
    const oob = entry.out_of_book;
    const oobSummary =
        oob.samples > 0
            ? `mean ${oob.mean_ply != null ? oob.mean_ply.toFixed(1) : '-'} / max ${oob.max_ply ?? '-'} ` +
              `(n=${oob.samples}, in-book/未測定=${oob.unbounded})`
            : `未測定 (in-book or not computed: ${oob.unbounded})`;
    return `
        <section class="book-card" data-book-key="${escapeHtml(entry.key)}">
            <header class="book-card-head">
                <h3 class="book-card-title">${label}</h3>
                <span class="book-card-validation">${validation}</span>
            </header>
            ${path ? `<div class="book-card-path">${path}</div>` : ''}
            <div class="book-card-grid">
                <div><span class="book-stat-label">Overall W-L-D</span><span class="book-stat-value">${formatWdl(entry.overall)}</span></div>
                <div><span class="book-stat-label">Win rate</span><span class="book-stat-value">${formatPercent(entry.overall.win_rate)}</span></div>
                <div><span class="book-stat-label">Black (先)</span><span class="book-stat-value">${formatWdl(entry.by_color.black)} · ${formatPercent(entry.by_color.black?.win_rate)}</span></div>
                <div><span class="book-stat-label">White (後)</span><span class="book-stat-value">${formatWdl(entry.by_color.white)} · ${formatPercent(entry.by_color.white?.win_rate)}</span></div>
            </div>
            <div class="book-card-section">
                <span class="book-stat-label">Out-of-book ply</span>
                <div class="book-oob-summary">${escapeHtml(oobSummary)}</div>
                ${renderHistogram(oob.histogram)}
            </div>
            <div class="book-card-section">
                <span class="book-stat-label">Engines</span>
                <table class="book-engines"><thead><tr><th>Engine</th><th>W-L-D</th><th>Win%</th></tr></thead>
                <tbody>${renderEngines(entry.engines)}</tbody></table>
            </div>
        </section>`;
}

export function renderBookSummary(payload: BookSummaryPayload, container: HTMLElement): void {
    if (!payload.books.length) {
        container.innerHTML = '<div class="book-empty">No engine opening book usage recorded for this run.</div>';
        return;
    }
    const note = payload.note ? `<p class="book-note">${escapeHtml(payload.note)}</p>` : '';
    const cards = payload.books.map(renderBookCard).join('');
    container.innerHTML = `${note}<div class="book-cards">${cards}</div>`;
}

function renderPairSummary(payload: BookPairPayload): string {
    const summary = payload.summary;
    return `
        <div class="book-pair-summary" aria-label="Book pair summary">
            <div><span class="book-stat-label">Pairs</span><span class="book-stat-value">${summary.pairs}</span></div>
            <div><span class="book-stat-label">Measured</span><span class="book-stat-value">${summary.measured_pairs}</span></div>
            <div><span class="book-stat-label">Partial</span><span class="book-stat-value">${summary.partial_pairs}</span></div>
            <div><span class="book-stat-label">Unmeasured</span><span class="book-stat-value">${summary.unmeasured_pairs}</span></div>
            <div><span class="book-stat-label">Invalid</span><span class="book-stat-value">${summary.invalid_pairs}</span></div>
            <div><span class="book-stat-label">Ambiguous</span><span class="book-stat-value">${summary.ambiguous_pairs}</span></div>
            <div><span class="book-stat-label">Unpaired</span><span class="book-stat-value">${summary.unpaired_games}</span></div>
            <div><span class="book-stat-label">Mean match</span><span class="book-stat-value">${formatPercent(summary.mean_prefix_match_rate)}</span></div>
        </div>`;
}

function renderPairRow(pair: BookPairEntry): string {
    const orders = pair.orders.map((order) => `#${order}`).join(' / ');
    const gameIds = pair.game_ids.map(escapeHtml).join(' / ');
    const sfen = pair.initial_sfen ? `<div class="book-pair-muted">${escapeHtml(pair.initial_sfen)}</div>` : '';
    return `
        <tr class="book-pair-row" data-pair-key="${escapeHtml(pair.pair_key)}">
            <td>
                <div class="book-pair-key">${escapeHtml(pair.matchup_key)}</div>
                <div class="book-pair-muted">slot ${pair.pair_slot} · ${escapeHtml(orders)}</div>
                <div class="book-pair-muted">${gameIds}</div>
                ${sfen}
            </td>
            <td>${statusBadge(pair.measurement_status)}</td>
            <td>${pair.same_sfen ? 'yes' : 'no'}</td>
            <td>${pair.matched_prefix_plies}</td>
            <td>${escapeHtml(formatFirstDiff(pair))}</td>
            <td>${formatPercent(pair.prefix_match_rate)}</td>
            <td>${formatSide(pair.left, 'L')}</td>
            <td>${formatSide(pair.right, 'R')}</td>
        </tr>`;
}

export function renderBookPairs(payload: BookPairPayload, container: HTMLElement): void {
    if (!payload.pairs.length) {
        const note = payload.note ? `<p class="book-note">${escapeHtml(payload.note)}</p>` : '';
        container.innerHTML = `${note}<div class="book-empty">No paired book prefix diagnostics recorded for this run.</div>`;
        return;
    }
    const note = payload.note ? `<p class="book-note">${escapeHtml(payload.note)}</p>` : '';
    const rows = payload.pairs.map(renderPairRow).join('');
    container.innerHTML = `
        ${note}
        ${renderPairSummary(payload)}
        <div class="book-pair-table-wrap">
            <table class="book-pair-table">
                <thead>
                    <tr>
                        <th>Pair</th>
                        <th>Status</th>
                        <th>same_sfen</th>
                        <th>Matched</th>
                        <th>First diff</th>
                        <th>Match%</th>
                        <th>Left</th>
                        <th>Right</th>
                    </tr>
                </thead>
                <tbody>${rows}</tbody>
            </table>
        </div>`;
}

function urlOutOfBookMode(owner: BookWindow): 'off' | 'bounded' | 'full' {
    try {
        const value = new URLSearchParams(owner.location.search).get('out_of_book')?.trim().toLowerCase();
        if (value === 'full') return 'full';
        if (value === '1' || value === 'true' || value === 'yes' || value === 'on') return 'bounded';
        return 'off';
    } catch {
        return 'off';
    }
}

function outOfBookQuery(owner: BookWindow): string {
    const urlMode = urlOutOfBookMode(owner);
    const toggle = owner.document.getElementById('bookOutOfBookToggle');
    if (toggle instanceof HTMLInputElement) {
        if (!toggle.checked) {
            return '';
        }
        return urlMode === 'full' ? '?out_of_book=full' : '?out_of_book=1';
    }
    if (urlMode === 'full') return '?out_of_book=full';
    if (urlMode === 'bounded') return '?out_of_book=1';
    return '';
}

async function fetchSummary(owner: BookWindow): Promise<void> {
    const core = owner.DashboardCore;
    if (!core) {
        throw new Error('DashboardCore must be initialized before Book module');
    }
    const container = owner.document.getElementById('bookSummaryContainer');
    if (!container) {
        return;
    }
    const apiBase = core.getApiBase();
    const query = outOfBookQuery(owner);
    const payload = await requestJson<BookSummaryPayload>(`${apiBase}/api/book/summary${query}`, {
        cache: 'no-cache',
    });
    renderBookSummary(payload, container);
}

async function fetchPairs(owner: BookWindow): Promise<void> {
    const core = owner.DashboardCore;
    if (!core) {
        throw new Error('DashboardCore must be initialized before Book module');
    }
    const container = owner.document.getElementById('bookPairsContainer');
    if (!container) {
        return;
    }
    const apiBase = core.getApiBase();
    const query = outOfBookQuery(owner);
    const payload = await requestJson<BookPairPayload>(`${apiBase}/api/book/pairs${query}`, {
        cache: 'no-cache',
    });
    renderBookPairs(payload, container);
}

export function installBookModule(owner: BookWindow = defaultWindow): DashboardBookApi {
    if (owner.DashboardBook) {
        return owner.DashboardBook;
    }

    const state = {
        active: false,
        timerId: null as number | null,
        toggleWired: false,
        viewWired: false,
        view: DEFAULT_BOOK_VIEW,
    };

    function wireToggle(): void {
        if (state.toggleWired) {
            return;
        }
        const toggle = owner.document.getElementById('bookOutOfBookToggle');
        if (!(toggle instanceof HTMLInputElement)) {
            return;
        }
        state.toggleWired = true;
        if (urlOutOfBookMode(owner) !== 'off') {
            toggle.checked = true;
        }
        toggle.addEventListener('change', () => {
            api.refresh();
        });
    }

    function applyView(view: BookModuleView): void {
        state.view = view;
        const buttons = owner.document.querySelectorAll<HTMLButtonElement>('[data-book-view]');
        buttons.forEach((button) => {
            const isActive = button.dataset.bookView === view;
            button.classList.toggle('active', isActive);
            button.setAttribute('aria-selected', isActive ? 'true' : 'false');
            button.tabIndex = isActive ? 0 : -1;
        });
        const summary = owner.document.getElementById('bookSummaryContainer');
        const pairs = owner.document.getElementById('bookPairsContainer');
        summary?.toggleAttribute('hidden', view !== 'summary');
        pairs?.toggleAttribute('hidden', view !== 'pairs');
    }

    function toBookView(value: string | undefined): BookModuleView | null {
        if (value === 'summary' || value === 'pairs') {
            return value;
        }
        return null;
    }

    function wireViewTabs(): void {
        if (state.viewWired) {
            return;
        }
        const buttons = Array.from(owner.document.querySelectorAll<HTMLButtonElement>('[data-book-view]'));
        if (!buttons.length) {
            return;
        }
        state.viewWired = true;
        buttons.forEach((button) => {
            const view = toBookView(button.dataset.bookView);
            if (!view) {
                return;
            }
            button.addEventListener('click', () => {
                if (state.view === view) {
                    return;
                }
                applyView(view);
                api.refresh();
            });
        });
        applyView(state.view);
    }

    const api: DashboardBookApi = {
        setActive(active: boolean) {
            state.active = !!active;
            if (state.active) {
                wireToggle();
                wireViewTabs();
                api.refresh();
                if (state.timerId == null) {
                    state.timerId = Number(
                        owner.setInterval(() => {
                            api.refresh();
                        }, BOOK_REFRESH_INTERVAL_MS),
                    );
                }
            } else if (state.timerId != null) {
                owner.clearInterval(state.timerId);
                state.timerId = null;
            }
        },
        refresh() {
            if (state.view === 'pairs') {
                fetchPairs(owner).catch((error) => {
                    owner.DashboardCore?.showApiError('Book pairs failed', error);
                });
                return;
            }
            fetchSummary(owner).catch((error) => {
                owner.DashboardCore?.showApiError('Book summary failed', error);
            });
        },
        getState() {
            return { ...state };
        },
    };

    owner.DashboardBook = api;
    return api;
}
