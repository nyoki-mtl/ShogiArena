import { computeIndexTicks, computeNiceTicks, formatTickLabel } from '@/modules/shared/utils/chart';
import type { SpsaDashboardState } from '@/modules/spsa/types';

import { SPSA_COLOR_ACTUAL } from './analysis/palette';

const CANVAS_ID = 'mobilityCanvas';
const VALUE_ID = 'mobilityCurrentValue';
const PADDING_LEFT = 52;
const PADDING_RIGHT = 20;
const PADDING_TOP = 24;
const PADDING_BOTTOM = 32;

export function renderMobility(state: SpsaDashboardState): void {
    const series = state.trend.gainAk ?? [];
    const variantSeries = state.trend.variantIndices ?? [];
    const latest = findLatestFinite(series);
    const valueNode = document.getElementById(VALUE_ID);
    if (valueNode) {
        valueNode.textContent = latest !== null ? formatValue(latest) : '-';
    }

    const canvas = document.getElementById(CANVAS_ID) as HTMLCanvasElement | null;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const paired: Array<{ variant: number; value: number }> = [];
    const finiteCount = Math.min(series.length, variantSeries.length || series.length);
    for (let index = 0; index < finiteCount; index += 1) {
        const value = series[index];
        const rawVariant = variantSeries[index];
        const variant = Number.isFinite(rawVariant) ? (rawVariant as number) : index + 1;
        if (!Number.isFinite(value) || !Number.isFinite(variant)) {
            continue;
        }
        paired.push({ variant: Math.round(variant), value: value as number });
    }

    if (!paired.length) {
        const surface = prepareSurface(canvas, ctx);
        if (!surface) return;
        const { width, height } = surface;
        ctx.clearRect(0, 0, width, height);
        const plotWidth = Math.max(1, width - (PADDING_LEFT + PADDING_RIGHT));
        const plotHeight = Math.max(1, height - (PADDING_TOP + PADDING_BOTTOM));
        const originX = PADDING_LEFT;
        const originY = height - PADDING_BOTTOM;
        const yTickInfo = computeNiceTicks(-1, 1, 6);
        const xTickOffsets = computeIndexTicks(1, 6);
        const domainStart = 0;
        const domainEnd = 1;
        const xTicks = xTickOffsets.map((offset) => domainStart + offset);
        drawMobilityGrid(ctx, {
            originX,
            originY,
            plotWidth,
            plotHeight,
            min: yTickInfo.min,
            max: yTickInfo.max,
            yTicks: yTickInfo.ticks,
            yStep: yTickInfo.step,
            xTicks,
            domainStart,
            domainEnd,
        });
        return;
    }

    const sortedPairs = paired.slice().sort((a, b) => a.variant - b.variant);
    let domainStart = sortedPairs[0]?.variant ?? 1;
    let domainEnd = sortedPairs[sortedPairs.length - 1]?.variant ?? domainStart;
    if (!Number.isFinite(domainStart)) {
        domainStart = 1;
    }
    if (!Number.isFinite(domainEnd)) {
        domainEnd = domainStart;
    }
    domainStart = Math.max(0, Math.min(domainStart, domainEnd) - 1);
    if (domainStart === domainEnd) {
        domainStart = Math.max(0, domainStart - 1);
        domainEnd = domainStart + 1;
    }

    const variantSpanCount = Math.max(1, domainEnd - domainStart + 1);
    const xTickOffsets = computeIndexTicks(variantSpanCount, 6);
    const xTicks = xTickOffsets
        .map((offset) => domainStart + offset)
        .filter((tick, index, array) => array.indexOf(tick) === index)
        .sort((a, b) => a - b);

    const surface = prepareSurface(canvas, ctx);
    if (!surface) return;
    const { width, height } = surface;
    ctx.clearRect(0, 0, width, height);

    const plotWidth = Math.max(1, width - (PADDING_LEFT + PADDING_RIGHT));
    const plotHeight = Math.max(1, height - (PADDING_TOP + PADDING_BOTTOM));
    const originX = PADDING_LEFT;
    const originY = height - PADDING_BOTTOM;
    const usableValues = sortedPairs.map((item) => item.value);
    const yTickInfo = usableValues.length
        ? computeNiceTicks(Math.min(...usableValues), Math.max(...usableValues), 6)
        : computeNiceTicks(-1, 1, 6);

    drawMobilityGrid(ctx, {
        originX,
        originY,
        plotWidth,
        plotHeight,
        yTicks: yTickInfo.ticks,
        yStep: yTickInfo.step,
        min: yTickInfo.min,
        max: yTickInfo.max,
        xTicks,
        domainStart,
        domainEnd,
    });

    drawMobilitySeries(ctx, {
        values: sortedPairs.map((item) => item.value),
        variants: sortedPairs.map((item) => item.variant),
        originX,
        originY,
        plotWidth,
        plotHeight,
        min: yTickInfo.min,
        max: yTickInfo.max,
        domainStart,
        domainEnd,
        color: SPSA_COLOR_ACTUAL,
    });
}

function prepareSurface(
    canvas: HTMLCanvasElement,
    ctx: CanvasRenderingContext2D,
): { width: number; height: number } | null {
    const rect = canvas.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    const width = rect.width || canvas.clientWidth || canvas.width || 300;
    const height = rect.height || canvas.clientHeight || canvas.height || 180;
    canvas.width = Math.max(1, Math.floor(width * dpr));
    canvas.height = Math.max(1, Math.floor(height * dpr));
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, width, height);
    return { width, height };
}

function findLatestFinite(values: readonly number[]): number | null {
    for (let i = values.length - 1; i >= 0; i -= 1) {
        const value = values[i];
        if (Number.isFinite(value)) {
            return value as number;
        }
    }
    return null;
}

function formatValue(value: number): string {
    return value >= 1 ? value.toFixed(2) : value.toFixed(4);
}

interface MobilityGridOptions {
    originX: number;
    originY: number;
    plotWidth: number;
    plotHeight: number;
    min: number;
    max: number;
    yTicks: readonly number[];
    yStep: number;
    xTicks: readonly number[];
    domainStart: number;
    domainEnd: number;
}

function drawMobilityGrid(ctx: CanvasRenderingContext2D, options: MobilityGridOptions): void {
    const { originX, originY, plotWidth, plotHeight, min, max, yTicks, yStep, xTicks, domainStart, domainEnd } =
        options;
    const yRange = max - min || 1;
    const normalizedStart = Number.isFinite(domainStart) ? Math.floor(domainStart) : 0;
    const normalizedEnd = Number.isFinite(domainEnd) ? Math.floor(domainEnd) : normalizedStart + 1;
    const span = Math.max(1, normalizedEnd - normalizedStart);

    ctx.strokeStyle = '#e5e7eb';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(originX, originY - plotHeight);
    ctx.lineTo(originX, originY);
    ctx.lineTo(originX + plotWidth, originY);
    ctx.stroke();

    ctx.font = '11px sans-serif';
    ctx.textAlign = 'right';
    ctx.textBaseline = 'middle';

    const horizontalTicks = yTicks.length ? yTicks : [min, max];
    horizontalTicks.forEach((tick) => {
        if (!Number.isFinite(tick)) return;
        const ratio = (tick - min) / yRange;
        const y = originY - plotHeight * ratio;
        ctx.strokeStyle = '#f1f5f9';
        ctx.beginPath();
        ctx.moveTo(originX, y);
        ctx.lineTo(originX + plotWidth, y);
        ctx.stroke();
        ctx.fillStyle = '#64748b';
        ctx.fillText(formatTickLabel(tick, yStep), originX - 8, y);
    });

    if (min < 0 && max > 0) {
        const zeroRatio = (0 - min) / yRange;
        const zeroY = originY - plotHeight * zeroRatio;
        ctx.strokeStyle = '#cbd5f5';
        ctx.setLineDash([4, 4]);
        ctx.beginPath();
        ctx.moveTo(originX, zeroY);
        ctx.lineTo(originX + plotWidth, zeroY);
        ctx.stroke();
        ctx.setLineDash([]);
    }

    ctx.textAlign = 'center';
    ctx.textBaseline = 'top';
    xTicks.forEach((tick) => {
        if (!Number.isFinite(tick)) return;
        const clampedValue = Math.min(normalizedEnd, Math.max(normalizedStart, Math.round(tick)));
        const ratio = span > 0 ? (clampedValue - normalizedStart) / span : 0;
        const x = originX + plotWidth * ratio;
        ctx.strokeStyle = '#f1f5f9';
        ctx.beginPath();
        ctx.moveTo(x, originY - plotHeight);
        ctx.lineTo(x, originY);
        ctx.stroke();

        ctx.fillStyle = '#64748b';
        ctx.fillText(`#${clampedValue}`, x, originY + 8);
    });
}

interface MobilitySeriesOptions {
    values: readonly number[];
    variants: readonly number[];
    originX: number;
    originY: number;
    plotWidth: number;
    plotHeight: number;
    min: number;
    max: number;
    domainStart: number;
    domainEnd: number;
    color: string;
}

function drawMobilitySeries(ctx: CanvasRenderingContext2D, options: MobilitySeriesOptions): void {
    const { values, variants, originX, originY, plotWidth, plotHeight, min, max, domainStart, domainEnd, color } =
        options;
    const yRange = max - min || 1;
    const normalizedStart = Number.isFinite(domainStart) ? Math.floor(domainStart) : 0;
    const normalizedEnd = Number.isFinite(domainEnd) ? Math.floor(domainEnd) : normalizedStart + 1;
    const domainSpan = Math.max(1, normalizedEnd - normalizedStart);
    const points: Array<{ x: number; y: number }> = [];

    values.forEach((value, index) => {
        const variant = variants[index];
        if (!Number.isFinite(value) || !Number.isFinite(variant)) {
            return;
        }
        const clampedVariant = Math.max(normalizedStart, Math.min(normalizedEnd, Math.round(variant)));
        const ratioX = domainSpan > 0 ? (clampedVariant - normalizedStart) / domainSpan : 0;
        const x = originX + plotWidth * ratioX;
        const ratioY = (value - min) / yRange;
        const y = originY - plotHeight * ratioY;
        points.push({ x, y });
    });

    if (!points.length) {
        return;
    }

    ctx.strokeStyle = color;
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(points[0].x, points[0].y);
    for (let idx = 1; idx < points.length; idx += 1) {
        ctx.lineTo(points[idx].x, points[idx].y);
    }
    ctx.stroke();

    if (points.length === 1) {
        ctx.fillStyle = color;
        ctx.beginPath();
        ctx.arc(points[0].x, points[0].y, 3, 0, Math.PI * 2);
        ctx.fill();
    }
}
