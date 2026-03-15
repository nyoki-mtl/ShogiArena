import { createDetailsPane, createEmptyState } from '@/modules/shared/components/elements';
import { gameResultWinner } from '@/modules/shared/utils/gameResult';
import type { JsonObject } from '@/types/shared';
import type { NormalizedTournamentSummary } from '@/modules/tournament/types';
import type { DashboardCoreState } from '@/types/dashboard';

export interface PentaDependencies {
    readonly doc: Document;
    readonly state: DashboardCoreState;
    readonly fetchGamesList: () => Promise<ReadonlyArray<JsonObject>>;
    readonly resolveSummarySnapshot: () => { normalized: NormalizedTournamentSummary };
}

export interface PentaController {
    buildPentaPane: () => HTMLElement;
    renderPentaHistogramNormalized: (container: Element | null, bins: Record<string, number> | null) => void;
    setupPentaUI: (name: string, container: Element, initialOpponent?: string | null) => Promise<void>;
    computePairPentanomial: (a: string, b: string) => Promise<{ pairs: number; bins: Record<string, number> }>;
}

function ensurePentaCache(state: DashboardCoreState): Map<string, unknown> {
    if (!(state.pentaCache instanceof Map)) {
        state.pentaCache = new Map();
    }
    return state.pentaCache as Map<string, unknown>;
}

export function createPentaController({
    doc,
    state,
    fetchGamesList,
    resolveSummarySnapshot,
}: PentaDependencies): PentaController {
    async function computePairPentanomial(
        a: string,
        b: string,
    ): Promise<{ pairs: number; bins: Record<string, number> }> {
        const key = [String(a), String(b)].sort().join('||');
        const cache = ensurePentaCache(state);
        const cached = cache.get(key) as { pairs: number; bins: Record<string, number> } | undefined;
        if (cached && typeof cached === 'object' && 'pairs' in cached && 'bins' in cached) {
            return cached;
        }

        const games = await fetchGamesList();
        if (!games.length) {
            return {
                pairs: 0,
                bins: { '2.0': 0, 1.5: 0, '1.0': 0, 0.5: 0, '0.0': 0 } as Record<string, number>,
            };
        }

        const bySfen = new Map<string, JsonObject[]>();
        for (const g of games) {
            const black = String(g.black_player || '').trim();
            const white = String(g.white_player || '').trim();
            if (!black || !white) continue;
            const sfen = String(g.initial_sfen || 'startpos');
            const isPair = (black === a && white === b) || (black === b && white === a);
            if (!isPair) continue;
            const list = bySfen.get(sfen) ?? [];
            list.push(g);
            bySfen.set(sfen, list);
        }

        const bins = { '2.0': 0, 1.5: 0, '1.0': 0, 0.5: 0, '0.0': 0 } as Record<string, number>;
        let pairs = 0;

        for (const [, list] of bySfen.entries()) {
            const gamesAB = list.filter((g) => String(g.black_player) === a && String(g.white_player) === b);
            const gamesBA = list.filter((g) => String(g.black_player) === b && String(g.white_player) === a);
            const matchCount = Math.min(gamesAB.length, gamesBA.length);
            for (let i = 0; i < matchCount; i += 1) {
                let score = 0;
                const resultAB = gameResultWinner(gamesAB[i].game_result);
                const resultBA = gameResultWinner(gamesBA[i].game_result);

                if (resultAB === 'black') score += 1;
                else if (resultAB === 'draw') score += 0.5;

                if (resultBA === 'white') score += 1;
                else if (resultBA === 'draw') score += 0.5;

                const bucket = score.toFixed(1);
                if (Object.hasOwn(bins, bucket)) {
                    bins[bucket] += 1;
                }
                pairs += 1;
            }
        }

        const result = { pairs, bins };
        cache.set(key, result);
        return result;
    }

    function renderPentaHistogramNormalized(container: Element | null, bins: Record<string, number> | null): void {
        if (!container) return;
        if (!bins) {
            container.replaceChildren(createEmptyState(doc, 'No data'));
            return;
        }

        const order = ['0.0', '0.5', '1.0', '1.5', '2.0'];
        const value = (k: string) => Number(bins[k] || 0);
        const total = order.reduce((acc, key) => acc + value(key), 0);
        const denominator = total > 0 ? total : 1;
        const maxRatio = Math.max(...order.map((key) => value(key) / denominator));
        const chartHeight = 140;

        const wrapper = doc.createElement('div');
        wrapper.className = 'flex items-end gap-3';

        const axis = doc.createElement('div');
        axis.className = 'flex h-[140px] flex-col justify-between pr-2 text-[0.78rem] text-dashboard-neutral/90';
        const ticks = [1.0, 0.75, 0.5, 0.25, 0.0];
        ticks.forEach((tick) => {
            const row = doc.createElement('div');
            row.className = 'flex items-center gap-1.5';
            const label = doc.createElement('div');
            label.className = 'w-7 text-right';
            label.textContent = tick.toFixed(2);
            const line = doc.createElement('div');
            line.className = 'h-px flex-1 bg-dashboard-border/70';
            row.append(label, line);
            axis.append(row);
        });
        wrapper.append(axis);

        const barsWrapper = doc.createElement('div');
        barsWrapper.className = 'flex items-end gap-2.5';

        order.forEach((bucket) => {
            const probability = value(bucket) / denominator;
            const heightPx = Math.round(probability * (maxRatio > 0 ? chartHeight / maxRatio : chartHeight));

            const column = doc.createElement('div');
            column.className = 'flex w-8 flex-col items-center';

            const barStack = doc.createElement('div');
            barStack.className = 'flex h-[140px] w-full items-end bg-transparent';
            barStack.title = `${bucket}: ${(probability * 100).toFixed(1)}%`;

            const fill = doc.createElement('div');
            fill.className = 'w-full rounded-t bg-dashboard-accent/80';
            fill.style.height = `${heightPx}px`;
            barStack.append(fill);

            const label = doc.createElement('div');
            label.className = 'mt-1 text-xs text-dashboard-muted';
            label.textContent = bucket;

            column.append(barStack, label);
            barsWrapper.append(column);
        });

        wrapper.append(barsWrapper);
        container.replaceChildren(wrapper);
    }

    const buildPentaPane = (): HTMLElement => {
        const pane = createDetailsPane(doc);

        const title = doc.createElement('div');
        title.className = 'title';
        const bold = doc.createElement('b');
        bold.textContent = 'Pentanomial by opponent';
        title.appendChild(bold);
        pane.appendChild(title);

        const split = doc.createElement('div');
        split.id = 'pentaSplit';
        split.className = 'flex items-start gap-3';
        pane.appendChild(split);

        const left = doc.createElement('div');
        left.id = 'pentaLeft';
        left.className = 'flex-1 min-w-[240px]';
        split.appendChild(left);

        const selectRow = doc.createElement('div');
        selectRow.className = 'mb-2 flex items-center justify-between gap-2';
        const select = doc.createElement('select');
        select.id = 'pentaSelect';
        select.className = 'select';
        selectRow.appendChild(select);
        left.appendChild(selectRow);

        const table = doc.createElement('table');
        table.id = 'pentaOppTable';
        table.className = 'table-compact';
        const thead = doc.createElement('thead');
        const headerRow = doc.createElement('tr');
        ['Opponent', 'W-D-L', 'ΔElo', 'LOS'].forEach((label) => {
            const th = doc.createElement('th');
            th.textContent = label;
            headerRow.appendChild(th);
        });
        thead.appendChild(headerRow);
        table.appendChild(thead);
        const tbody = doc.createElement('tbody');
        table.appendChild(tbody);
        left.appendChild(table);

        const right = doc.createElement('div');
        right.id = 'pentaRight';
        right.className = 'flex-1 min-w-[260px]';
        split.appendChild(right);

        const indicator = doc.createElement('div');
        indicator.id = 'pentaOppIndicator';
        indicator.className = 'mb-1 text-sm font-semibold text-dashboard-heading';
        right.appendChild(indicator);

        const pairs = doc.createElement('div');
        pairs.id = 'pentaPairs';
        pairs.className = 'subtle mb-2 text-xs text-dashboard-neutral';
        right.appendChild(pairs);

        const chart = doc.createElement('div');
        chart.id = 'pentaChart';
        chart.className = 'relative h-[180px] w-full';
        right.appendChild(chart);

        return pane;
    };

    const setupPentaUI = async (name: string, container: Element, initialOpponent?: string | null): Promise<void> => {
        const { normalized } = resolveSummarySnapshot();
        const engines = new Set<string>(normalized.engines.map(String));
        Object.keys(normalized.engineStats).forEach((engine) => {
            engines.add(String(engine));
        });
        engines.delete(String(name));

        const pairsLabel = container.querySelector<HTMLElement>('#pentaPairs');
        const leftTableBody = container.querySelector<HTMLTableSectionElement>('#pentaOppTable tbody');
        const chart = container.querySelector<HTMLElement>('#pentaChart');
        if (!leftTableBody || !chart) return;

        const pair = normalized.pairResults;
        const btd = normalized.btd;
        const covariance = btd?.raw?.rating_cov ?? null;

        const normalCdf = (z: number): number => {
            const mathErf = (Math as Math & { erf?: (x: number) => number }).erf;
            if (typeof mathErf === 'function') {
                return 0.5 * (1 + mathErf(z / Math.SQRT2));
            }
            const sign = z >= 0 ? 1 : -1;
            const x = Math.abs(z) / Math.SQRT2;
            const a1 = 0.254829592;
            const a2 = -0.284496736;
            const a3 = 1.421413741;
            const a4 = -1.453152027;
            const a5 = 1.061405429;
            const p = 0.3275911;
            const t = 1 / (1 + p * x);
            const y = 1 - ((((a5 * t + a4) * t + a3) * t + a2) * t + a1) * t * Math.exp(-x * x);
            return 0.5 * (1 + sign * y);
        };

        type PentaRow = {
            op: string;
            w: number;
            d: number;
            l: number;
            n: number;
            delta: string;
            los: string;
            inferred: boolean;
        };

        const entries: PentaRow[] = Array.from(engines).map((opponent) => {
            const record = pair[String(name)]?.[opponent] ?? { wins: 0, losses: 0, draws: 0 };
            const wins = Number(record.wins ?? 0);
            const losses = Number(record.losses ?? 0);
            const draws = Number(record.draws ?? 0);
            const total = wins + losses + draws;
            let deltaLabel = '-';
            let losLabel = '-';

            if (btd?.ratings && Object.hasOwn(btd.ratings, name) && Object.hasOwn(btd.ratings, opponent)) {
                const ratingA = btd.ratings[name];
                const ratingB = btd.ratings[opponent];
                const meanA = Number(ratingA?.mean ?? 0);
                const meanB = Number(ratingB?.mean ?? 0);
                const seA = Number(ratingA?.standardError ?? 0);
                const seB = Number(ratingB?.standardError ?? 0);
                const deltaElo = meanA - meanB;
                let variance = seA * seA + seB * seB;
                const covEntry = covariance && typeof covariance[name] === 'object' ? covariance[name] : null;
                if (covEntry && typeof covEntry[opponent] === 'number') {
                    variance = Math.max(0, variance - 2 * Number(covEntry[opponent]));
                }
                const seDelta = Math.sqrt(Math.max(0, variance));
                deltaLabel = (deltaElo >= 0 ? '+' : '') + Math.round(deltaElo);
                if (seDelta > 0) {
                    const z = deltaElo / seDelta;
                    losLabel = `${(normalCdf(z) * 100).toFixed(1)}%`;
                }
            }

            return {
                op: opponent,
                w: wins,
                d: draws,
                l: losses,
                n: total,
                delta: deltaLabel,
                los: losLabel,
                inferred: total === 0,
            } satisfies PentaRow;
        });

        entries.sort((a, b) => b.n - a.n || a.op.localeCompare(b.op));

        const rowsFragment = doc.createDocumentFragment();
        entries.forEach((row) => {
            const tr = doc.createElement('tr');
            if (row.inferred) tr.classList.add('inferred');
            tr.dataset.op = row.op;

            const nameCell = doc.createElement('td');
            nameCell.className = 'op-name';
            nameCell.textContent = row.op;

            const wdlCell = doc.createElement('td');
            wdlCell.textContent = `${row.w}-${row.d}-${row.l}`;

            const deltaCell = doc.createElement('td');
            deltaCell.textContent = row.delta;

            const losCell = doc.createElement('td');
            losCell.textContent = row.los;

            tr.append(nameCell, wdlCell, deltaCell, losCell);
            rowsFragment.appendChild(tr);
        });
        leftTableBody.replaceChildren(rowsFragment);

        const renderForOpponent = async (opponent: string | null): Promise<void> => {
            entries.forEach((entry) => {
                const tr = leftTableBody.querySelector<HTMLTableRowElement>(`tr[data-op="${CSS.escape(entry.op)}"]`);
                if (tr) tr.classList.toggle('selected', entry.op === opponent);
            });

            if (!opponent) {
                if (pairsLabel) pairsLabel.textContent = '';
                renderPentaHistogramNormalized(chart, null);
                state.expanded = { name, mode: 'penta', opponent: null } as unknown;
                return;
            }

            const result = await computePairPentanomial(String(name), opponent);
            if (pairsLabel) {
                pairsLabel.textContent = typeof result.pairs === 'number' ? `pairs=${result.pairs}` : '';
            }
            const indicator = container.querySelector<HTMLElement>('#pentaOppIndicator');
            if (indicator) {
                indicator.textContent = `Opponent: ${opponent}`;
                indicator.style.removeProperty('color');
            }
            renderPentaHistogramNormalized(chart, result ? result.bins : null);
            state.expanded = { name, mode: 'penta', opponent } as unknown;
        };

        leftTableBody.addEventListener('click', (event) => {
            const target = event.target as HTMLElement | null;
            const row = target?.closest<HTMLTableRowElement>('tr[data-op]');
            if (!row) return;
            const opponent = row.getAttribute('data-op') || '';
            void renderForOpponent(opponent);
        });

        leftTableBody.querySelectorAll<HTMLTableRowElement>('tr').forEach((tr) => {
            tr.style.cursor = 'pointer';
        });

        const defaultOpponent =
            initialOpponent && entries.some((entry) => entry.op === initialOpponent)
                ? initialOpponent
                : entries[0]
                  ? entries[0].op
                  : null;
        if (defaultOpponent) {
            await renderForOpponent(defaultOpponent);
        } else {
            renderPentaHistogramNormalized(chart, null);
        }
    };

    return {
        buildPentaPane,
        renderPentaHistogramNormalized,
        setupPentaUI,
        computePairPentanomial,
    } satisfies PentaController;
}
