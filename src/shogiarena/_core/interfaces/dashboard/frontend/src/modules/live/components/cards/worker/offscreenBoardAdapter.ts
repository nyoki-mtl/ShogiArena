import type { LiveBoardAdapter } from '@/modules/live/types';
import { recordLiveDiagnosticsMetric } from '@/modules/live/utils/liveNamespace/metrics';
import { isTestEnvironment } from '@/modules/shared/utils/env';
import OffscreenBoardWorker from './offscreenBoardWorker?worker';
import offscreenBoardWorkerUrl from './offscreenBoardWorker?worker&url';

type ShogiBoardAdapterCtor = new () => LiveBoardAdapter;

function isVerboseOffscreenDebug(): boolean {
    try {
        const params = new URLSearchParams(window?.location?.search ?? '');
        return params.get('offscreenDebug') === '1';
    } catch {
        return false;
    }
}

function getAssetBaseUrl(): string | null {
    try {
        // Vite injects BASE_URL at build time.
        const meta = import.meta as unknown as { env?: Record<string, unknown> };
        const base = meta.env?.BASE_URL;
        return typeof base === 'string' ? base : null;
    } catch {
        return null;
    }
}

function describeErrorForConsole(error: unknown): unknown {
    if (!error) return null;
    if (error instanceof Error) {
        return { name: error.name, message: error.message, stack: error.stack ?? null };
    }
    return describeWorkerError(error);
}

function describeWorkerError(error: unknown): unknown {
    if (!error) return null;
    if (error instanceof Error) {
        return { name: error.name, message: error.message, stack: error.stack ?? null };
    }
    if (typeof error === 'object') {
        const anyErr = error as Record<string, unknown>;
        const message = typeof anyErr.message === 'string' ? anyErr.message : null;
        const filename = typeof anyErr.filename === 'string' ? anyErr.filename : null;
        const lineno = typeof anyErr.lineno === 'number' ? anyErr.lineno : null;
        const colno = typeof anyErr.colno === 'number' ? anyErr.colno : null;
        const type =
            typeof (anyErr as { type?: unknown }).type === 'string' ? (anyErr as { type?: string }).type : null;
        const innerError = anyErr.error instanceof Error ? anyErr.error : null;
        if (message || filename || lineno != null || colno != null || type || innerError) {
            return {
                type,
                message,
                filename,
                lineno,
                colno,
                error: innerError
                    ? { name: innerError.name, message: innerError.message, stack: innerError.stack }
                    : null,
            };
        }
    }
    return error;
}

function hasOffscreenSupport(): boolean {
    if (typeof OffscreenCanvas !== 'function') return false;
    if (typeof Worker !== 'function') return false;
    // best-effort: check transferControlToOffscreen
    return typeof HTMLCanvasElement !== 'undefined' && 'transferControlToOffscreen' in HTMLCanvasElement.prototype;
}

class OffscreenBoardAdapter implements LiveBoardAdapter {
    [key: string]: unknown;
    private canvas: HTMLCanvasElement | null = null;
    private worker: Worker | null = null;
    private fallback: LiveBoardAdapter | null = null;
    private dpr = 1;
    private host: Element | null = null;
    private offscreenActive = false;
    private fallbackEmitted = false;
    private lastSfen: string | null = null;
    private lastMoves: string[] | null = null;
    private lastMovesKey: string | null = null;
    private lastMovesRev: number | null = null;
    private lastPly: number | null = null;
    private lastHighlights: number[] | null = null;
    private lastSize: { width: number; height: number } | null = null;
    private lastFlip: boolean | null = null;
    private lastTheme: Record<string, unknown> | null = null;
    private verbose = false;
    private workerScriptUrl: string | null = null;
    private workerScriptProbeStarted = false;
    private fallbackLogged = false;

    constructor(fallbackCtor: ShogiBoardAdapterCtor | null) {
        this.fallback = fallbackCtor ? new fallbackCtor() : null;
    }

    mount(target: Element): void {
        const verbose = isVerboseOffscreenDebug();
        this.verbose = verbose;
        if (verbose) {
            console.info?.('[OffscreenBoardAdapter] mount start', {
                hasOffscreenSupport: hasOffscreenSupport(),
                crossOriginIsolated: typeof crossOriginIsolated === 'boolean' ? crossOriginIsolated : null,
                assetBaseUrl: getAssetBaseUrl(),
                location: window?.location?.href ?? null,
                userAgent: navigator?.userAgent ?? null,
            });
        }
        if (!hasOffscreenSupport()) {
            this.emitFallbackMetric('unsupported');
            this.fallback?.mount?.(target);
            return;
        }
        this.host = target;
        this.dpr = window?.devicePixelRatio ?? 1;
        const canvas = document.createElement('canvas');
        canvas.className = 'live-board-offscreen-canvas';
        canvas.style.width = '100%';
        canvas.style.height = '100%';
        const cssW = Math.max(1, Math.floor(target.clientWidth || 640));
        const cssH = Math.max(1, Math.floor(target.clientHeight || 640));
        canvas.width = Math.max(1, Math.floor(cssW * this.dpr));
        canvas.height = Math.max(1, Math.floor(cssH * this.dpr));
        target.appendChild(canvas);
        this.canvas = canvas;

        try {
            const offscreen = canvas.transferControlToOffscreen();
            this.workerScriptUrl = typeof offscreenBoardWorkerUrl === 'string' ? offscreenBoardWorkerUrl : null;
            this.worker = new OffscreenBoardWorker();
            this.worker.addEventListener('error', (event) => {
                if (verbose) {
                    console.warn?.('[OffscreenBoardAdapter] worker error event', describeWorkerError(event));
                }
                this.fallbackToMainThread('worker-error', event);
            });
            this.worker.addEventListener('messageerror', (event) => {
                if (verbose) {
                    console.warn?.('[OffscreenBoardAdapter] worker messageerror event', describeWorkerError(event));
                }
                this.fallbackToMainThread('worker-error', event);
            });
            this.worker.addEventListener('message', (event: MessageEvent) => {
                const data = event.data;
                if (!data || typeof data !== 'object') return;
                if ((data as { type?: unknown }).type === '__error__') {
                    this.fallbackToMainThread('worker-error', (data as { error?: unknown }).error);
                }
            });
            this.worker.postMessage({ type: 'init', canvas: offscreen }, [offscreen]);
            this.worker.postMessage({
                type: 'resize',
                width: canvas.width,
                height: canvas.height,
                cssWidth: cssW,
                cssHeight: cssH,
                dpr: this.dpr,
            });
            this.offscreenActive = true;
            this.lastSize = { width: canvas.width, height: canvas.height };
        } catch (error) {
            this.fallbackToMainThread('init-failed', error);
        }
    }

    setPositionFromSFEN?(sfen: string): void {
        this.lastSfen = sfen;
        if (this.worker && this.offscreenActive) {
            this.worker.postMessage({ type: 'position', sfen });
        } else {
            this.fallback?.setPositionFromSFEN?.(sfen);
        }
    }

    setMoves?(moves: readonly string[]): void {
        this.applyMoves(moves, null);
    }

    setMovesWithRevision?(moves: readonly string[], rev: number): void {
        const safeRev = typeof rev === 'number' && Number.isFinite(rev) ? Math.trunc(rev) : 0;
        this.applyMoves(moves, safeRev);
    }

    private applyMoves(moves: readonly string[], rev: number | null): void {
        const list = Array.isArray(moves) ? (moves as string[]) : [];
        const first = list.length ? String(list[0] ?? '') : '';
        const last = list.length ? String(list[list.length - 1] ?? '') : '';
        const key = `${rev ?? 0}:${list.length}:${first}:${last}`;
        // boardSync は毎回 setMoves するので、同一内容なら postMessage を避ける
        if (this.lastMovesKey === key) {
            return;
        }
        if (this.worker && this.offscreenActive) {
            const previous = this.lastMoves;
            const canAppend =
                rev != null &&
                this.lastMovesRev != null &&
                rev === this.lastMovesRev &&
                previous &&
                previous.length > 0 &&
                list.length >= previous.length;
            if (canAppend) {
                const prevLen = previous.length;
                const prevFirst = String(previous[0] ?? '');
                const prevLast = String(previous[prevLen - 1] ?? '');
                const isAppendOnly =
                    (list.length === prevLen && key === this.lastMovesKey) ||
                    (list.length > prevLen &&
                        String(list[0] ?? '') === prevFirst &&
                        String(list[prevLen - 1] ?? '') === prevLast);
                if (isAppendOnly) {
                    const appended = list.slice(prevLen).map((mv) => String(mv ?? ''));
                    this.lastMovesKey = key;
                    this.lastMovesRev = rev;
                    if (!appended.length) {
                        return;
                    }
                    // Mutate local cache to avoid re-cloning the full moves array every tick.
                    previous.push(...appended);
                    this.worker.postMessage({ type: 'append_moves', moves: appended });
                    return;
                }
            }
            this.lastMovesKey = key;
            this.lastMovesRev = rev ?? 0;
            this.lastMoves = list.map((mv) => String(mv ?? ''));
            // Keep an internal cache we can mutate, but avoid handing the same array reference
            // to postMessage (tests mock postMessage by reference; real postMessage clones).
            this.worker.postMessage({ type: 'moves', moves: [...this.lastMoves] });
        } else {
            this.lastMovesKey = key;
            this.lastMovesRev = rev ?? 0;
            this.lastMoves = list.map((mv) => String(mv ?? ''));
            this.fallback?.setMoves?.(this.lastMoves);
        }
    }

    goTo?(ply: number): void {
        this.lastPly = Number.isFinite(ply) ? Math.max(0, Math.floor(ply)) : 0;
        if (this.worker && this.offscreenActive) {
            this.worker.postMessage({ type: 'goto', ply: this.lastPly });
        } else {
            this.fallback?.goTo?.(this.lastPly);
        }
    }

    highlightSquares?(squares: Iterable<number>): void {
        const list = Array.from(squares ?? []);
        this.lastHighlights = list;
        if (this.worker && this.offscreenActive) {
            this.worker.postMessage({ type: 'highlight', squares: list });
        } else {
            this.fallback?.highlightSquares?.(list);
        }
    }

    flip?(enabled: boolean): void {
        this.lastFlip = Boolean(enabled);
        if (this.worker && this.offscreenActive) {
            this.worker.postMessage({ type: 'flip', enabled: this.lastFlip });
            return;
        }
        const fallback = this.fallback as unknown as { flip?: unknown };
        if (typeof fallback.flip === 'function') {
            fallback.flip(this.lastFlip);
        }
    }

    setTheme?(theme: Record<string, unknown>): void {
        this.lastTheme = theme ? { ...theme } : null;
        if (this.worker && this.offscreenActive) {
            this.worker.postMessage({ type: 'theme', theme: this.lastTheme ?? {} });
            return;
        }
        const fallback = this.fallback as unknown as { setTheme?: unknown };
        if (typeof fallback.setTheme === 'function') {
            fallback.setTheme(this.lastTheme ?? {});
        }
    }

    resize?(): void {
        if (!this.canvas) {
            this.fallback?.resize?.();
            return;
        }
        const rect = this.canvas.getBoundingClientRect();
        const cssW = Math.max(1, Math.floor(rect.width || this.canvas.width));
        const cssH = Math.max(1, Math.floor(rect.height || this.canvas.height));
        const w = Math.max(1, Math.floor(cssW * this.dpr));
        const h = Math.max(1, Math.floor(cssH * this.dpr));
        this.lastSize = { width: w, height: h };
        if (this.worker && this.offscreenActive) {
            this.worker.postMessage({
                type: 'resize',
                width: w,
                height: h,
                cssWidth: cssW,
                cssHeight: cssH,
                dpr: this.dpr,
            });
        } else {
            this.fallback?.resize?.();
        }
    }

    destroy?(): void {
        this.worker?.terminate();
        this.worker = null;
        this.fallback?.destroy?.();
        if (this.canvas?.parentElement) {
            this.canvas.parentElement.removeChild(this.canvas);
        }
        this.canvas = null;
        this.offscreenActive = false;
        this.host = null;
    }

    private fallbackToMainThread(reason: string, error?: unknown): void {
        if (!this.fallbackLogged) {
            this.fallbackLogged = true;
            const base = getAssetBaseUrl();
            if (!isTestEnvironment() || this.verbose) {
                console.warn?.(
                    '[OffscreenBoardAdapter] OffscreenCanvas disabled; falling back to main-thread adapter.',
                    {
                        reason,
                        error: describeErrorForConsole(error),
                        workerScriptUrl: this.workerScriptUrl,
                        assetBaseUrl: base,
                        crossOriginIsolated: typeof crossOriginIsolated === 'boolean' ? crossOriginIsolated : null,
                    },
                );
            }
            if (reason === 'worker-error' || reason === 'init-failed') {
                void this.probeWorkerScriptUrl();
            }
        } else if (this.verbose) {
            console.warn?.(
                `OffscreenCanvas disabled (${reason}); falling back to main-thread adapter.`,
                describeErrorForConsole(error),
            );
        }
        this.offscreenActive = false;
        this.worker?.terminate();
        this.worker = null;
        if (this.canvas?.parentElement) {
            this.canvas.parentElement.removeChild(this.canvas);
        }
        if (this.host) {
            this.fallback?.mount?.(this.host);
            // フォールバック直後は、直前まで Offscreen 側に投げていた状態を main-thread 側へ再適用する
            // （この再適用が無いと “盤面が崩れたまま” が残りやすい）
            try {
                if (this.lastSfen && typeof this.fallback?.setPositionFromSFEN === 'function') {
                    this.fallback.setPositionFromSFEN(this.lastSfen);
                }
                if (this.lastMoves && typeof this.fallback?.setMoves === 'function') {
                    this.fallback.setMoves(this.lastMoves);
                }
                if (this.lastFlip != null) {
                    const fallback = this.fallback as unknown as { flip?: unknown };
                    if (typeof fallback.flip === 'function') {
                        fallback.flip(this.lastFlip);
                    }
                }
                if (this.lastTheme) {
                    const fallback = this.fallback as unknown as { setTheme?: unknown };
                    if (typeof fallback.setTheme === 'function') {
                        fallback.setTheme(this.lastTheme);
                    }
                }
                if (this.lastPly != null && typeof this.fallback?.goTo === 'function') {
                    this.fallback.goTo(this.lastPly);
                }
                if (this.lastHighlights && typeof this.fallback?.highlightSquares === 'function') {
                    this.fallback.highlightSquares(this.lastHighlights);
                }
                if (this.lastSize && typeof this.fallback?.resize === 'function') {
                    this.fallback.resize();
                }
            } catch (error) {
                console.warn?.('Failed to replay board state after offscreen fallback', error);
            }
        }
        this.emitFallbackMetric(reason);
    }

    private async probeWorkerScriptUrl(): Promise<void> {
        if (this.workerScriptProbeStarted) return;
        this.workerScriptProbeStarted = true;
        if (isTestEnvironment()) return;
        const url = this.workerScriptUrl;
        if (!url) return;
        try {
            const response = await fetch(url, { method: 'GET', cache: 'no-store' });
            console.warn?.('[OffscreenBoardAdapter] Worker script probe', {
                url,
                ok: response.ok,
                status: response.status,
                contentType: response.headers.get('content-type'),
            });
        } catch (error) {
            console.warn?.('[OffscreenBoardAdapter] Worker script probe failed', {
                url,
                error: describeErrorForConsole(error),
            });
        }
    }

    private emitFallbackMetric(reason: string): void {
        if (this.fallbackEmitted) return;
        this.fallbackEmitted = true;
        const reasonCodes: Record<string, number> = {
            unsupported: 1,
            'init-failed': 2,
            'worker-error': 3,
        };
        recordLiveDiagnosticsMetric('live.board.offscreen_fallback', {
            triggered: 1,
            reason_code: reasonCodes[reason] ?? 0,
        });
    }
}

export function supportsOffscreenCanvas(): boolean {
    return hasOffscreenSupport();
}

export function createOffscreenBoardAdapterCtor(fallbackCtor: ShogiBoardAdapterCtor | null): ShogiBoardAdapterCtor {
    return class OffscreenBoardAdapterCtor extends OffscreenBoardAdapter {
        constructor() {
            super(fallbackCtor);
        }
    };
}
