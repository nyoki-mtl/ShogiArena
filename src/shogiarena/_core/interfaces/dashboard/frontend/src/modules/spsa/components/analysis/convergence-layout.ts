import { escapeHtml } from '@/modules/shared/utils/html';
import { DEFAULT_CONVERGENCE_DOMAIN } from '../../constants';
import {
    prepareCanvasSurface,
    resolveDomainCount,
    computeIndexTicks,
    computeNiceTicks,
    formatTickLabel,
} from './convergence-state';

const CONVERGENCE_CONTAINER_ID = 'convergenceContainer';
export const DELTA_HISTORY_CANVAS_ID = 'deltaNormHistoryCanvas';
export const DELTA_HISTORY_META_ID = 'deltaNormHistoryMeta';
export const LTC_ELO_CANVAS_ID = 'ltcEloHistoryCanvas';
export const LTC_ELO_META_ID = 'ltcEloHistoryMeta';
export const LTC_SUMMARY_ID = 'ltcRegressionSummary';

export type ConvergenceLayout = {
    container: HTMLElement;
};

const convergenceLayoutCache = new WeakMap<HTMLElement, ConvergenceLayout>();

export function ensureConvergenceLayout(): ConvergenceLayout | null {
    const container = document.getElementById(CONVERGENCE_CONTAINER_ID);
    if (!(container instanceof HTMLElement)) {
        return null;
    }
    const cached = convergenceLayoutCache.get(container);
    if (cached?.container?.isConnected) {
        return cached;
    }

    const hasDeltaCanvas = container.querySelector(`#${DELTA_HISTORY_CANVAS_ID}`);
    const hasLtcCanvas = container.querySelector(`#${LTC_ELO_CANVAS_ID}`);
    if (!hasDeltaCanvas && !hasLtcCanvas) {
        container.innerHTML = '<p class="loading">Loading convergence data...</p>';
    }
    container.hidden = false;
    const layout: ConvergenceLayout = { container };
    convergenceLayoutCache.set(container, layout);
    return layout;
}

export function clearConvergenceContent(): void {
    const layout = ensureConvergenceLayout();
    if (!layout) return;
    const hasDeltaCanvas = layout.container.querySelector(`#${DELTA_HISTORY_CANVAS_ID}`);
    const hasLtcCanvas = layout.container.querySelector(`#${LTC_ELO_CANVAS_ID}`);
    if (hasDeltaCanvas || hasLtcCanvas) {
        layout.container.hidden = false;
        return;
    }
    layout.container.hidden = true;
    layout.container.innerHTML = '';
}

export function renderConvergenceLoading(): void {
    const layout = ensureConvergenceLayout();
    if (!layout) return;
    const hasDeltaCanvas = layout.container.querySelector(`#${DELTA_HISTORY_CANVAS_ID}`);
    const hasLtcCanvas = layout.container.querySelector(`#${LTC_ELO_CANVAS_ID}`);
    layout.container.hidden = false;
    if (!hasDeltaCanvas && !hasLtcCanvas) {
        layout.container.innerHTML = '<p class="loading">Convergence analysis is loading...</p>';
    }
}

export function renderConvergenceError(message: string): void {
    const layout = ensureConvergenceLayout();
    if (!layout) return;
    const hasDeltaCanvas = layout.container.querySelector(`#${DELTA_HISTORY_CANVAS_ID}`);
    const hasLtcCanvas = layout.container.querySelector(`#${LTC_ELO_CANVAS_ID}`);
    layout.container.hidden = false;
    if (!hasDeltaCanvas && !hasLtcCanvas) {
        layout.container.innerHTML = `<p class="error">${escapeHtml(message)}</p>`;
    }
}

export function updateMetaText(id: string, text: string): void {
    const element = document.getElementById(id);
    if (!element) return;
    element.textContent = text;
}

export function resetConvergenceCharts(reason?: string): void {
    const fallbackDomain = resolveDomainCount(0, DEFAULT_CONVERGENCE_DOMAIN);
    const historyMessage = reason ? `No Δ-norm data (${reason})` : '';
    drawCanvasPlaceholder(DELTA_HISTORY_CANVAS_ID, historyMessage, {
        yRange: [-1, 1],
        pointCount: fallbackDomain,
        formatXLabel: (variant: number) => `#${variant}`,
    });
    const deltaCanvas = document.getElementById(DELTA_HISTORY_CANVAS_ID) as HTMLCanvasElement | null;
    if (deltaCanvas) {
        deltaCanvas.replaceWith(deltaCanvas); // clears listeners if any
    }
    updateMetaText(DELTA_HISTORY_META_ID, '');
    const ltcCanvas = document.getElementById(LTC_ELO_CANVAS_ID) as HTMLCanvasElement | null;
    if (ltcCanvas) {
        drawCanvasPlaceholder(ltcCanvas, reason ? 'No LTC regression data' : '', {
            yRange: [-200, 200],
            pointCount: fallbackDomain,
            formatXLabel: (variant: number) => `#${variant}`,
        });
        ltcCanvas.replaceWith(ltcCanvas);
    }
    updateMetaText(LTC_ELO_META_ID, '');
}

type PlaceholderOptions = {
    readonly yRange?: readonly [number, number];
    readonly pointCount?: number;
    readonly formatXLabel?: (index: number) => string;
};

export function drawCanvasPlaceholder(
    target: string | HTMLCanvasElement,
    message: string,
    options: PlaceholderOptions = {},
): void {
    const canvas = typeof target === 'string' ? document.getElementById(target) : target;
    if (!(canvas instanceof HTMLCanvasElement)) return;
    const surface = prepareCanvasSurface(canvas);
    if (!surface) return;

    const { ctx, width, height } = surface;
    const padding = { top: 24, right: 20, bottom: 32, left: 52 };
    const plotWidth = Math.max(1, width - (padding.left + padding.right));
    const plotHeight = Math.max(1, height - (padding.top + padding.bottom));

    const [rangeMin, rangeMax] = options.yRange ?? [-1, 1];
    const { ticks, min, max, step } = computeNiceTicks(rangeMin, rangeMax, 5);
    const placeholderCount =
        typeof options.pointCount === 'number' && Number.isFinite(options.pointCount)
            ? Math.max(2, Math.min(50, Math.round(options.pointCount)))
            : 5;
    const xTicks = computeIndexTicks(placeholderCount, Math.min(placeholderCount, 6));
    const originX = padding.left;
    const originY = padding.top + plotHeight;

    ctx.clearRect(0, 0, width, height);
    ctx.fillStyle = '#f8fafc';
    ctx.fillRect(0, 0, width, height);

    drawPlaceholderGrid(ctx, {
        originX,
        originY,
        plotWidth,
        plotHeight,
        minValue: min,
        maxValue: max,
        pointCount: placeholderCount,
        yTicks: ticks,
        yStep: step,
        xTicks,
        formatXLabel: options.formatXLabel,
    });

    const trimmedMessage = typeof message === 'string' ? message.trim() : '';
    if (!trimmedMessage) {
        return;
    }

    ctx.font = '14px sans-serif';
    const metrics = ctx.measureText(trimmedMessage);
    const textWidth = Math.max(0, metrics.width);
    const boxPaddingX = 12;
    const boxPaddingY = 8;
    const ascent = metrics.actualBoundingBoxAscent ?? 0;
    const descent = metrics.actualBoundingBoxDescent ?? 0;
    const textHeight = ascent + descent || 14;
    const boxWidth = Math.min(plotWidth, textWidth + boxPaddingX * 2);
    const boxHeight = Math.max(32, textHeight + boxPaddingY * 2);
    const boxX = Math.max(originX, Math.min(originX + plotWidth - boxWidth, originX + plotWidth / 2 - boxWidth / 2));
    const boxY = padding.top + plotHeight / 2 - boxHeight / 2;

    ctx.fillStyle = 'rgba(248, 250, 252, 0.92)';
    ctx.fillRect(boxX, boxY, boxWidth, boxHeight);
    ctx.strokeStyle = 'rgba(148, 163, 184, 0.35)';
    ctx.lineWidth = 1;
    ctx.strokeRect(boxX, boxY, boxWidth, boxHeight);

    ctx.fillStyle = '#64748b';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(trimmedMessage, boxX + boxWidth / 2, boxY + boxHeight / 2);
}

function drawPlaceholderGrid(
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
