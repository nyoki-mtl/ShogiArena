import {
    average,
    computeIndexTicks,
    computeNiceTicks,
    drawLineSeries,
    formatTickLabel,
    prepareCanvasSurface,
    resolveDomainCount,
    stdDev,
} from './convergence-state';
import {
    DELTA_HISTORY_CANVAS_ID,
    DELTA_HISTORY_META_ID,
    drawCanvasPlaceholder,
    updateMetaText,
} from './convergence-layout';
import { SPSA_COLOR_ACTUAL, SPSA_COLOR_MEAN } from './palette';
import {
    bindChartInteractivity,
    focusVariant,
    type InteractiveChartPoint,
    clearChartInteractivity,
} from './chart-interaction';

export type DeltaNormChartPoint = InteractiveChartPoint & {
    series: 'delta-norm' | 'mean-vector';
};

export function renderDeltaNormHistoryChartNumeric(
    deltaSeries: readonly number[],
    directionalSeries: readonly number[],
    context?: {
        domainSize?: number | null;
        windowSize?: number | null;
        metaLabel?: string | null;
    },
): void {
    const canvas = document.getElementById(DELTA_HISTORY_CANVAS_ID) as HTMLCanvasElement | null;
    if (!canvas) return;
    const deltaSamples = deltaSeries.filter((value) => Number.isFinite(value)) as number[];
    const directionalSamples = directionalSeries.filter((value) => Number.isFinite(value)) as number[];
    const xIndices = deltaSeries.map((_, index) => index + 1);
    const directionalIndices = directionalSeries.map((_, index) => index + 1);
    const lastIndex = Math.max(
        xIndices.length ? xIndices[xIndices.length - 1] : 0,
        directionalIndices.length ? directionalIndices[directionalIndices.length - 1] : 0,
    );
    const domainBase = lastIndex > 0 ? lastIndex + 1 : 1;
    const domainCount = resolveDomainCount(domainBase, context?.domainSize ?? null);
    const domainMaxIndex = Math.max(0, domainCount - 1);
    const xTickValues = computeIndexTicks(domainCount, 6);
    const windowLabel =
        typeof context?.windowSize === 'number' && Number.isFinite(context.windowSize)
            ? ` (win=${Math.round(context.windowSize)})`
            : '';
    let metaLabel = context?.metaLabel ?? '';
    if (!metaLabel) {
        const metaSegments: string[] = [];
        if (deltaSamples.length) {
            metaSegments.push(`‖Δ‖ μ ${average(deltaSamples).toFixed(4)} · σ ${stdDev(deltaSamples).toFixed(4)}`);
        }
        if (directionalSamples.length) {
            const latestDirectional = directionalSamples[directionalSamples.length - 1];
            metaSegments.push(`‖meanΔ‖${windowLabel} ${latestDirectional.toFixed(4)}`);
        }
        const sampleCount = Math.max(deltaSamples.length, directionalSamples.length);
        metaLabel = metaSegments.length ? `Last ${sampleCount} updates · ${metaSegments.join(' · ')}` : '';
    }
    updateMetaText(DELTA_HISTORY_META_ID, metaLabel);
    if (!deltaSamples.length && !directionalSamples.length) {
        drawCanvasPlaceholder(canvas, '', {
            yRange: [-1, 1],
            pointCount: domainCount,
            formatXLabel: (variant) => `#${variant}`,
        });
        clearChartInteractivity(canvas, '.convergence-card');
        return;
    }

    const surface = prepareCanvasSurface(canvas);
    if (!surface) return;
    const { ctx, width, height } = surface;
    const padding = { top: 24, right: 20, bottom: 32, left: 52 };
    const plotWidth = Math.max(1, width - (padding.left + padding.right));
    const plotHeight = Math.max(1, height - (padding.top + padding.bottom));
    const combinedSamples = [...deltaSamples, ...directionalSamples];
    let minValue = combinedSamples.length ? Math.min(...combinedSamples) : -1;
    let maxValue = combinedSamples.length ? Math.max(...combinedSamples) : 1;
    if (maxValue === minValue) {
        maxValue += 1;
        minValue -= 1;
    }

    const yTickInfo = computeNiceTicks(minValue, maxValue, 5);
    minValue = yTickInfo.min;
    maxValue = yTickInfo.max;
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

    const deltaLinePoints = drawLineSeries(ctx, {
        values: deltaSeries,
        color: SPSA_COLOR_ACTUAL,
        originX,
        originY,
        plotWidth,
        plotHeight,
        minValue,
        maxValue,
        domainMaxIndex,
        xIndices,
    });

    const directionalLinePoints = drawLineSeries(ctx, {
        values: directionalSeries,
        color: SPSA_COLOR_MEAN,
        originX,
        originY,
        plotWidth,
        plotHeight,
        minValue,
        maxValue,
        domainMaxIndex,
        xIndices: directionalIndices,
    });

    drawLegend(ctx, {
        x: originX,
        y: padding.top * 0.6,
        items: [
            { color: SPSA_COLOR_ACTUAL, label: '‖Δ‖' },
            { color: SPSA_COLOR_MEAN, label: '‖meanΔ‖' },
        ],
    });

    const interactivePoints: DeltaNormChartPoint[] = [];

    const pushInteractivePoint = (
        point: { x: number; y: number; sourceIndex: number; displayValue: number },
        series: DeltaNormChartPoint['series'],
    ) => {
        const displayVariant = Number.isFinite(point.displayValue)
            ? Math.max(0, Math.round(point.displayValue))
            : Math.max(0, point.sourceIndex + 1);
        const variantIdx = displayVariant > 0 ? displayVariant - 1 : 0;
        interactivePoints.push({
            x: point.x,
            y: point.y,
            variantIdx,
            displayVariant,
            series,
        });
    };

    deltaLinePoints.forEach((point) => {
        pushInteractivePoint(point, 'delta-norm');
    });
    directionalLinePoints.forEach((point) => {
        pushInteractivePoint(point, 'mean-vector');
    });

    bindChartInteractivity(canvas, interactivePoints, {
        containerSelector: '.convergence-card',
        tooltipRenderer: (point) => {
            const variantIndexZeroBased = Math.max(0, point.variantIdx);
            const rawValue =
                variantIndexZeroBased < deltaSeries.length ? deltaSeries[variantIndexZeroBased] : Number.NaN;
            const directionalValue =
                variantIndexZeroBased < directionalSeries.length
                    ? directionalSeries[variantIndexZeroBased]
                    : Number.NaN;
            const rawLabel = Number.isFinite(rawValue) ? rawValue.toFixed(4) : 'n/a';
            const directionalLabel = Number.isFinite(directionalValue) ? directionalValue.toFixed(4) : 'n/a';
            return `
                <div><strong>Variant</strong><span>#${point.displayVariant}</span></div>
                <div><strong>‖Δ‖</strong><span>${rawLabel}</span></div>
                <div><strong>‖meanΔ‖</strong><span>${directionalLabel}</span></div>
            `;
        },
        onPointSelected: (point) => focusVariant(point.variantIdx, { navigateToUpdates: true }),
    });
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

    if (minValue < 0 && maxValue > 0) {
        const zeroRatio = (0 - minValue) / yRange;
        const zeroY = originY - plotHeight * zeroRatio;
        ctx.strokeStyle = '#cbd5f5';
        ctx.setLineDash([4, 4]);
        ctx.beginPath();
        ctx.moveTo(originX, zeroY);
        ctx.lineTo(originX + plotWidth, zeroY);
        ctx.stroke();
        ctx.setLineDash([]);
    }

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

function drawLegend(
    ctx: CanvasRenderingContext2D,
    options: {
        x: number;
        y: number;
        items: ReadonlyArray<{ color: string; label: string }>;
        itemSpacing?: number;
    },
): void {
    const { x, y, items, itemSpacing = 16 } = options;
    if (!items.length) {
        return;
    }

    ctx.save();
    ctx.font = '11px sans-serif';
    ctx.textBaseline = 'middle';
    ctx.textAlign = 'left';
    const textColor = '#334155';
    let cursorX = x;

    items.forEach((item) => {
        const swatchWidth = 22;
        const swatchHeight = 8;
        const labelOffset = 28;

        ctx.strokeStyle = item.color;
        ctx.lineWidth = 3;
        ctx.beginPath();
        ctx.moveTo(cursorX, y);
        ctx.lineTo(cursorX + swatchWidth, y);
        ctx.stroke();

        ctx.fillStyle = item.color;
        ctx.beginPath();
        ctx.arc(cursorX + swatchWidth / 2, y, swatchHeight / 2, 0, Math.PI * 2);
        ctx.fill();

        ctx.fillStyle = textColor;
        ctx.fillText(item.label, cursorX + labelOffset, y);

        const labelWidth = ctx.measureText(item.label).width;
        cursorX += labelOffset + labelWidth + itemSpacing;
    });

    ctx.restore();
}
