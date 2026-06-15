import { requestJson } from '@/modules/shared/services/api';
import type { BookEntry, BookSummaryPayload, BookWdl, BookWindow, DashboardBookApi } from '../types';

const defaultWindow = window as BookWindow;

const BOOK_REFRESH_INTERVAL_MS = 8000;

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

function urlHasOutOfBook(owner: BookWindow): boolean {
    try {
        return new URLSearchParams(owner.location.search).get('out_of_book') === '1';
    } catch {
        return false;
    }
}

function isOutOfBookEnabled(owner: BookWindow): boolean {
    const toggle = owner.document.getElementById('bookOutOfBookToggle');
    if (toggle instanceof HTMLInputElement) {
        return toggle.checked;
    }
    // Fallback to the page URL (?out_of_book=1) when the toggle is absent.
    return urlHasOutOfBook(owner);
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
    const query = isOutOfBookEnabled(owner) ? '?out_of_book=1' : '';
    const payload = await requestJson<BookSummaryPayload>(`${apiBase}/api/book/summary${query}`, {
        cache: 'no-cache',
    });
    renderBookSummary(payload, container);
}

export function installBookModule(owner: BookWindow = defaultWindow): DashboardBookApi {
    if (owner.DashboardBook) {
        return owner.DashboardBook;
    }

    const state = {
        active: false,
        timerId: null as number | null,
        toggleWired: false,
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
        if (urlHasOutOfBook(owner)) {
            toggle.checked = true;
        }
        toggle.addEventListener('change', () => {
            api.refresh();
        });
    }

    const api: DashboardBookApi = {
        setActive(active: boolean) {
            state.active = !!active;
            if (state.active) {
                wireToggle();
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
