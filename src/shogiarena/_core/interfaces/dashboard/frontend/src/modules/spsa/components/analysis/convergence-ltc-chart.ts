import { escapeHtml } from '@/modules/shared/utils/html';
import type { SpsaLtcResultEntry } from '@/modules/spsa/types';
import {
    LTC_ELO_CANVAS_ID,
    LTC_ELO_META_ID,
    LTC_SUMMARY_ID,
    drawCanvasPlaceholder,
    updateMetaText,
} from './convergence-layout';
import {
    SPSA_COLOR_ACTUAL,
    SPSA_COLOR_ACTUAL_SOFT,
    SPSA_COLOR_NEGATIVE,
    SPSA_COLOR_PENDING,
    SPSA_COLOR_POSITIVE,
} from './palette';
import {
    bindChartInteractivity,
    clearChartInteractivity,
    focusVariant,
    type InteractiveChartPoint,
} from './chart-interaction';
import { resolveNumeric } from './shared';
import {
    computeIndexTicks,
    type computeNiceTicks,
    prepareCanvasSurface,
    resolveDomainCount,
    drawLineSeries,
    drawConfidenceBand,
    formatTickLabel,
} from './convergence-state';
import { normalizeLtcResults, resolveLtcYAxis, type ProcessedLtcRun } from './convergence-compute';

export type LtcChartPoint = InteractiveChartPoint & {
    kind: 'estimate' | 'sprt';
    value: number;
    status: string;
    mean?: number | null;
    sigma?: number | null;
    lower?: number | null;
    upper?: number | null;
    estimateSource?: string | null;
    compositionDepth?: number | null;
    delta?: number | null;
    effectiveGames?: number | null;
    sprtDiff?: number | null;
    sprtDecision?: string | null;
    sprtGames?: number | null;
    baselineMean?: number | null;
    probPositive?: number | null;
};

export function renderLtcEloChart(
    results: readonly SpsaLtcResultEntry[],
    summary: unknown,
    options: { domainSize?: number | null } = {},
): void {
    const canvas = document.getElementById(LTC_ELO_CANVAS_ID) as HTMLCanvasElement | null;
    if (!canvas) {
        return;
    }

    const processed = normalizeLtcResults(results);
    renderLtcEloChartProcessed(processed, summary, options);
}

export function renderLtcEloChartProcessed(
    processed: readonly ProcessedLtcRun[],
    summary: unknown,
    options: {
        domainSize?: number | null;
        yAxis?: { minValue: number; maxValue: number; yTickInfo: ReturnType<typeof computeNiceTicks> };
    } = {},
): void {
    const canvas = document.getElementById(LTC_ELO_CANVAS_ID) as HTMLCanvasElement | null;
    if (!canvas) {
        return;
    }

    if (!processed.length) {
        const domainCount = resolveDomainCount(0, options.domainSize ?? null);
        drawCanvasPlaceholder(canvas, '', {
            yRange: [-200, 200],
            pointCount: domainCount,
            formatXLabel: (index: number) => `#${index}`,
        });
        updateMetaText(LTC_ELO_META_ID, '');
        clearChartInteractivity(canvas, '.convergence-card');
        return;
    }

    const surface = prepareCanvasSurface(canvas);
    if (!surface) return;
    const { ctx, width, height } = surface;
    const padding = { top: 24, right: 20, bottom: 32, left: 52 };
    const plotWidth = Math.max(1, width - (padding.left + padding.right));
    const plotHeight = Math.max(1, height - (padding.top + padding.bottom));

    const xIndices = processed.map((item) => item.ordinal);
    const meanValues = processed.map((item) => item.bestMean ?? Number.NaN);
    const upperValues = processed.map((item) =>
        item.bestUpper !== null && item.bestUpper !== undefined ? (item.bestUpper as number) : Number.NaN,
    );
    const lowerValues = processed.map((item) =>
        item.bestLower !== null && item.bestLower !== undefined ? (item.bestLower as number) : Number.NaN,
    );

    const yAxis = options.yAxis ?? resolveLtcYAxis(processed);
    const { minValue, maxValue, yTickInfo } = yAxis;
    const lastOrdinal = processed[processed.length - 1]?.ordinal ?? 1;
    const domainBase = lastOrdinal > 0 ? lastOrdinal + 1 : 1;
    const domainCount = resolveDomainCount(domainBase, options.domainSize ?? null);
    const domainMaxIndex = Math.max(0, domainCount - 1);
    const xTickValues = computeIndexTicks(domainCount, 6);
    const originX = padding.left;
    const originY = padding.top + plotHeight;

    ctx.clearRect(0, 0, width, height);
    drawDeltaNormGrid(ctx, {
        originX,
        originY,
        plotWidth,
        plotHeight,
        minValue,
        maxValue,
        pointCount: domainCount,
        yTicks: yTickInfo.ticks,
        yStep: yTickInfo.step,
        xTicks: xTickValues,
    });

    drawConfidenceBand(ctx, {
        upperValues,
        lowerValues,
        originX,
        originY,
        plotWidth,
        plotHeight,
        minValue,
        maxValue,
        xIndices,
        domainMaxIndex,
        fillStyle: SPSA_COLOR_ACTUAL_SOFT,
        strokeStyle: SPSA_COLOR_ACTUAL,
        strokeWidth: 2,
    });

    const linePoints = drawLineSeries(ctx, {
        values: meanValues,
        color: SPSA_COLOR_ACTUAL,
        originX,
        originY,
        plotWidth,
        plotHeight,
        minValue,
        maxValue,
        xIndices,
        domainMaxIndex,
    });

    const interactivePoints: LtcChartPoint[] = [];
    const yRange = maxValue - minValue || 1;

    linePoints.forEach((point) => {
        const data = processed[point.sourceIndex];
        if (!data) return;
        const displayVariant = Number.isFinite(point.displayValue)
            ? Math.max(1, Math.round(point.displayValue))
            : point.sourceIndex + 1;
        const variantIdx = Number.isFinite(data.updateIdx)
            ? Math.max(0, Math.round(data.updateIdx as number))
            : Math.max(0, displayVariant - 1);
        interactivePoints.push({
            kind: 'estimate',
            x: point.x,
            y: point.y,
            variantIdx,
            displayVariant,
            value: data.bestMean ?? Number.NaN,
            status: data.status,
            mean: data.bestMean,
            sigma: data.bestSigma,
            lower: data.bestLower,
            upper: data.bestUpper,
            estimateSource: data.bestSource,
            compositionDepth: data.bestDepth,
            delta: data.deltaMean,
            effectiveGames: data.effectiveGames ?? data.totalGames ?? null,
            sprtDiff: data.sprtElo,
            sprtDecision: data.sprtDecision,
            sprtGames: data.sprtGames,
            baselineMean: data.bestPrior,
            probPositive: data.probPositive,
        });
    });

    processed.forEach((item) => {
        if (item.sprtPoint === null || !Number.isFinite(item.sprtPoint)) {
            return;
        }
        const ordinal = item.ordinal;
        const clampedIndex = Math.max(0, Math.min(domainMaxIndex, ordinal));
        const ratio = domainMaxIndex > 0 ? clampedIndex / domainMaxIndex : 0;
        const x = originX + plotWidth * ratio;
        const y = originY - plotHeight * ((item.sprtPoint - minValue) / yRange);
        const sprtDecision = (item.sprtDecision || '').toString().toLowerCase();
        const sprtStatus = (item.status || '').toString().toLowerCase();
        let sprtColor = SPSA_COLOR_PENDING;
        if (sprtDecision === 'accept_h1' || sprtStatus === 'passed') {
            sprtColor = SPSA_COLOR_POSITIVE;
        } else if (sprtDecision === 'accept_h0' || sprtStatus === 'failed') {
            sprtColor = SPSA_COLOR_NEGATIVE;
        }
        ctx.save();
        ctx.fillStyle = sprtColor;
        ctx.beginPath();
        ctx.arc(x, y, 4, 0, Math.PI * 2);
        ctx.fill();
        ctx.restore();

        const displayVariant = Math.max(1, Math.round(ordinal));
        const variantIdx = Number.isFinite(item.updateIdx)
            ? Math.max(0, Math.round(item.updateIdx as number))
            : Math.max(0, displayVariant - 1);

        interactivePoints.push({
            kind: 'sprt',
            x,
            y,
            variantIdx,
            displayVariant,
            value: item.sprtPoint,
            status: item.status,
            sprtDiff: item.sprtElo,
            sprtDecision: item.sprtDecision,
            sprtGames: item.sprtGames,
            baselineMean: item.bestPrior,
        });
    });

    if (interactivePoints.length) {
        bindChartInteractivity(canvas, interactivePoints, {
            containerSelector: '.convergence-card',
            tooltipRenderer: (point) => renderLtcTooltip(point),
            onPointSelected: (point) => focusVariant(point.variantIdx, { navigateToUpdates: true }),
        });
    } else {
        clearChartInteractivity(canvas, '.convergence-card');
    }

    const latest = processed[processed.length - 1];
    const metaParts: string[] = [`Last ${processed.length} LTC runs`];
    metaParts.push(`Best μ ${formatElo(latest.bestMean)}`);
    if (latest.bestSigma !== null && latest.bestSigma !== undefined && Number.isFinite(latest.bestSigma)) {
        metaParts.push(`σ ${formatNumber(Math.abs(latest.bestSigma), 1)}`);
    }
    metaParts.push(`Source ${describeSource(latest.bestSource)}`);
    const probPositiveLatest = resolveNumeric(latest.probPositive);
    if (probPositiveLatest !== null) {
        metaParts.push(`P(>0) ${formatPercent(probPositiveLatest)}`);
    }
    updateMetaText(LTC_ELO_META_ID, metaParts.join(' · '));

    updateLtcSummary(summary);
}

function renderLtcTooltip(point: LtcChartPoint): string {
    const variantLabel = `#${point.displayVariant}`;
    if (point.kind === 'sprt') {
        const diffLabel = formatElo(point.sprtDiff ?? null);
        const projectedLabel = formatElo(point.value);
        const baselineLabel = formatElo(point.baselineMean ?? null);
        const decisionLabel = describeDecision(point.sprtDecision);
        const gamesLabel = formatGames(point.sprtGames ?? null);
        return `
                <div><strong>Variant</strong><span>${escapeHtml(variantLabel)}</span></div>
                <div><strong>SPRT ΔElo</strong><span>${diffLabel}</span></div>
                <div><strong>Projected μ</strong><span>${projectedLabel}</span></div>
                <div><strong>Baseline μ</strong><span>${baselineLabel}</span></div>
                <div><strong>Decision</strong><span>${escapeHtml(decisionLabel)}</span></div>
                <div><strong>SPRT games</strong><span>${gamesLabel}</span></div>
            `;
    }
    const sigmaLabel = formatSigma(point.sigma ?? null);
    const bandLabel = formatBand(point.lower ?? null, point.upper ?? null);
    const sourceLabel = describeSource(point.estimateSource);
    const deltaLabel = formatElo(point.delta ?? null);
    const gamesLabel = formatGames(point.effectiveGames ?? null);
    const probLabel = formatPercent(point.probPositive ?? null);
    const statusLabel = describeStatus(point.status);
    return `
            <div><strong>Variant</strong><span>${escapeHtml(variantLabel)}</span></div>
            <div><strong>Best μ</strong><span>${formatElo(point.mean ?? null)}</span></div>
            <div><strong>1σ</strong><span>${bandLabel} (${sigmaLabel})</span></div>
            <div><strong>Source</strong><span>${escapeHtml(sourceLabel)}</span></div>
            <div><strong>Δ Elo</strong><span>${deltaLabel}</span></div>
            <div><strong>Effective games</strong><span>${gamesLabel}</span></div>
            <div><strong>P(Δ &gt; 0)</strong><span>${probLabel}</span></div>
            <div><strong>Status</strong><span>${escapeHtml(statusLabel)}</span></div>
        `;
}

function updateLtcSummary(summary: unknown): void {
    const summaryRecord = summary && typeof summary === 'object' ? (summary as Record<string, unknown>) : null;
    const summaryBest = summaryRecord?.best_estimate as Record<string, unknown> | null | undefined;
    const summaryElement = document.getElementById(LTC_SUMMARY_ID);
    if (!summaryElement) return;

    const summaryParts: string[] = [];
    if (summaryRecord?.status && typeof summaryRecord.status === 'string' && summaryRecord.status !== 'disabled') {
        summaryParts.push(`Status: ${summaryRecord.status}`);
    }
    const summaryWinrate = summaryRecord?.winrate as number | null | undefined;
    if (typeof summaryWinrate === 'number' && Number.isFinite(summaryWinrate)) {
        summaryParts.push(`Winrate: ${formatPercent(summaryWinrate)}`);
    }
    const bestMeanSummary = summaryBest ? resolveNumeric(summaryBest.mean as unknown) : null;
    const bestSigmaSummary = summaryBest ? resolveNumeric(summaryBest.sigma as unknown) : null;
    if (bestMeanSummary !== null) {
        summaryParts.push(`Best μ: ${formatElo(bestMeanSummary)}`);
    }
    if (bestSigmaSummary !== null) {
        summaryParts.push(`σ ${formatNumber(Math.abs(bestSigmaSummary), 1)}`);
    }
    const bestProbSummary = summaryBest ? resolveNumeric(summaryBest.prob_positive as unknown) : null;
    if (bestProbSummary !== null) {
        summaryParts.push(`P(>0): ${formatPercent(bestProbSummary)}`);
    }
    summaryElement.textContent = summaryParts.length > 0 ? summaryParts.join(' · ') : 'No LTC regression data';
}

function formatNumber(value: number, digits = 1): string {
    const factor = 10 ** digits;
    const rounded = Math.round(value * factor) / factor;
    const fixed = rounded.toFixed(digits);
    return fixed.replace(/\.0+$/, '').replace(/\.(\d*[1-9])0+$/, '.$1');
}

function formatElo(value: number | null | undefined): string {
    if (typeof value !== 'number' || !Number.isFinite(value)) {
        return 'n/a';
    }
    return `${value >= 0 ? '+' : ''}${formatNumber(value, 1)}`;
}

function formatSigma(value: number | null | undefined): string {
    if (typeof value !== 'number' || !Number.isFinite(value)) {
        return 'n/a';
    }
    return `±${formatNumber(Math.abs(value), 1)}`;
}

function formatBand(lower: number | null | undefined, upper: number | null | undefined): string {
    if (typeof lower !== 'number' || typeof upper !== 'number' || !Number.isFinite(lower) || !Number.isFinite(upper)) {
        return 'n/a';
    }
    return `${formatElo(lower)} → ${formatElo(upper)}`;
}

function formatGames(value: number | null | undefined): string {
    if (typeof value !== 'number' || !Number.isFinite(value)) {
        return 'n/a';
    }
    return Math.max(0, Math.round(value)).toString();
}

function formatPercent(value: number | null | undefined): string {
    if (typeof value !== 'number' || !Number.isFinite(value)) {
        return 'n/a';
    }
    return `${formatNumber(value * 100, 1)}%`;
}

function describeSource(source: string | null | undefined): string {
    switch (source) {
        case 'direct':
            return 'Direct vs initial';
        case 'composed':
            return 'Cumulative vs initial';
        case 'baseline':
            return 'Initial baseline';
        case 'bradley-terry':
            return 'Bradley-Terry';
        case null:
        case undefined:
            return 'Unknown';
        default:
            return source;
    }
}

function describeDecision(decision: string | null | undefined): string {
    if (!decision) {
        return 'n/a';
    }
    const normalized = decision.toLowerCase();
    if (normalized === 'accept_h1') return 'Accept H1';
    if (normalized === 'accept_h0') return 'Accept H0';
    if (normalized === 'continue') return 'Continue';
    return decision;
}

function describeStatus(status: string | null | undefined): string {
    switch (status) {
        case 'passed':
            return 'Passed';
        case 'failed':
            return 'Failed';
        case 'pending':
            return 'Pending';
        default:
            return status ? status : 'Unknown';
    }
}

function drawDeltaNormGrid(
    ctx: CanvasRenderingContext2D,
    options: {
        originX: number;
        originY: number;
        plotWidth: number;
        plotHeight: number;
        minValue: number;
        maxValue: number;
        pointCount: number;
        yTicks: readonly number[];
        yStep: number;
        xTicks: readonly number[];
        formatXLabel?: (index: number) => string;
    },
): void {
    const {
        originX,
        originY,
        plotWidth,
        plotHeight,
        minValue,
        maxValue,
        pointCount,
        yTicks,
        yStep,
        xTicks,
        formatXLabel,
    } = options;
    const yRange = maxValue - minValue || 1;

    ctx.strokeStyle = '#e5e7eb';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(originX, originY - plotHeight);
    ctx.lineTo(originX, originY);
    ctx.lineTo(originX + plotWidth, originY);
    ctx.stroke();

    const horizontalTicks = yTicks.length ? yTicks : [minValue, maxValue];
    ctx.font = '11px sans-serif';
    ctx.textAlign = 'right';
    ctx.textBaseline = 'middle';

    horizontalTicks.forEach((tick) => {
        if (!Number.isFinite(tick)) return;
        const ratio = (tick - minValue) / yRange;
        const y = originY - plotHeight * ratio;
        ctx.strokeStyle = '#f1f5f9';
        ctx.beginPath();
        ctx.moveTo(originX, y);
        ctx.lineTo(originX + plotWidth, y);
        ctx.stroke();
        ctx.fillStyle = '#64748b';
        ctx.fillText(formatTickLabel(tick, yStep), originX - 8, y);
    });

    const maxIndex = Math.max(0, pointCount - 1);
    const span = Math.max(1, maxIndex);
    if (xTicks.length) {
        ctx.textAlign = 'center';
        ctx.textBaseline = 'top';
        xTicks.forEach((tickValue) => {
            if (!Number.isFinite(tickValue)) return;
            const indexValue = Math.min(maxIndex, Math.max(0, Math.round(tickValue)));
            const ratio = span > 0 ? indexValue / span : 0;
            const x = originX + plotWidth * ratio;
            ctx.strokeStyle = '#f1f5f9';
            ctx.beginPath();
            ctx.moveTo(x, originY);
            ctx.lineTo(x, originY - plotHeight);
            ctx.stroke();
            const label = formatXLabel ? formatXLabel(indexValue) : `#${indexValue}`;
            ctx.fillStyle = '#64748b';
            ctx.fillText(label, x, originY + 8);
        });
    }
}
