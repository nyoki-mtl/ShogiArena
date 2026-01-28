import { getDashboardSpsaApi } from './shared';

const CHART_INTERACTION_DISTANCE_PX = 18;
const CHART_AXIS_SNAP_PADDING_PX = 12;

type ChartTooltip = {
    element: HTMLElement;
    container: HTMLElement;
};

type ChartCrosshair = {
    element: HTMLElement;
    container: HTMLElement;
};

type ChartInteractionHandlers = {
    move: (event: MouseEvent) => void;
    leave: () => void;
    click: (event: MouseEvent) => void;
};

const chartInteractionRegistry = new WeakMap<HTMLCanvasElement, ChartInteractionHandlers>();

export type InteractiveChartPoint = {
    x: number;
    y: number;
    variantIdx: number;
    displayVariant: number;
};

export interface ChartInteractivityOptions<T extends InteractiveChartPoint> {
    tooltipRenderer: (point: T) => string;
    containerSelector?: string;
    snapToAxis?: boolean;
    onPointSelected?: (point: T) => void;
}

export function bindChartInteractivity<T extends InteractiveChartPoint>(
    canvas: HTMLCanvasElement,
    points: readonly T[],
    options: ChartInteractivityOptions<T>,
): void {
    clearChartInteractivity(canvas, options.containerSelector);
    if (!points.length) {
        return;
    }

    const tooltip = ensureChartTooltip(canvas, options.containerSelector);
    const crosshair = ensureChartCrosshair(canvas, options.containerSelector);
    if (!tooltip || !crosshair) {
        return;
    }

    const snapToAxis = options.snapToAxis !== false;

    const moveHandler = (event: MouseEvent) => {
        const target = resolveChartPointFromEvent(canvas, points, event, { snapToAxis });
        if (!target) {
            hideChartTooltip(tooltip);
            hideChartCrosshair(crosshair);
            return;
        }
        showChartCrosshair(crosshair, canvas, target);
        showChartTooltip(canvas, tooltip, target, options.tooltipRenderer(target));
    };

    const leaveHandler = () => {
        hideChartTooltip(tooltip);
        hideChartCrosshair(crosshair);
    };

    const clickHandler = (event: MouseEvent) => {
        const target = resolveChartPointFromEvent(canvas, points, event, { snapToAxis });
        if (!target) {
            return;
        }
        event.preventDefault();
        options.onPointSelected?.(target);
    };

    canvas.addEventListener('mousemove', moveHandler);
    canvas.addEventListener('mouseleave', leaveHandler);
    canvas.addEventListener('click', clickHandler);
    chartInteractionRegistry.set(canvas, { move: moveHandler, leave: leaveHandler, click: clickHandler });
}

export function clearChartInteractivity(canvas: HTMLCanvasElement, containerSelector?: string): void {
    const handlers = chartInteractionRegistry.get(canvas);
    if (handlers) {
        canvas.removeEventListener('mousemove', handlers.move);
        canvas.removeEventListener('mouseleave', handlers.leave);
        canvas.removeEventListener('click', handlers.click);
        chartInteractionRegistry.delete(canvas);
    }
    const tooltip = ensureChartTooltip(canvas, containerSelector);
    const crosshair = ensureChartCrosshair(canvas, containerSelector);
    hideChartTooltip(tooltip);
    hideChartCrosshair(crosshair);
}

export function focusVariant(variantIdx: number, options?: { navigateToUpdates?: boolean; scroll?: boolean }): void {
    if (!Number.isFinite(variantIdx)) return;
    const resolvedIdx = Math.max(0, Math.round(variantIdx));
    const api = getDashboardSpsaApi();
    if (!api) {
        console.warn('[SPSA] Dashboard API is not available to focus update from chart.');
        return;
    }
    if (options?.navigateToUpdates !== false && typeof api.switchTab === 'function') {
        api.switchTab('updates');
    }
    if (typeof api.focusUpdate === 'function') {
        api.focusUpdate(resolvedIdx, { scroll: options?.scroll !== false });
    }
}

type ResolvePointOptions = {
    snapToAxis?: boolean;
};

function resolveChartPointFromEvent<T extends InteractiveChartPoint>(
    canvas: HTMLCanvasElement,
    points: readonly T[],
    event: MouseEvent,
    options: ResolvePointOptions,
): T | null {
    if (!points.length) return null;
    const rect = canvas.getBoundingClientRect();
    const borders = getCanvasBorderOffsets(canvas);
    const clickX = event.clientX - rect.left - borders.left;
    const clickY = event.clientY - rect.top - borders.top;
    const width = Math.max(0, rect.width - borders.left - borders.right);
    const height = Math.max(0, rect.height - borders.top - borders.bottom);
    const thresholdSq = CHART_INTERACTION_DISTANCE_PX * CHART_INTERACTION_DISTANCE_PX;
    let closest: T | null = null;
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
    if (closest || options.snapToAxis === false) {
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
    let axisClosest: T | null = null;
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

function ensureChartTooltip(canvas: HTMLCanvasElement, selector?: string): ChartTooltip | null {
    const container = resolveContainer(canvas, selector);
    if (!container) return null;
    let element = container.querySelector<HTMLElement>('[data-role="convergence-chart-tooltip"]');
    if (!element) {
        element = document.createElement('div');
        element.dataset.role = 'convergence-chart-tooltip';
        element.className = 'parameter-chart-tooltip';
        element.style.display = 'none';
        container.appendChild(element);
    }
    return { element, container };
}

function ensureChartCrosshair(canvas: HTMLCanvasElement, selector?: string): ChartCrosshair | null {
    const container = resolveContainer(canvas, selector);
    if (!container) return null;
    let element = container.querySelector<HTMLElement>('[data-role="convergence-chart-crosshair"]');
    if (!element) {
        element = document.createElement('div');
        element.dataset.role = 'convergence-chart-crosshair';
        element.className = 'parameter-chart-crosshair';
        element.style.display = 'none';
        container.appendChild(element);
    }
    return { element, container };
}

function showChartTooltip(
    canvas: HTMLCanvasElement,
    tooltip: ChartTooltip,
    point: InteractiveChartPoint,
    html: string,
): void {
    const rect = canvas.getBoundingClientRect();
    const containerRect = tooltip.container.getBoundingClientRect();
    tooltip.element.style.display = 'block';
    tooltip.element.innerHTML = html;
    tooltip.element.style.left = `${rect.left + point.x - containerRect.left + 12}px`;
    tooltip.element.style.top = `${rect.top + point.y - containerRect.top - 12}px`;
}

function hideChartTooltip(tooltip: ChartTooltip | null): void {
    if (!tooltip) return;
    tooltip.element.style.display = 'none';
}

function showChartCrosshair(
    crosshair: ChartCrosshair | null,
    canvas: HTMLCanvasElement,
    point: InteractiveChartPoint,
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

function resolveContainer(canvas: HTMLCanvasElement, selector?: string): HTMLElement | null {
    if (selector) {
        const scoped = canvas.closest<HTMLElement>(selector);
        if (scoped) {
            return scoped;
        }
    }
    return canvas.parentElement instanceof HTMLElement ? canvas.parentElement : null;
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
