import { computeIndexTicks, computeNiceTicks, formatTickLabel } from '@/modules/shared/utils/chart';

export type LineSeriesPoint = {
    x: number;
    y: number;
    sourceIndex: number;
    rawIndex: number;
    displayValue: number;
};

export function average(series: readonly number[]): number {
    if (!series.length) return Number.NaN;
    const sum = series.reduce((acc, value) => acc + value, 0);
    return sum / series.length;
}

export function stdDev(series: readonly number[]): number {
    if (series.length < 2) return 0;
    const mean = average(series);
    const variance = series.reduce((acc, value) => acc + (value - mean) ** 2, 0) / (series.length - 1);
    return Math.sqrt(variance);
}

export function resolveDomainCount(valueCount: number, domainSize: number | null): number {
    const base = Number.isFinite(valueCount) && valueCount >= 0 ? Math.floor(valueCount) : 0;
    const hinted = domainSize !== null && Number.isFinite(domainSize) && domainSize >= 0 ? Math.floor(domainSize) : 0;
    const resolved = Math.max(base, hinted);
    return Math.max(resolved, 1);
}

export function prepareCanvasSurface(
    canvas: HTMLCanvasElement,
): { ctx: CanvasRenderingContext2D; width: number; height: number } | null {
    const ctx = canvas.getContext('2d');
    if (!ctx) return null;

    const devicePixelRatio =
        typeof window !== 'undefined' && Number.isFinite(window.devicePixelRatio)
            ? Math.min(3, Math.max(1, window.devicePixelRatio))
            : 1;

    const rect = canvas.getBoundingClientRect();
    const fallbackWidth = canvas.clientWidth || canvas.offsetWidth;
    const fallbackHeight = canvas.clientHeight || canvas.offsetHeight;
    const previousWidth = canvas.width ? Math.round(canvas.width / devicePixelRatio) : 0;
    const previousHeight = canvas.height ? Math.round(canvas.height / devicePixelRatio) : 0;

    const displayWidth = Math.max(1, Math.round(rect.width || fallbackWidth || previousWidth || 300));
    const displayHeight = Math.max(1, Math.round(rect.height || fallbackHeight || previousHeight || 150));

    const renderWidth = Math.max(1, Math.round(displayWidth * devicePixelRatio));
    const renderHeight = Math.max(1, Math.round(displayHeight * devicePixelRatio));

    if (canvas.width !== renderWidth || canvas.height !== renderHeight) {
        canvas.width = renderWidth;
        canvas.height = renderHeight;
    }

    if (typeof ctx.setTransform === 'function') {
        ctx.setTransform(devicePixelRatio, 0, 0, devicePixelRatio, 0, 0);
    } else {
        ctx.resetTransform?.();
        if (devicePixelRatio !== 1) {
            ctx.scale(devicePixelRatio, devicePixelRatio);
        }
    }

    return { ctx, width: displayWidth, height: displayHeight };
}

function generateLineSeriesPoints(options: {
    values: readonly number[];
    originX: number;
    originY: number;
    plotWidth: number;
    plotHeight: number;
    minValue: number;
    maxValue: number;
    xIndices?: readonly number[];
    domainMaxIndex?: number;
}): LineSeriesPoint[] {
    const { values, originX, originY, plotWidth, plotHeight, minValue, maxValue, xIndices, domainMaxIndex } = options;
    const yRange = maxValue - minValue || 1;
    const effectiveDomainMax = (() => {
        if (Number.isFinite(domainMaxIndex)) {
            return Math.max(1, domainMaxIndex as number);
        }
        if (Array.isArray(xIndices) && xIndices.length) {
            const finiteValues = xIndices.filter((value) => Number.isFinite(value)) as number[];
            if (finiteValues.length) {
                return Math.max(1, Math.max(...finiteValues));
            }
        }
        if (values.length) {
            return Math.max(1, values.length);
        }
        return 1;
    })();

    const points: LineSeriesPoint[] = [];

    values.forEach((value, index) => {
        if (!Number.isFinite(value)) return;
        let rawIndex: number;
        let displayValue: number;
        if (Array.isArray(xIndices)) {
            const candidate = xIndices[index];
            if (Number.isFinite(candidate)) {
                const numeric = Number(candidate);
                rawIndex = numeric;
                displayValue = numeric;
            } else {
                rawIndex = index + 1;
                displayValue = rawIndex;
            }
        } else {
            rawIndex = index + 1;
            displayValue = rawIndex;
        }
        const clampedIndex = Math.max(0, Math.min(effectiveDomainMax, rawIndex));
        const ratio = effectiveDomainMax > 0 ? clampedIndex / effectiveDomainMax : 0;
        const x = originX + plotWidth * ratio;
        const yRatio = (value - minValue) / yRange;
        const y = originY - plotHeight * yRatio;
        points.push({ x, y, sourceIndex: index, rawIndex: clampedIndex, displayValue });
    });

    return points;
}

export function drawLineSeries(
    ctx: CanvasRenderingContext2D,
    options: {
        values: readonly number[];
        color: string;
        originX: number;
        originY: number;
        plotWidth: number;
        plotHeight: number;
        minValue: number;
        maxValue: number;
        xIndices?: readonly number[];
        domainMaxIndex?: number;
        lineDash?: readonly number[];
    },
): LineSeriesPoint[] {
    const {
        values,
        color,
        originX,
        originY,
        plotWidth,
        plotHeight,
        minValue,
        maxValue,
        xIndices,
        domainMaxIndex,
        lineDash,
    } = options;

    const points = generateLineSeriesPoints({
        values,
        originX,
        originY,
        plotWidth,
        plotHeight,
        minValue,
        maxValue,
        xIndices,
        domainMaxIndex,
    });

    if (!points.length) {
        return [];
    }

    ctx.save();
    ctx.strokeStyle = color;
    ctx.lineWidth = 2;
    if (Array.isArray(lineDash) && lineDash.length) {
        ctx.setLineDash(lineDash as number[]);
    } else {
        ctx.setLineDash([]);
    }
    ctx.beginPath();
    ctx.moveTo(points[0].x, points[0].y);
    for (let idx = 1; idx < points.length; idx += 1) {
        ctx.lineTo(points[idx].x, points[idx].y);
    }
    ctx.stroke();
    ctx.restore();

    if (points.length === 1) {
        ctx.fillStyle = color;
        ctx.beginPath();
        ctx.arc(points[0].x, points[0].y, 3, 0, Math.PI * 2);
        ctx.fill();
    }

    return points;
}

export function drawConfidenceBand(
    ctx: CanvasRenderingContext2D,
    options: {
        upperValues: readonly number[];
        lowerValues: readonly number[];
        originX: number;
        originY: number;
        plotWidth: number;
        plotHeight: number;
        minValue: number;
        maxValue: number;
        xIndices?: readonly number[];
        domainMaxIndex?: number;
        fillStyle?: string;
        strokeStyle?: string;
        strokeWidth?: number;
    },
): void {
    const {
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
        fillStyle,
        strokeStyle,
        strokeWidth,
    } = options;

    const upperPoints = generateLineSeriesPoints({
        values: upperValues,
        originX,
        originY,
        plotWidth,
        plotHeight,
        minValue,
        maxValue,
        xIndices,
        domainMaxIndex,
    });
    const lowerPoints = generateLineSeriesPoints({
        values: lowerValues,
        originX,
        originY,
        plotWidth,
        plotHeight,
        minValue,
        maxValue,
        xIndices,
        domainMaxIndex,
    });

    if (!upperPoints.length || !lowerPoints.length) {
        return;
    }

    const upperMap = new Map<number, LineSeriesPoint>();
    const lowerMap = new Map<number, LineSeriesPoint>();
    upperPoints.forEach((point) => {
        upperMap.set(point.sourceIndex, point);
    });
    lowerPoints.forEach((point) => {
        lowerMap.set(point.sourceIndex, point);
    });

    const segments: Array<{ upper: LineSeriesPoint[]; lower: LineSeriesPoint[] }> = [];
    let currentUpper: LineSeriesPoint[] = [];
    let currentLower: LineSeriesPoint[] = [];
    const maxIndex = Math.max(upperValues.length, lowerValues.length);

    for (let idx = 0; idx < maxIndex; idx += 1) {
        const upperPoint = upperMap.get(idx);
        const lowerPoint = lowerMap.get(idx);
        if (upperPoint && lowerPoint) {
            currentUpper.push(upperPoint);
            currentLower.push(lowerPoint);
            continue;
        }

        if (currentUpper.length && currentLower.length) {
            segments.push({ upper: currentUpper, lower: currentLower });
        }
        currentUpper = [];
        currentLower = [];
    }

    if (currentUpper.length && currentLower.length) {
        segments.push({ upper: currentUpper, lower: currentLower });
    }

    const resolvedFill = fillStyle ?? '#cbd5f5';
    const resolvedStroke = strokeStyle ?? resolvedFill ?? '#64748b';
    const resolvedStrokeWidth = Number.isFinite(strokeWidth) ? (strokeWidth as number) : 2;

    segments.forEach((segment) => {
        if (segment.upper.length === 1 && segment.lower.length === 1) {
            const upperPoint = segment.upper[0];
            const lowerPoint = segment.lower[0];
            ctx.save();
            ctx.strokeStyle = resolvedStroke;
            ctx.lineWidth = resolvedStrokeWidth;
            ctx.beginPath();
            ctx.moveTo(upperPoint.x, upperPoint.y);
            ctx.lineTo(lowerPoint.x, lowerPoint.y);
            ctx.stroke();
            ctx.restore();
            return;
        }
        if (segment.upper.length < 2 || segment.lower.length < 2) {
            return;
        }
        ctx.save();
        ctx.fillStyle = resolvedFill;
        ctx.beginPath();
        ctx.moveTo(segment.upper[0].x, segment.upper[0].y);
        for (let idx = 1; idx < segment.upper.length; idx += 1) {
            ctx.lineTo(segment.upper[idx].x, segment.upper[idx].y);
        }
        for (let idx = segment.lower.length - 1; idx >= 0; idx -= 1) {
            ctx.lineTo(segment.lower[idx].x, segment.lower[idx].y);
        }
        ctx.closePath();
        ctx.fill();
        ctx.restore();
    });
}

export { computeIndexTicks, computeNiceTicks, formatTickLabel };
