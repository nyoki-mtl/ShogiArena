type SparklineKind = 'cpu' | 'mem' | 'core';

interface SparklinePoint {
    readonly ts: number;
    readonly value: number | null;
}

interface RegisteredCanvas {
    canvas: OffscreenCanvas;
    ctx: OffscreenCanvasRenderingContext2D;
    kind: SparklineKind;
    core: string | null;
    baseWidth: number;
    baseHeight: number;
}

type RegisterMessage = {
    type: 'register';
    canvasId: string;
    instanceId: string;
    kind: SparklineKind;
    core?: string | null;
    baseWidth: number;
    baseHeight: number;
    canvas: OffscreenCanvas;
};

type UnregisterMessage = {
    type: 'unregister';
    canvasId: string;
};

type RenderMessage = {
    type: 'render';
    canvasId: string;
    kind: SparklineKind;
    now: number;
    startTs: number;
    points: SparklinePoint[];
    width: number;
    height: number;
    devicePixelRatio: number;
};

type SparklineMessage = RegisterMessage | UnregisterMessage | RenderMessage;

const canvases = new Map<string, RegisteredCanvas>();
const gridCache = new Map<string, OffscreenCanvas>();

const yTicks = [0, 25, 50, 75, 100];
const xTicks = [-60, -50, -40, -30, -20, -10, 0];

function injectDomainPoints(seriesPoints: SparklinePoint[], fromTs: number, toTs: number): SparklinePoint[] {
    if (!Array.isArray(seriesPoints) || seriesPoints.length === 0) return [];
    const pts = seriesPoints.slice();

    const hasLeftPoint = pts.some((point) => typeof point.ts === 'number' && point.ts < fromTs);

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
        for (let i = 1; i < pts.length; i += 1) {
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

    if (hasLeftPoint) {
        ensurePointAt(fromTs);
        while (pts.length && pts[0] && pts[0].ts < fromTs) {
            pts.shift();
        }
    }
    ensurePointAt(toTs);
    return pts;
}

function getGridCanvas(kind: SparklineKind, width: number, height: number, ratio: number): OffscreenCanvas {
    const key = `${kind}:${width}:${height}:${ratio}`;
    const cached = gridCache.get(key);
    if (cached) return cached;

    const canvas = new OffscreenCanvas(width * ratio, height * ratio);
    const ctx = canvas.getContext('2d');
    if (!ctx) {
        gridCache.set(key, canvas);
        return canvas;
    }

    const padLeft = 10;
    const padTop = 10;
    const padRight = kind === 'core' ? 24 : 42;
    const padBottom = kind === 'core' ? 20 : 34;
    const w = width - padLeft - padRight;
    const h = height - padTop - padBottom;
    const range = 100;

    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.scale(ratio, ratio);
    ctx.strokeStyle = '#e5e7eb';
    ctx.lineWidth = 1;
    ctx.font = '10px system-ui';
    ctx.textAlign = 'left';
    ctx.textBaseline = 'middle';

    yTicks.forEach((tick) => {
        const y = padTop + (h - (tick / range) * h);
        ctx.beginPath();
        ctx.moveTo(padLeft, y);
        ctx.lineTo(padLeft + w, y);
        ctx.stroke();
        ctx.fillStyle = '#6b7280';
        ctx.fillText(`${tick}%`, padLeft + w + 4, y);
    });

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

    gridCache.set(key, canvas);
    return canvas;
}

function drawSparkline(
    ctx: OffscreenCanvasRenderingContext2D,
    kind: SparklineKind,
    width: number,
    height: number,
    ratio: number,
    now: number,
    startTs: number,
    points: SparklinePoint[],
): void {
    if (!points.length) {
        ctx.setTransform(1, 0, 0, 1, 0, 0);
        ctx.clearRect(0, 0, width * ratio, height * ratio);
        return;
    }

    const markerPoints = points
        .map((point) => ({
            ts: typeof point.ts === 'number' ? point.ts : now,
            value: typeof point.value === 'number' ? point.value : null,
        }))
        .filter((point) => point.value != null)
        .sort((a, b) => a.ts - b.ts);

    if (!markerPoints.length) {
        ctx.setTransform(1, 0, 0, 1, 0, 0);
        ctx.clearRect(0, 0, width * ratio, height * ratio);
        return;
    }

    const linePoints = markerPoints.length >= 2 ? injectDomainPoints(markerPoints, startTs, now) : markerPoints.slice();

    const padLeft = 10;
    const padTop = 10;
    const padRight = kind === 'core' ? 24 : 42;
    const padBottom = kind === 'core' ? 20 : 34;
    const w = width - padLeft - padRight;
    const h = height - padTop - padBottom;

    const min = 0;
    const max = Math.max(100, Math.max(...linePoints.map((p) => p.value ?? 0)));
    const range = max - min || 1;
    const lineColor = kind === 'mem' ? '#d97706' : '#4c51bf';

    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, width * ratio, height * ratio);
    ctx.drawImage(getGridCanvas(kind, width, height, ratio), 0, 0);

    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.scale(ratio, ratio);
    ctx.lineWidth = 1.5;
    ctx.strokeStyle = lineColor;
    ctx.fillStyle = lineColor;

    if (linePoints.length >= 2) {
        ctx.beginPath();
        linePoints.forEach((p, index) => {
            const clampedTs = Math.min(now, Math.max(startTs, p.ts));
            const xNorm = (clampedTs - startTs) / (now - startTs || 1);
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

    markerPoints.forEach((p) => {
        const clampedTs = Math.min(now, Math.max(startTs, p.ts));
        if (clampedTs <= startTs || clampedTs > now) return;
        const xNorm = (clampedTs - startTs) / (now - startTs || 1);
        const x = padLeft + w * xNorm;
        const y = padTop + (max === min ? h / 2 : h - ((p.value ?? min) - min) * (h / range));
        ctx.beginPath();
        ctx.arc(x, y, 2.5, 0, Math.PI * 2);
        ctx.fill();
    });

    ctx.setTransform(1, 0, 0, 1, 0, 0);
}

self.onmessage = (event: MessageEvent<SparklineMessage>) => {
    const payload = event.data;
    if (!payload || typeof payload !== 'object') return;
    if (payload.type === 'register') {
        const ctx = payload.canvas.getContext('2d');
        if (!ctx) return;
        canvases.set(payload.canvasId, {
            canvas: payload.canvas,
            ctx,
            kind: payload.kind,
            core: payload.core ?? null,
            baseWidth: payload.baseWidth,
            baseHeight: payload.baseHeight,
        });
        return;
    }
    if (payload.type === 'unregister') {
        canvases.delete(payload.canvasId);
        return;
    }
    if (payload.type === 'render') {
        const entry = canvases.get(payload.canvasId);
        if (!entry) return;
        const width = Math.max(entry.baseWidth, payload.width);
        const height = entry.baseHeight;
        const ratio = payload.devicePixelRatio || 1;
        if (entry.canvas.width !== width * ratio || entry.canvas.height !== height * ratio) {
            entry.canvas.width = width * ratio;
            entry.canvas.height = height * ratio;
        }
        drawSparkline(entry.ctx, payload.kind, width, height, ratio, payload.now, payload.startTs, payload.points);
    }
};
