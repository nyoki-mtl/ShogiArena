import { computeNiceTicks, formatTickLabel } from '@/modules/shared/utils/chart';
import { escapeHtml } from '@/modules/shared/utils/html';
import { getDashboardSpsaApi } from './shared';
import { formatParameterValue, type ParameterInsight } from './parameter-shared';
import { METRIC_ZERO_THRESHOLD } from './thresholds';
import { buildStaticBaselineSeries, getEpsilon, normalizeRatio } from './correlation-state';

export type ParameterChartSeries = {
    actual: number[];
    baseline: number[];
    variants: number[];
    displayVariants: number[];
    pending: boolean[];
    ltcRejected: boolean[];
    ltcDecisionRejected?: boolean[];
};

type ParameterChartPoint = {
    x: number;
    y: number;
    variantIdx: number;
    pending: boolean;
    ltcRejected?: boolean;
    baselineValue: number;
    updatedValue: number;
    delta: number;
    displayVariant: number;
};

type ParameterChartCanvas = HTMLCanvasElement & {
    __parameterChartPoints?: readonly ParameterChartPoint[];
    __parameterRangeScale?: number | null;
    __parameterChartPayload?: { series: ParameterChartSeries; insight: ParameterInsight };
    __parameterResizeObserver?: ResizeObserver | null;
    __parameterRendering?: boolean;
};

type ChartTooltip = {
    element: HTMLElement;
    container: HTMLElement;
};

type ChartCrosshair = {
    element: HTMLElement;
    container: HTMLElement;
};

const CHART_CLICK_DISTANCE_PX = 18;
const CHART_AXIS_SNAP_PADDING_PX = 24;
const CHART_MIN_RENDER_SIZE_PX = 24;
const CHART_FALLBACK_WIDTH_PX = 400;
const CHART_FALLBACK_HEIGHT_PX = 320;

const parameterChartClickHandlers = new WeakMap<HTMLCanvasElement, (event: MouseEvent) => void>();
const parameterChartHoverHandlers = new WeakMap<
    HTMLCanvasElement,
    {
        move: (event: MouseEvent) => void;
        leave: () => void;
    }
>();

export function buildChartData(insight: ParameterInsight): ParameterChartSeries | null {
    const actual = Array.isArray(insight.evolution) ? [...insight.evolution] : [];
    if (!actual.length) {
        return null;
    }
    const baselineSource = Array.isArray(insight.baselineEvolution) ? insight.baselineEvolution : [];
    const variantsSource = Array.isArray(insight.variantIndexes) ? insight.variantIndexes : [];
    const displayIndexesSource = Array.isArray(insight.displayIndexes) ? insight.displayIndexes : [];
    const pendingSource = Array.isArray(insight.pendingFlags) ? insight.pendingFlags : [];
    const ltcRejectedSource = Array.isArray(insight.ltcRejectedFlags) ? insight.ltcRejectedFlags : [];
    const ltcDecisionRejectedSource = Array.isArray(insight.ltcDecisionRejectedFlags)
        ? insight.ltcDecisionRejectedFlags
        : [];
    const baseline = baselineSource.length === actual.length ? [...baselineSource] : buildStaticBaselineSeries(actual);
    if (variantsSource.length !== actual.length) {
        return null;
    }
    const variants = variantsSource.map((value) => Number(value));
    if (!variants.every((value) => Number.isFinite(value))) {
        return null;
    }
    const displayVariants =
        displayIndexesSource.length === actual.length
            ? displayIndexesSource.map((value) => Number(value))
            : variants.map((_, idx) => idx);
    if (!displayVariants.every((value) => Number.isFinite(value))) {
        return null;
    }
    const pending = pendingSource.length === actual.length ? [...pendingSource] : new Array(actual.length).fill(false);
    const ltcRejected =
        ltcRejectedSource.length === actual.length ? [...ltcRejectedSource] : new Array(actual.length).fill(false);
    const ltcDecisionRejected =
        ltcDecisionRejectedSource.length === actual.length
            ? [...ltcDecisionRejectedSource]
            : new Array(actual.length).fill(false);
    return { actual, baseline, variants, displayVariants, pending, ltcRejected, ltcDecisionRejected };
}

export function renderParameterChart(
    canvas: HTMLCanvasElement,
    series: ParameterChartSeries,
    insight: ParameterInsight,
): void {
    const chartCanvas = canvas as ParameterChartCanvas;
    chartCanvas.__parameterChartPayload = { series, insight };
    ensureChartResizeObserver(chartCanvas);

    if (chartCanvas.__parameterRendering) {
        return;
    }

    const size = resolveCanvasRenderSize(canvas);
    const usingFallbackSize = !size;
    const width = size?.width ?? CHART_FALLBACK_WIDTH_PX;
    const height = size?.height ?? CHART_FALLBACK_HEIGHT_PX;
    const dpr = size?.dpr ?? resolveDevicePixelRatio();

    if (usingFallbackSize) {
        scheduleFallbackChartRender(chartCanvas);
    }

    canvas.width = Math.max(1, Math.round(width * dpr));
    canvas.height = Math.max(1, Math.round(height * dpr));
    const ctx = canvas.getContext('2d');
    if (!ctx) {
        chartCanvas.__parameterChartPoints = [];
        bindParameterChartInteraction(canvas, []);
        return;
    }

    chartCanvas.__parameterRendering = true;
    try {
        ctx.save();
        ctx.scale(dpr, dpr);
        ctx.clearRect(0, 0, width, height);

        const points = series.actual.length;
        if (!points) {
            ctx.restore();
            chartCanvas.__parameterChartPoints = [];
            bindParameterChartInteraction(canvas, []);
            return;
        }

        const extents = [...series.actual, ...series.baseline];
        let min = Math.min(...extents);
        let max = Math.max(...extents);
        if (!Number.isFinite(min) || !Number.isFinite(max)) {
            ctx.restore();
            chartCanvas.__parameterChartPoints = [];
            bindParameterChartInteraction(canvas, []);
            return;
        }
        if (Math.abs(max - min) < 1e-6) {
            const pad = Math.max(Math.abs(max) * 0.05, 0.5);
            min -= pad;
            max += pad;
        }
        const range = Math.max(max - min, getEpsilon());
        const padding = { top: 16, right: 24, bottom: 52, left: 74 };
        const plotWidth = Math.max(width - padding.left - padding.right, 10);
        const plotHeight = Math.max(height - padding.top - padding.bottom, 10);
        if (!Array.isArray(series.variants) || series.variants.length !== points) {
            ctx.restore();
            chartCanvas.__parameterChartPoints = [];
            bindParameterChartInteraction(canvas, []);
            return;
        }

        const displayVariants = Array.isArray(series.displayVariants)
            ? series.displayVariants.map((value) => (Number.isFinite(value) ? Number(value) : Number.NaN))
            : series.variants.map((value) => (Number.isFinite(value) ? Number(value) : Number.NaN));
        const variantValues = series.variants.map((value) => (Number.isFinite(value) ? Number(value) : Number.NaN));
        const ltcRejected = Array.isArray(series.ltcRejected) ? series.ltcRejected : [];
        const ltcDecisionRejected = Array.isArray(series.ltcDecisionRejected) ? series.ltcDecisionRejected : [];
        const finiteDisplayVariants = displayVariants.filter((value): value is number => Number.isFinite(value));
        if (!finiteDisplayVariants.length) {
            ctx.restore();
            chartCanvas.__parameterChartPoints = [];
            bindParameterChartInteraction(canvas, []);
            return;
        }

        chartCanvas.__parameterRangeScale = insight.rangeScale ?? null;

        const axisStart = Math.min(...finiteDisplayVariants);
        const maxDisplayVariant = Math.max(...finiteDisplayVariants);
        const variantSpanRaw = maxDisplayVariant - axisStart;
        const variantSpan = variantSpanRaw === 0 ? 1 : variantSpanRaw;
        const variantCeiling = Math.max(resolveVariantCeiling(finiteDisplayVariants), axisStart + 1);

        const variantValueToX = (variantValue: number): number | null => {
            if (!Number.isFinite(variantValue)) {
                return null;
            }
            const clamped = Math.max(axisStart, Math.min(variantValue, variantCeiling));
            const ratio = variantSpanRaw === 0 ? 0.5 : (clamped - axisStart) / variantSpan;
            return padding.left + ratio * plotWidth;
        };

        const valueToY = (value: number): number => {
            const normalized = (value - min) / range;
            return padding.top + (1 - normalized) * plotHeight;
        };

        const palette = {
            baselineLine: 'rgba(59, 130, 246, 0.95)',
            baselinePoint: 'rgba(59, 130, 246, 0.9)',
            actualLine: 'rgba(59, 130, 246, 0.4)',
            pendingPoint: 'rgba(148, 163, 184, 0.8)',
            positive: 'rgba(34, 197, 94, 0.9)',
            negative: 'rgba(248, 113, 113, 0.9)',
            ltcRejected: 'rgba(148, 163, 184, 0.6)',
        } as const;

        const resolveDeltaColor = (
            delta: number,
            pending: boolean,
            ltcRejected: boolean,
            isDecisionReject: boolean,
        ): string => {
            // LTC により無効化された点はグレー（reject 決定点も含む）。
            // 後続の reject で上書きされた reject 決定点は ltcRejected=true になる。
            if (ltcRejected) return palette.ltcRejected;
            // 最後の reject 決定点（後続 reject がない）は青色で表示。
            if (isDecisionReject) return palette.baselinePoint;
            if (pending) return palette.pendingPoint;
            if (delta > getEpsilon()) return palette.positive;
            if (delta < -getEpsilon()) return palette.negative;
            return palette.baselinePoint;
        };
        const pointRadius = 4;

        ctx.strokeStyle = 'rgba(15, 23, 42, 0.35)';
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(padding.left, padding.top);
        ctx.lineTo(padding.left, padding.top + plotHeight);
        ctx.lineTo(padding.left + plotWidth, padding.top + plotHeight);
        ctx.stroke();

        ctx.font = '12px "Inter", "Roboto", system-ui';
        ctx.fillStyle = 'rgba(15, 23, 42, 0.75)';
        ctx.textAlign = 'right';
        ctx.textBaseline = 'middle';

        // Use the shared "nice ticks" helper so fractional value ranges (e.g. 0.1–0.5) get
        // decimal labels instead of collapsing to integer ticks ("0"/"1").
        const tickInfo = computeNiceTicks(min, max, 5);
        const tickTolerance = Math.abs(tickInfo.step) * 1e-6;
        const inRangeTicks = tickInfo.ticks.filter(
            (tick) => tick >= min - tickTolerance && tick <= max + tickTolerance,
        );
        const yTicks = inRangeTicks.length ? inRangeTicks : tickInfo.ticks;
        yTicks.forEach((tick) => {
            if (!Number.isFinite(tick)) return;
            const clampedValue = clampToRange(tick, min, max);
            const ratio = (clampedValue - min) / range;
            const y = padding.top + (1 - ratio) * plotHeight;
            ctx.beginPath();
            ctx.moveTo(padding.left - 6, y);
            ctx.lineTo(padding.left, y);
            ctx.stroke();
            ctx.fillText(formatTickLabel(tick, tickInfo.step), padding.left - 10, y);
        });

        const tickStep = resolveVariantTickStep(variantCeiling);
        const tickValueSet = new Set<number>();
        tickValueSet.add(axisStart);
        tickValueSet.add(maxDisplayVariant);
        if (tickStep > 0) {
            const startTick = Math.ceil(axisStart / tickStep) * tickStep;
            for (let value = startTick; value <= variantCeiling + getEpsilon(); value += tickStep) {
                if (value < axisStart) continue;
                tickValueSet.add(value);
            }
        }
        const tickValues = [...tickValueSet].filter((value) => Number.isFinite(value)).sort((a, b) => a - b);

        ctx.textAlign = 'center';
        ctx.textBaseline = 'top';
        tickValues.forEach((value) => {
            if (!Number.isFinite(value)) return;
            const rounded = Math.round(value);
            const x = variantValueToX(value);
            if (x === null) return;
            ctx.beginPath();
            ctx.moveTo(x, padding.top + plotHeight);
            ctx.lineTo(x, padding.top + plotHeight + 6);
            ctx.stroke();
            ctx.fillText(`#${rounded}`, x, padding.top + plotHeight + 8);
        });

        ctx.save();
        ctx.translate(padding.left - 58, padding.top + plotHeight / 2);
        ctx.rotate((-90 * Math.PI) / 180);
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText('Parameter value', 0, 0);
        ctx.restore();

        ctx.textAlign = 'center';
        ctx.textBaseline = 'top';
        ctx.fillText('Variant #', padding.left + plotWidth / 2, height - 18);

        // Draw actual parameter evolution as a continuous polyline,
        // switching colour per segment based on the LTC status of the
        // segment's endpoints.
        ctx.save();
        ctx.lineWidth = 2;
        for (let idx = 1; idx < series.actual.length; idx += 1) {
            const prevValue = series.actual[idx - 1];
            const currValue = series.actual[idx];
            if (!Number.isFinite(prevValue) || !Number.isFinite(currValue)) continue;
            const prevX = variantValueToX(displayVariants[idx - 1]);
            const currX = variantValueToX(displayVariants[idx]);
            if (prevX === null || currX === null) continue;
            const prevY = valueToY(prevValue);
            const currY = valueToY(currValue);
            const segmentRejected = Boolean(ltcRejected[idx - 1] || ltcRejected[idx]);
            ctx.strokeStyle = segmentRejected ? palette.ltcRejected : palette.actualLine;
            ctx.beginPath();
            ctx.moveTo(prevX, prevY);
            ctx.lineTo(currX, currY);
            ctx.stroke();
        }
        ctx.restore();

        const interactivePoints: ParameterChartPoint[] = [];

        series.actual.forEach((value, index) => {
            if (!Number.isFinite(value)) return;
            const baselineValue = series.baseline[index];
            if (!Number.isFinite(baselineValue)) return;
            const delta = value - baselineValue;
            const pending = Boolean(series.pending[index]);
            const ltcRej = Boolean(ltcRejected[index]);
            const isDecisionReject = Boolean(ltcDecisionRejected[index]);
            ctx.fillStyle = resolveDeltaColor(delta, pending, ltcRej, isDecisionReject);
            const x = variantValueToX(displayVariants[index]);
            if (x === null) return;
            const y = valueToY(value);
            ctx.beginPath();
            ctx.arc(x, y, pointRadius, 0, Math.PI * 2);
            ctx.fill();
            ctx.lineWidth = 1;
            ctx.strokeStyle = 'rgba(255, 255, 255, 0.9)';
            ctx.stroke();
            const actualVariant = Number.isFinite(variantValues[index]) ? variantValues[index] : index;
            const displayVariant = Number.isFinite(displayVariants[index]) ? displayVariants[index] : index;
            const deltaValue = value - baselineValue;
            interactivePoints.push({
                x,
                y,
                variantIdx: actualVariant,
                pending,
                // ツールチップ上では「決定点」も (LTC Rejected) と表示するため、
                // ltcRejected には区間無効化または決定リジェクトのどちらかが立っていればよい。
                ltcRejected: ltcRej || isDecisionReject,
                baselineValue,
                updatedValue: value,
                delta: deltaValue,
                displayVariant,
            });
        });

        ctx.restore();

        chartCanvas.__parameterChartPoints = interactivePoints;
        bindParameterChartInteraction(canvas, interactivePoints);
    } finally {
        chartCanvas.__parameterRendering = false;
    }
}

function bindParameterChartInteraction(canvas: HTMLCanvasElement, points: readonly ParameterChartPoint[]): void {
    const chartCanvas = canvas as ParameterChartCanvas;
    chartCanvas.__parameterChartPoints = points;
    const existing = parameterChartClickHandlers.get(canvas);
    if (existing) {
        canvas.removeEventListener('click', existing);
        parameterChartClickHandlers.delete(canvas);
    }
    const hoverHandlers = parameterChartHoverHandlers.get(canvas);
    if (hoverHandlers) {
        canvas.removeEventListener('mousemove', hoverHandlers.move);
        canvas.removeEventListener('mouseleave', hoverHandlers.leave);
        parameterChartHoverHandlers.delete(canvas);
    }
    if (!points.length) {
        return;
    }
    const handler = (event: MouseEvent) => {
        const targetPoint = resolveChartPointFromEvent(canvas, points, event, { snapToAxis: true });
        if (!targetPoint) return;
        event.preventDefault();
        handleChartPointSelection(targetPoint.variantIdx);
    };
    parameterChartClickHandlers.set(canvas, handler);
    canvas.addEventListener('click', handler);

    const tooltip = ensureChartTooltip(canvas);
    const crosshair = ensureChartCrosshair(canvas);
    const moveHandler = (event: MouseEvent) => {
        handleChartHover(canvas, points, event, tooltip, crosshair);
    };
    const leaveHandler = () => {
        hideChartTooltip(tooltip);
        hideChartCrosshair(crosshair);
    };
    parameterChartHoverHandlers.set(canvas, { move: moveHandler, leave: leaveHandler });
    canvas.addEventListener('mousemove', moveHandler);
    canvas.addEventListener('mouseleave', leaveHandler);
}

function resolveChartPointFromEvent(
    canvas: HTMLCanvasElement,
    points: readonly ParameterChartPoint[],
    event: MouseEvent,
    options?: { snapToAxis?: boolean },
): ParameterChartPoint | null {
    if (!points.length) return null;
    const rect = canvas.getBoundingClientRect();
    const borders = getCanvasBorderOffsets(canvas);
    const clickX = event.clientX - rect.left - borders.left;
    const clickY = event.clientY - rect.top - borders.top;
    const width = Math.max(0, rect.width - borders.left - borders.right);
    const height = Math.max(0, rect.height - borders.top - borders.bottom);
    const thresholdSq = CHART_CLICK_DISTANCE_PX * CHART_CLICK_DISTANCE_PX;
    let closest: ParameterChartPoint | null = null;
    let minDistance = thresholdSq;
    for (const point of points) {
        const dx = point.x - clickX;
        const dy = point.y - clickY;
        const distanceSq = dx * dx + dy * dy;
        if (distanceSq <= minDistance) {
            minDistance = distanceSq;
            closest = point;
        }
    }
    if (closest || !options?.snapToAxis) {
        return closest;
    }
    const withinCanvasBounds =
        clickX >= -CHART_AXIS_SNAP_PADDING_PX &&
        clickX <= width + CHART_AXIS_SNAP_PADDING_PX &&
        clickY >= -CHART_AXIS_SNAP_PADDING_PX &&
        clickY <= height + CHART_AXIS_SNAP_PADDING_PX;
    if (!withinCanvasBounds) {
        return null;
    }
    let axisClosest: ParameterChartPoint | null = null;
    let minHorizontalDistance = Number.POSITIVE_INFINITY;
    for (const point of points) {
        const dx = Math.abs(point.x - clickX);
        if (dx < minHorizontalDistance) {
            minHorizontalDistance = dx;
            axisClosest = point;
        }
    }
    return axisClosest;
}

function handleChartPointSelection(variantIdx: number): void {
    if (!Number.isFinite(variantIdx)) return;
    const updateIdx = Math.max(0, Math.round(variantIdx));
    const api = getDashboardSpsaApi();
    if (!api || typeof api.focusUpdate !== 'function') {
        console.warn('[SPSA] Dashboard API is not available to focus update from chart.');
        return;
    }
    api.focusUpdate(updateIdx, { scroll: true });
}

function handleChartHover(
    canvas: HTMLCanvasElement,
    points: readonly ParameterChartPoint[],
    event: MouseEvent,
    tooltip: ChartTooltip | null,
    crosshair: ChartCrosshair | null,
): void {
    if (!tooltip) return;
    const targetPoint = resolveChartPointFromEvent(canvas, points, event, { snapToAxis: true });
    if (!targetPoint) {
        hideChartTooltip(tooltip);
        hideChartCrosshair(crosshair);
        return;
    }
    showChartCrosshair(crosshair, canvas, targetPoint);

    const chartCanvas = canvas as ParameterChartCanvas;
    const deltaRatio = normalizeRatio(targetPoint.delta, chartCanvas.__parameterRangeScale ?? null);
    const deltaMetric = formatRatioMetric(deltaRatio, { signed: true });

    const rect = canvas.getBoundingClientRect();
    const containerRect = tooltip.container.getBoundingClientRect();
    const left = rect.left + targetPoint.x - containerRect.left + 12;
    const top = rect.top + targetPoint.y - containerRect.top - 12;

    tooltip.element.style.display = 'block';
    tooltip.element.style.left = `${left}px`;
    tooltip.element.style.top = `${top}px`;
    const ltcRejectedLabel = targetPoint.ltcRejected
        ? '<span style="color: rgba(148, 163, 184, 1);"> (LTC Rejected)</span>'
        : '';
    tooltip.element.innerHTML = `
        <div><strong>Variant ID</strong><span>#${formatVariantLabel(targetPoint.displayVariant)}${ltcRejectedLabel}</span></div>
        <div><strong>Baseline</strong><span>${formatParameterValue(targetPoint.baselineValue)}</span></div>
        <div><strong>Updated</strong><span>${formatParameterValue(targetPoint.updatedValue)}</span></div>
        <div><strong>Δ</strong><span>${escapeHtml(deltaMetric.label)}</span></div>
    `;
}

function hideChartTooltip(tooltip: ChartTooltip | null): void {
    if (!tooltip) return;
    tooltip.element.style.display = 'none';
}

function showChartCrosshair(
    crosshair: ChartCrosshair | null,
    canvas: HTMLCanvasElement,
    point: ParameterChartPoint,
): void {
    if (!crosshair) return;
    const canvasRect = canvas.getBoundingClientRect();
    const containerRect = crosshair.container.getBoundingClientRect();
    const borders = getCanvasBorderOffsets(canvas);
    const top = canvasRect.top - containerRect.top + borders.top;
    const left = canvasRect.left - containerRect.left + borders.left + point.x;
    const height = Math.max(0, canvasRect.height - borders.top - borders.bottom);
    crosshair.element.style.display = 'block';
    crosshair.element.style.left = `${left}px`;
    crosshair.element.style.top = `${top}px`;
    crosshair.element.style.height = `${height}px`;
}

function hideChartCrosshair(crosshair: ChartCrosshair | null): void {
    if (!crosshair) return;
    crosshair.element.style.display = 'none';
}

function ensureChartTooltip(canvas: HTMLCanvasElement): ChartTooltip | null {
    const container = canvas.closest<HTMLElement>('.parameter-detail-grid');
    if (!container) return null;
    let element = container.querySelector<HTMLElement>('[data-role="parameter-chart-tooltip"]');
    if (!element) {
        element = document.createElement('div');
        element.dataset.role = 'parameter-chart-tooltip';
        element.className = 'parameter-chart-tooltip';
        element.style.display = 'none';
        container.appendChild(element);
    }
    return { element, container };
}

function ensureChartCrosshair(canvas: HTMLCanvasElement): ChartCrosshair | null {
    const container = canvas.closest<HTMLElement>('.parameter-detail-grid');
    if (!container) return null;
    let element = container.querySelector<HTMLElement>('[data-role="parameter-chart-crosshair"]');
    if (!element) {
        element = document.createElement('div');
        element.dataset.role = 'parameter-chart-crosshair';
        element.className = 'parameter-chart-crosshair';
        element.style.display = 'none';
        container.appendChild(element);
    }
    return { element, container };
}

function getCanvasBorderOffsets(canvas: HTMLCanvasElement): {
    top: number;
    right: number;
    bottom: number;
    left: number;
} {
    const style = window.getComputedStyle(canvas);
    const parse = (value: string | null): number => {
        if (!value) return 0;
        const parsed = Number.parseFloat(value);
        return Number.isFinite(parsed) ? parsed : 0;
    };
    return {
        top: parse(style.borderTopWidth),
        right: parse(style.borderRightWidth),
        bottom: parse(style.borderBottomWidth),
        left: parse(style.borderLeftWidth),
    };
}

function ensureChartResizeObserver(canvas: ParameterChartCanvas): void {
    if (canvas.__parameterResizeObserver || typeof ResizeObserver !== 'function') {
        return;
    }
    const observer = new ResizeObserver(() => {
        if (!canvas.isConnected) {
            observer.disconnect();
            canvas.__parameterResizeObserver = null;
            return;
        }
        if (canvas.__parameterRendering) {
            return;
        }
        const payload = canvas.__parameterChartPayload;
        if (!payload) {
            return;
        }
        const hasRenderableSize =
            canvas.clientWidth > CHART_MIN_RENDER_SIZE_PX && canvas.clientHeight > CHART_MIN_RENDER_SIZE_PX;
        if (!hasRenderableSize) {
            return;
        }
        renderParameterChart(canvas, payload.series, payload.insight);
    });
    canvas.__parameterResizeObserver = observer;
    observer.observe(canvas);
}

function scheduleFallbackChartRender(canvas: ParameterChartCanvas): void {
    if (typeof ResizeObserver === 'function') {
        return;
    }
    if (!canvas.__parameterChartPayload) {
        return;
    }
    if (canvas.__parameterRendering) {
        return;
    }
    const payload = canvas.__parameterChartPayload;
    window.requestAnimationFrame(() => {
        if (!canvas.isConnected || !payload) {
            return;
        }
        if (canvas.clientWidth <= CHART_MIN_RENDER_SIZE_PX || canvas.clientHeight <= CHART_MIN_RENDER_SIZE_PX) {
            return;
        }
        renderParameterChart(canvas, payload.series, payload.insight);
    });
}

function resolveCanvasRenderSize(canvas: HTMLCanvasElement): { width: number; height: number; dpr: number } | null {
    const rect = canvas.getBoundingClientRect();
    const width = rect.width || canvas.clientWidth || 0;
    const height = rect.height || canvas.clientHeight || 0;
    if (width <= CHART_MIN_RENDER_SIZE_PX || height <= CHART_MIN_RENDER_SIZE_PX) {
        return null;
    }
    return { width, height, dpr: resolveDevicePixelRatio() };
}

function resolveDevicePixelRatio(): number {
    return Number.isFinite(window.devicePixelRatio) && window.devicePixelRatio > 0 ? window.devicePixelRatio : 1;
}

function resolveVariantCeiling(values: number[]): number {
    const numeric = values.filter((value): value is number => Number.isFinite(value));
    if (!numeric.length) return 0;
    return Math.max(...numeric);
}

function resolveVariantTickStep(maxVariant: number): number {
    if (maxVariant <= 10) return 1;
    if (maxVariant <= 50) return 5;
    if (maxVariant <= 200) return 10;
    if (maxVariant <= 500) return 20;
    if (maxVariant <= 1000) return 50;
    return 100;
}

function clampToRange(value: number, min: number, max: number): number {
    if (!Number.isFinite(value) || !Number.isFinite(min) || !Number.isFinite(max)) {
        return value;
    }
    const low = Math.min(min, max);
    const high = Math.max(min, max);
    if (value < low) return low;
    if (value > high) return high;
    return value;
}

function formatVariantLabel(value: number): string {
    if (!Number.isFinite(value)) return '-';
    return Math.max(0, Math.round(value)).toString();
}

function formatRatioMetric(
    value: number | null,
    options: { signed?: boolean } = {},
): { label: string; className: string } {
    if (value === null || !Number.isFinite(value)) {
        return { label: '—', className: 'parameter-metric metric-neutral' };
    }
    const normalized = Math.abs(value) <= METRIC_ZERO_THRESHOLD ? 0 : (value as number);
    const rounded = Number.parseFloat(normalized.toFixed(3));
    if (rounded === 0) {
        return { label: ' 0.000', className: 'parameter-metric metric-neutral' };
    }
    const base = rounded.toFixed(3);
    if (!options.signed) {
        return { label: base, className: 'parameter-metric metric-neutral' };
    }
    if (rounded > 0) {
        return { label: `+${base}`, className: 'parameter-metric metric-positive' };
    }
    return { label: base, className: 'parameter-metric metric-negative' };
}
