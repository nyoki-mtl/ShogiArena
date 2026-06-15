import { computeIndexTicks, computeNiceTicks, formatTickLabel } from '@/modules/shared/utils/chart';
import { escapeHtml } from '@/modules/shared/utils/html';

export interface LineChartPoint {
    x: number;
    y: number | null;
}

export interface LineChartSeries {
    id: string;
    label: string;
    color: string;
    points: LineChartPoint[];
}

export interface LineChartOptions {
    minY?: number;
    maxY?: number;
    yTickCount?: number;
    xTickCount?: number;
    yFormatter?: (value: number) => string;
    xFormatter?: (value: number) => string;
    showLegend?: boolean;
    height?: number;
    width?: number;
}

const DEFAULT_WIDTH = 640;
const DEFAULT_HEIGHT = 240;

const DEFAULT_PADDING = {
    top: 16,
    right: 16,
    bottom: 28,
    left: 48,
};

function computeBounds(series: readonly LineChartSeries[]): { minX: number; maxX: number; minY: number; maxY: number } {
    let minX = Number.POSITIVE_INFINITY;
    let maxX = Number.NEGATIVE_INFINITY;
    let minY = Number.POSITIVE_INFINITY;
    let maxY = Number.NEGATIVE_INFINITY;

    for (const s of series) {
        for (const point of s.points) {
            if (!Number.isFinite(point.x)) {
                continue;
            }
            minX = Math.min(minX, point.x);
            maxX = Math.max(maxX, point.x);
            if (point.y === null || !Number.isFinite(point.y)) {
                continue;
            }
            minY = Math.min(minY, point.y);
            maxY = Math.max(maxY, point.y);
        }
    }

    if (!Number.isFinite(minX) || !Number.isFinite(maxX)) {
        minX = 0;
        maxX = 1;
    }
    if (!Number.isFinite(minY) || !Number.isFinite(maxY)) {
        minY = 0;
        maxY = 1;
    }
    if (minX === maxX) {
        minX -= 1;
        maxX += 1;
    }
    if (minY === maxY) {
        const offset = Math.abs(minY) || 1;
        minY -= offset;
        maxY += offset;
    }

    return { minX, maxX, minY, maxY };
}

function buildPath(
    points: readonly LineChartPoint[],
    mapper: (point: LineChartPoint) => [number, number] | null,
): string {
    let path = '';
    let started = false;
    for (const point of points) {
        if (point.y === null || !Number.isFinite(point.y)) {
            started = false;
            continue;
        }
        const mapped = mapper(point);
        if (!mapped) {
            started = false;
            continue;
        }
        const [x, y] = mapped;
        if (!started) {
            path += `M ${x} ${y}`;
            started = true;
        } else {
            path += ` L ${x} ${y}`;
        }
    }
    return path;
}

export function renderLineChart(
    container: HTMLElement,
    series: readonly LineChartSeries[],
    options: LineChartOptions = {},
): void {
    if (!container) {
        return;
    }
    if (!series.length) {
        container.innerHTML = '<div class="text-sm text-dashboard-neutral">No data yet.</div>';
        return;
    }

    const width = options.width ?? DEFAULT_WIDTH;
    const height = options.height ?? DEFAULT_HEIGHT;
    const padding = DEFAULT_PADDING;

    const bounds = computeBounds(series);
    const minY = Number.isFinite(options.minY ?? NaN) ? (options.minY as number) : bounds.minY;
    const maxY = Number.isFinite(options.maxY ?? NaN) ? (options.maxY as number) : bounds.maxY;
    const minX = bounds.minX;
    const maxX = bounds.maxX;

    const plotWidth = Math.max(1, width - padding.left - padding.right);
    const plotHeight = Math.max(1, height - padding.top - padding.bottom);

    const mapPoint = (point: LineChartPoint): [number, number] | null => {
        if (!Number.isFinite(point.x) || point.y === null || !Number.isFinite(point.y)) {
            return null;
        }
        const xRatio = (point.x - minX) / (maxX - minX || 1);
        const yRatio = (point.y - minY) / (maxY - minY || 1);
        const x = padding.left + xRatio * plotWidth;
        const y = padding.top + (1 - yRatio) * plotHeight;
        return [Number(x.toFixed(2)), Number(y.toFixed(2))];
    };

    const yTicks = computeNiceTicks(minY, maxY, options.yTickCount ?? 5);
    const xTicks = computeIndexTicks(Math.max(1, Math.floor(maxX - minX + 1)), options.xTickCount ?? 5);

    const yFormatter = options.yFormatter ?? ((value: number) => formatTickLabel(value, yTicks.step));
    const xFormatter =
        options.xFormatter ??
        ((value: number) => {
            if (!Number.isFinite(value)) return '';
            return String(value);
        });

    const axisLines = [
        `<line x1="${padding.left}" y1="${padding.top}" x2="${padding.left}" y2="${padding.top + plotHeight}" class="line-chart__axis"/>`,
        `<line x1="${padding.left}" y1="${padding.top + plotHeight}" x2="${padding.left + plotWidth}" y2="${padding.top + plotHeight}" class="line-chart__axis"/>`,
    ];

    const gridLines = yTicks.ticks
        .map((tick) => {
            const y = padding.top + (1 - (tick - yTicks.min) / (yTicks.max - yTicks.min || 1)) * plotHeight;
            return `<line x1="${padding.left}" y1="${y}" x2="${padding.left + plotWidth}" y2="${y}" class="line-chart__grid"/>`;
        })
        .join('');

    const yLabels = yTicks.ticks
        .map((tick) => {
            const y = padding.top + (1 - (tick - yTicks.min) / (yTicks.max - yTicks.min || 1)) * plotHeight;
            return `<text x="${padding.left - 8}" y="${y}" class="line-chart__label" text-anchor="end">${escapeHtml(
                yFormatter(tick),
            )}</text>`;
        })
        .join('');

    const xLabels = xTicks
        .map((tick) => {
            const x = padding.left + ((tick - 0) / Math.max(1, xTicks[xTicks.length - 1] || 1)) * plotWidth;
            return `<text x="${x}" y="${padding.top + plotHeight + 18}" class="line-chart__label" text-anchor="middle">${escapeHtml(
                xFormatter(tick + minX),
            )}</text>`;
        })
        .join('');

    const paths = series
        .map((s) => {
            const path = buildPath(s.points, mapPoint);
            if (!path) return '';
            return `<path d="${path}" fill="none" stroke="${escapeHtml(s.color)}" stroke-width="2" class="line-chart__path"/>`;
        })
        .join('');

    const legend =
        options.showLegend === false
            ? ''
            : `<div class="line-chart__legend">${series
                  .map(
                      (s) =>
                          `<div class="line-chart__legend-item"><span class="line-chart__legend-swatch" style="background:${escapeHtml(
                              s.color,
                          )}"></span>${escapeHtml(s.label)}</div>`,
                  )
                  .join('')}</div>`;

    container.innerHTML = `
        <div class="line-chart">
            <svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Line chart" preserveAspectRatio="none">
                ${gridLines}
                ${axisLines.join('')}
                ${paths}
                ${yLabels}
                ${xLabels}
            </svg>
            ${legend}
        </div>
    `;
}
