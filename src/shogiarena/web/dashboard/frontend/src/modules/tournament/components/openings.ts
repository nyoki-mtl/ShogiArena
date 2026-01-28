import type { OpeningPlyHistogramBin } from '@/modules/tournament/types';
import { buildOpeningStatsDisplay, normalizeHistogram } from '@/modules/tournament/viewmodels/openings';

export interface OpeningStatsRowView {
    sfen: string;
    label: string;
    total: number;
    black: number;
    white: number;
    draw: number;
    blackRatio: number;
    whiteRatio: number;
    avgPlies: number | null;
    basePlies: number;
}

export interface OpeningStatsTableRenderParams {
    rows: readonly OpeningStatsRowView[];
    sortState: { key: string | null; direction: string | null };
    escapeHtml(value: unknown): string;
    limit?: number;
}

export interface OpeningStatsTableRenderResult {
    html: string;
    limitedRows: OpeningStatsRowView[];
}

export function renderOpeningStatsTableMarkup({
    rows,
    sortState,
    escapeHtml,
    limit,
}: OpeningStatsTableRenderParams): OpeningStatsTableRenderResult {
    const maxRows = typeof limit === 'number' && limit > 0 ? Math.floor(limit) : rows.length;
    const limitedRows = rows.slice(0, maxRows);

    const buildHeaderCell = (key: string, label: string, extraClass = ''): string => {
        const isActive = sortState.key === key && !!sortState.direction;
        const ariaSort = isActive ? (sortState.direction === 'asc' ? 'ascending' : 'descending') : 'none';
        const cls = ['sortable', extraClass].filter(Boolean).join(' ');
        return `<th data-sort-key="${key}" aria-sort="${ariaSort}" class="${cls}">${label}</th>`;
    };

    let html = '';
    html += '<table class="standings-table opening-stats-table" aria-label="Opening statistics table">';
    html +=
        '<thead><tr>' +
        '<th class="standings-rank group-engine">#</th>' +
        buildHeaderCell('opening', 'Opening', 'opening-group-label') +
        buildHeaderCell('plies', 'Plies', 'numeric opening-group-plies') +
        buildHeaderCell('games', 'Games', 'numeric opening-group-games') +
        buildHeaderCell('wins', 'Black Wins', 'numeric opening-group-games') +
        buildHeaderCell('draws', 'Draws', 'numeric opening-group-games') +
        buildHeaderCell('losses', 'White Wins', 'numeric opening-group-games') +
        buildHeaderCell(
            'blackWin',
            '<span title="Black-side win ratio (draw = 0.5)">B Win Ratio</span>',
            'numeric opening-group-ratio',
        ) +
        buildHeaderCell(
            'whiteWin',
            '<span title="White-side win ratio (draw = 0.5)">W Win Ratio</span>',
            'numeric opening-group-ratio',
        ) +
        '</tr></thead><tbody>';

    html += limitedRows
        .map((row, idx) => {
            const view = buildOpeningStatsDisplay(row, idx);
            const labelEsc = escapeHtml(view.label);
            const sfenEsc = escapeHtml(view.sfen);
            return (
                `<tr class="standings-row" data-sfen="${sfenEsc}">` +
                `<td class="standings-rank group-engine">${view.rank}</td>` +
                `<td class="opening-name opening-group-label" data-sfen="${sfenEsc}" data-label="${labelEsc}" title="Preview position">${labelEsc}</td>` +
                `<td class="numeric opening-group-plies opening-plies" data-action="opening-ply-stats" title="Show ply-length details">${escapeHtml(view.plyDisplay)}</td>` +
                `<td class="numeric opening-group-games" data-action="opening-engine-stats" data-metric="games" title="Show per-engine breakdown">${view.total}</td>` +
                `<td class="numeric opening-group-games" data-action="opening-engine-stats" data-metric="games" title="Show per-engine breakdown">${view.black}</td>` +
                `<td class="numeric opening-group-games" data-action="opening-engine-stats" data-metric="games" title="Show per-engine breakdown">${view.draw}</td>` +
                `<td class="numeric opening-group-games" data-action="opening-engine-stats" data-metric="games" title="Show per-engine breakdown">${view.white}</td>` +
                `<td class="numeric opening-group-ratio" data-action="opening-engine-stats" data-metric="ratio" title="Show per-engine breakdown">${escapeHtml(view.blackWinRatioDisplay)}</td>` +
                `<td class="numeric opening-group-ratio" data-action="opening-engine-stats" data-metric="ratio" title="Show per-engine breakdown">${escapeHtml(view.whiteWinRatioDisplay)}</td>` +
                '</tr>'
            );
        })
        .join('');

    html += '</tbody></table>';

    return { html, limitedRows };
}

export interface OpeningEngineSummaryView {
    name: string;
    total: number;
    score: number | null;
    blackScore: number | null;
    whiteScore: number | null;
    overall: { wins: number; losses: number; draws: number };
    black: { games: number; wins: number; losses: number; draws: number };
    white: { games: number; wins: number; losses: number; draws: number };
}

export interface OpeningEngineStatsPaneParams {
    row: {
        label: string;
        sfen: string;
        engines: OpeningEngineSummaryView[];
    };
    escapeHtml(value: unknown): string;
    formatRecordTriplet(value: unknown): string;
    formatWinRatio(value: number | null | undefined): string;
}

export function renderOpeningEngineStatsPane({
    row,
    escapeHtml,
    formatRecordTriplet,
    formatWinRatio,
}: OpeningEngineStatsPaneParams): string {
    const labelEsc = escapeHtml(row.label || (row.sfen === 'startpos' ? 'startpos' : row.sfen));
    const rowsHtml = row.engines.length
        ? row.engines
              .map((engine) => {
                  const nameEsc = escapeHtml(engine.name);
                  const totalCell = `${engine.total} (${formatRecordTriplet(engine.overall)})`;
                  const blackCell = `${engine.black.games} (${formatRecordTriplet(engine.black)})`;
                  const whiteCell = `${engine.white.games} (${formatRecordTriplet(engine.white)})`;
                  const scoreCell = formatWinRatio(engine.score);
                  const blackScoreCell = formatWinRatio(engine.blackScore);
                  const whiteScoreCell = formatWinRatio(engine.whiteScore);
                  return `
                      <tr>
                          <td class="engine-name" style="font-weight:600;">${nameEsc}</td>
                          <td class="numeric">${escapeHtml(totalCell)}</td>
                          <td class="numeric">${escapeHtml(scoreCell)}</td>
                          <td class="numeric">${escapeHtml(blackCell)}</td>
                          <td class="numeric">${escapeHtml(blackScoreCell)}</td>
                          <td class="numeric">${escapeHtml(whiteCell)}</td>
                          <td class="numeric">${escapeHtml(whiteScoreCell)}</td>
                      </tr>
                  `;
              })
              .join('')
        : '<tr><td colspan="7" class="subtle">No per-engine data available for this opening.</td></tr>';

    return `
        <div class="details-pane theme-stats opening-engine-stats-pane">
            <div class="title">${labelEsc} — Engine Breakdown</div>
            <table class="opt-table opening-engine-stats-table">
                <thead>
                    <tr>
                        <th>Engine</th>
                        <th class="numeric">Total (W-D-L)</th>
                        <th class="numeric">Win Ratio</th>
                        <th class="numeric">Black (W-D-L)</th>
                        <th class="numeric">B Win Ratio</th>
                        <th class="numeric">White (W-D-L)</th>
                        <th class="numeric">W Win Ratio</th>
                    </tr>
                </thead>
                <tbody>${rowsHtml}</tbody>
            </table>
        </div>
    `;
}

export interface OpeningPlyStatsPaneParams {
    row: {
        label: string;
        sfen: string;
        basePlies: number;
        plyStats: {
            samples: number;
            averagePlies: number | null;
            minPlies: number | null;
            maxPlies: number | null;
            stdevPlies: number;
            histogram: OpeningPlyHistogramBin[];
        } | null;
    };
    escapeHtml(value: unknown): string;
    resolveMaxMovesToDraw(): number;
    defaultMaxMovesToDraw: number;
    gaussianDensity(x: number, mean: number, stdev: number): number;
    hashString(value: string): number;
}

export function renderOpeningPlyStatsPane({
    row,
    escapeHtml,
    resolveMaxMovesToDraw,
    defaultMaxMovesToDraw,
    gaussianDensity,
    hashString,
}: OpeningPlyStatsPaneParams): string {
    const labelEsc = escapeHtml(row.label || (row.sfen === 'startpos' ? 'startpos' : row.sfen));
    const plyStats = row.plyStats;
    const samples = Number(plyStats?.samples ?? 0);
    const basePlies = Number.isFinite(row.basePlies) ? Number(row.basePlies) : 0;
    const averagePliesRaw = plyStats?.averagePlies;
    const averagePlies = Number.isFinite(averagePliesRaw) ? Number(averagePliesRaw) : null;
    const totalPlies = averagePlies !== null ? basePlies + averagePlies : null;
    const stdevPliesRaw = plyStats?.stdevPlies;
    const stdevPlies = Number.isFinite(stdevPliesRaw) ? Number(stdevPliesRaw) : null;

    const buildChart = (): string => {
        const histogram: OpeningPlyHistogramBin[] = normalizeHistogram(plyStats?.histogram);
        if (!histogram.length || samples === 0) {
            return '<div class="subtle">Not enough finished games to build a ply-count distribution.</div>';
        }

        const limitValueRaw = resolveMaxMovesToDraw();
        const limitValue = Number.isFinite(limitValueRaw) && limitValueRaw > 0 ? limitValueRaw : defaultMaxMovesToDraw;
        const targetRange = Math.max(10, Math.ceil(limitValue));
        const binWidth = 10;
        const binCount = Math.max(1, Math.ceil(targetRange / binWidth));
        const dataWidth = binCount * binWidth;
        const bins = new Array(binCount).fill(0);
        histogram.forEach((item) => {
            const value = Number(item.bucketValue);
            const count = Number(item.count) || 0;
            if (!Number.isFinite(value) || count <= 0) return;
            const clamped = Math.min(Math.max(value, 0), dataWidth);
            const idx = Math.min(binCount - 1, Math.floor(clamped / binWidth));
            bins[idx] += count;
        });

        const probabilities = bins.map((count) => (samples > 0 ? count / samples : 0));
        const maxProb = probabilities.length ? Math.max(...probabilities) : 0;
        if (maxProb <= 0) {
            return '<div class="subtle">Not enough finished games to build a ply-count distribution.</div>';
        }

        const marginLeft = 28;
        const marginBottom = 26;
        const topMargin = 16;
        const widthScale = 2.0;
        const heightScale = 2.0;
        const chartHeight = Math.round(100 * heightScale);
        const chartWidth = dataWidth * widthScale;
        const svgWidth = marginLeft + chartWidth + 24;
        const svgHeight = topMargin + chartHeight + marginBottom;
        const baselineY = topMargin + chartHeight;
        const maxDisplayProb = 1.05;
        const probToPx = chartHeight / maxDisplayProb;

        const gradientId = `moveLengthBarGradient-${Math.abs(hashString(String(row.sfen || ''))) % 100000}`;

        const barElements = probabilities
            .map((prob, idx) => {
                const capped = Math.min(maxDisplayProb, Math.max(0, prob));
                const barHeight = capped * probToPx;
                const x = marginLeft + idx * binWidth * widthScale;
                const y = baselineY - barHeight;
                const start = idx * binWidth;
                const end = start + binWidth;
                const percent = (prob * 100).toFixed(1);
                return `
                    <rect class="move-length-svg-bar" x="${(x + 0.5).toFixed(2)}" y="${y.toFixed(2)}" width="${Math.max(
                        1,
                        binWidth * widthScale - 1,
                    ).toFixed(2)}" height="${barHeight.toFixed(2)}" fill="url(#${gradientId})">
                        <title>${start}–${end} plies: ${percent}% of games</title>
                    </rect>
                `;
            })
            .join('');

        let curveElement = '';
        if (samples >= 3 && Number.isFinite(totalPlies) && Number.isFinite(stdevPlies) && stdevPlies > 0) {
            const step = 1;
            const points = [];
            for (let x = 0; x <= dataWidth; x += step) {
                const density = gaussianDensity(x, totalPlies, stdevPlies);
                const probDensity = Math.min(maxDisplayProb, density * binWidth);
                const y = baselineY - probDensity * probToPx;
                points.push(`${(marginLeft + x * widthScale).toFixed(2)},${y.toFixed(2)}`);
            }
            if (points.length >= 2) {
                curveElement = `<polyline class="move-length-svg-curve" points="${points.join(' ')}" />`;
            }
        }

        const muDisplay = Number.isFinite(totalPlies) ? totalPlies.toFixed(1) : null;
        const sigmaDisplay = Number.isFinite(stdevPlies) && stdevPlies > 0 ? stdevPlies.toFixed(1) : null;
        const statsPieces: string[] = [];
        if (muDisplay !== null) statsPieces.push(`μ ≈ ${muDisplay}`);
        if (sigmaDisplay !== null) statsPieces.push(`σ ≈ ${sigmaDisplay}`);
        const legendEntries = [
            '<span class="legend-entry"><span class="legend-swatch legend-hist"></span>Observed</span>',
        ];
        if (curveElement && statsPieces.length) {
            legendEntries.push(
                `<span class="legend-entry"><span class="legend-swatch legend-normal"></span>Normal fit (${statsPieces
                    .map((item) => escapeHtml(item))
                    .join(', ')})</span>`,
            );
        } else if (curveElement) {
            legendEntries.push(
                '<span class="legend-entry"><span class="legend-swatch legend-normal"></span>Normal fit</span>',
            );
        } else if (statsPieces.length) {
            legendEntries.push(
                `<span class="legend-entry legend-info">${statsPieces
                    .map((item) => escapeHtml(item))
                    .join(', ')}</span>`,
            );
        }
        legendEntries.push(`<span class="legend-entry legend-meta">n = ${escapeHtml(String(samples))}</span>`);
        const legendHtml = `<div class="move-length-legend">${legendEntries.join('')}</div>`;

        const yTicks: string[] = [];
        for (let v = 0; v <= 1.0001; v += 0.2) {
            const scaled = Math.min(maxDisplayProb, v);
            const y = baselineY - scaled * probToPx;
            yTicks.push(
                `<line class="move-length-svg-grid" x1="${marginLeft}" y1="${y.toFixed(2)}" x2="${
                    marginLeft + chartWidth
                }" y2="${y.toFixed(2)}" />`,
            );
            yTicks.push(
                `<text class="move-length-svg-ylabel" x="${marginLeft - 4}" y="${(y + 3).toFixed(
                    2,
                )}" text-anchor="end">${v.toFixed(1)}</text>`,
            );
        }

        const xTicks: string[] = [];
        for (let value = 0; value <= dataWidth; value += 10) {
            const x = marginLeft + value * widthScale;
            const tickLength = value % 50 === 0 ? 6 : 3;
            xTicks.push(
                `<line class="move-length-svg-axis-tick" x1="${x.toFixed(2)}" y1="${baselineY}" x2="${x.toFixed(
                    2,
                )}" y2="${baselineY + tickLength}" />`,
            );
            if (value % 50 === 0) {
                xTicks.push(
                    `<text class="move-length-svg-xlabel" x="${x.toFixed(2)}" y="${baselineY + 12}" text-anchor="middle">${value}</text>`,
                );
            }
        }

        return `
            <div class="move-length-chart" role="img" aria-label="Normalized histogram of total ply counts">
                <svg class="move-length-svg" width="${svgWidth}" height="${svgHeight}" viewBox="0 0 ${svgWidth} ${svgHeight}" preserveAspectRatio="xMinYMin meet">
                    <defs>
                        <linearGradient id="${gradientId}" x1="0" x2="0" y1="0" y2="1">
                            <stop offset="0%" stop-color="#38bdf8" />
                            <stop offset="100%" stop-color="#2563eb" />
                        </linearGradient>
                    </defs>
                    <rect class="move-length-svg-background" x="${marginLeft}" y="${topMargin}" width="${chartWidth}" height="${chartHeight}" />
                    ${yTicks.join('')}
                    <line class="move-length-svg-axis" x1="${marginLeft}" y1="${baselineY}" x2="${
                        marginLeft + chartWidth
                    }" y2="${baselineY}" />
                    ${xTicks.join('')}
                    ${barElements}
                    ${curveElement}
                </svg>
                ${legendHtml}
            </div>
        `;
    };

    if (samples === 0) {
        return `
            <div class="details-pane theme-time opening-ply-stats-pane">
                <div class="title">${labelEsc} — Ply Length Summary</div>
                <div class="subtle">No completed games for this opening yet.</div>
            </div>
        `;
    }

    const chart = buildChart();

    return `
        <div class="details-pane theme-time opening-ply-stats-pane">
            <div class="title">${labelEsc} — Ply Length Summary</div>
            ${chart || '<div class="subtle">Not enough finished games to build a ply-count distribution.</div>'}
        </div>
    `;
}
