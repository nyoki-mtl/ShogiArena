import { SPARK_HISTORY_MS } from '@/modules/instances/state/constants';
import type { InstancesDashboardState } from '@/modules/instances/state';

interface SparklinePoint {
    readonly ts: number;
    readonly value: number | null;
}

function injectDomainPoints(seriesPoints: readonly SparklinePoint[], fromTs: number, toTs: number): SparklinePoint[] {
    if (!Array.isArray(seriesPoints) || seriesPoints.length === 0) return [];
    const pts = seriesPoints.slice();

    const ensurePointAt = (targetTs: number) => {
        if (!pts.length) return;
        const first = pts[0];
        if (first && first.ts >= targetTs) {
            if (first.ts === targetTs) return;
            pts.unshift({ ts: targetTs, value: first.value });
            return;
        }
        const last = pts[pts.length - 1];
        if (last && last.ts <= targetTs) {
            if (last.ts === targetTs) return;
            pts.push({ ts: targetTs, value: last.value });
            return;
        }
        for (let i = 1; i < pts.length; i++) {
            const prev = pts[i - 1];
            const next = pts[i];
            if (!prev || !next) continue;
            if (prev.ts <= targetTs && next.ts >= targetTs) {
                if (prev.ts === targetTs || next.ts === targetTs) return;
                pts.splice(i, 0, { ts: targetTs, value: prev.value });
                return;
            }
        }
    };

    const hasLeftPoint = pts.some((point) => typeof point.ts === 'number' && point.ts < fromTs);
    if (hasLeftPoint) {
        ensurePointAt(fromTs);
        while (pts.length && pts[0] && pts[0].ts < fromTs) {
            pts.shift();
        }
    }
    ensurePointAt(toTs);
    return pts;
}

function collectCanvases(ids?: readonly string[]): HTMLCanvasElement[] {
    if (!ids || ids.length === 0) {
        return Array.from(document.querySelectorAll<HTMLCanvasElement>('canvas.inst-spark'));
    }
    const canvases: HTMLCanvasElement[] = [];
    ids.forEach((id) => {
        const safeId = typeof CSS !== 'undefined' && typeof CSS.escape === 'function' ? CSS.escape(id) : id;
        canvases.push(
            ...Array.from(document.querySelectorAll<HTMLCanvasElement>(`canvas.inst-spark[data-id="${safeId}"]`)),
        );
    });
    return canvases;
}

export function drawSparklines(state: InstancesDashboardState, ids?: readonly string[]): void {
    const now = Date.now();
    const startTs = now - SPARK_HISTORY_MS;
    const canvases = collectCanvases(ids);
    if (canvases.length === 0) {
        return;
    }
    canvases.forEach((canvas) => {
        // A canvas whose control was transferred to the OffscreenCanvas worker can no longer
        // return a 2D context on the main thread; getContext() would throw InvalidStateError.
        // Skip it so a worker failure cannot break the whole main-thread sparkline fallback.
        if (canvas.dataset.offscreenTransferred === 'true') {
            return;
        }
        const instId = canvas.dataset.id || '';
        const kind = canvas.dataset.kind || 'cpu';
        const history = state.history[instId];
        if (!history) {
            const ctx = canvas.getContext('2d');
            ctx?.clearRect(0, 0, canvas.width, canvas.height);
            return;
        }
        const sourceSeries = (() => {
            if (kind === 'mem') {
                return history.mem;
            }
            if (kind === 'core' && canvas.dataset.core) {
                const series = history.cores[canvas.dataset.core];
                return series ?? [];
            }
            return history.cpu;
        })();

        const markerPoints: SparklinePoint[] = sourceSeries
            .map((point) => ({
                ts: typeof point.ts === 'number' ? point.ts : now,
                value: typeof point.value === 'number' ? point.value : null,
            }))
            .filter((point) => point.value != null)
            .sort((a, b) => a.ts - b.ts);

        const ctx = canvas.getContext('2d');
        if (!ctx) return;
        if (markerPoints.length === 0) {
            ctx.clearRect(0, 0, canvas.width, canvas.height);
            return;
        }

        const linePoints =
            markerPoints.length >= 2 ? injectDomainPoints(markerPoints, startTs, now) : markerPoints.slice();

        const rect = canvas.getBoundingClientRect();
        const ratio = window.devicePixelRatio || 1;
        const baseWidth = kind === 'core' ? 180 : 240;
        const baseHeight = kind === 'core' ? 80 : 96;
        const width = Math.max(baseWidth, Math.floor(rect.width || canvas.width || baseWidth));
        const height = baseHeight;
        if (canvas.width !== width * ratio || canvas.height !== height * ratio) {
            canvas.width = width * ratio;
            canvas.height = height * ratio;
        }
        ctx.setTransform(1, 0, 0, 1, 0, 0);
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        ctx.scale(ratio, ratio);

        const min = 0;
        const max = Math.max(100, Math.max(...linePoints.map((p) => p.value ?? 0)));
        const padLeft = 10;
        const padTop = 10;
        const padRight = kind === 'core' ? 24 : 42;
        const padBottom = kind === 'core' ? 20 : 34;
        const w = width - padLeft - padRight;
        const h = height - padTop - padBottom;
        const range = max - min || 1;
        const lineColor = kind === 'mem' ? '#d97706' : '#4c51bf';
        ctx.lineWidth = 1.5;
        ctx.strokeStyle = lineColor;

        ctx.save();
        ctx.strokeStyle = '#e5e7eb';
        ctx.lineWidth = 1;
        const yTicks = [0, 25, 50, 75, 100];
        ctx.font = '10px system-ui';
        ctx.textAlign = 'left';
        yTicks.forEach((tick) => {
            const y = padTop + (max === min ? h / 2 : h - (tick - min) * (h / range));
            ctx.beginPath();
            ctx.moveTo(padLeft, y);
            ctx.lineTo(padLeft + w, y);
            ctx.stroke();
            ctx.fillStyle = '#6b7280';
            ctx.textBaseline = 'middle';
            ctx.fillText(`${tick}%`, padLeft + w + 4, y);
        });
        const xTicks = [-60, -50, -40, -30, -20, -10, 0];
        ctx.textAlign = 'center';
        ctx.textBaseline = 'top';
        xTicks.forEach((tick) => {
            const clampedTick = Math.max(-60, Math.min(0, tick));
            const x = padLeft + ((clampedTick + 60) / 60) * w;
            ctx.strokeStyle = '#e5e7eb';
            ctx.beginPath();
            ctx.moveTo(x, padTop);
            ctx.lineTo(x, padTop + h);
            ctx.stroke();
            ctx.fillStyle = '#6b7280';
            ctx.fillText(`${clampedTick}s`, x, padTop + h + 4);
        });
        ctx.restore();

        if (linePoints.length >= 2) {
            ctx.beginPath();
            linePoints.forEach((p, index) => {
                const clampedTs = Math.min(now, Math.max(startTs, p.ts));
                const xNorm = (clampedTs - startTs) / SPARK_HISTORY_MS;
                const x = padLeft + w * xNorm;
                const y = padTop + (max === min ? h / 2 : h - ((p.value ?? min) - min) * (h / range));
                if (index === 0) {
                    ctx.moveTo(x, y);
                } else {
                    ctx.lineTo(x, y);
                }
            });
            ctx.stroke();
        }
        ctx.fillStyle = lineColor;
        markerPoints.forEach((p) => {
            const clampedTs = Math.min(now, Math.max(startTs, p.ts));
            if (clampedTs <= startTs || clampedTs > now) return;
            const xNorm = (clampedTs - startTs) / SPARK_HISTORY_MS;
            const x = padLeft + w * xNorm;
            const y = padTop + (max === min ? h / 2 : h - ((p.value ?? min) - min) * (h / range));
            ctx.beginPath();
            if (typeof ctx.arc === 'function') {
                ctx.arc(x, y, 2.5, 0, Math.PI * 2);
                ctx.fill();
            } else if (typeof ctx.fillRect === 'function') {
                ctx.fillRect(x - 2, y - 2, 4, 4);
            }
        });
        ctx.setTransform(1, 0, 0, 1, 0, 0);
    });
}
