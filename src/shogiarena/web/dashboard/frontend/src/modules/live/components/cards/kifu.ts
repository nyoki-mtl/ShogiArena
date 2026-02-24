import type { LiveCardId, LiveCardState } from '@/modules/live/types';
import type { DashboardCore, DashboardCoreState } from '@/types/dashboard';
import { resultCodeToKifJP } from '@/modules/live/utils';
import { formatDurationMs } from '@/modules/shared/utils/format';
import type { KifuHandlers, WorkerSnapshotRecord } from './types';

export interface KifuDeps {
    state: DashboardCoreState;
    owner: Window;
    getWorkerSnapshot: (workerIdx: number) => WorkerSnapshotRecord;
    normalizeSFEN: (sfen: string | null | undefined) => string;
    getStartingPlyNumber: (sfen: string | null | undefined) => number;
    formatRemain: (ms: number | string | null | undefined) => string;
    formatInc: (ms: number | string | null | undefined) => string;
    formatCountUp: (ms: number | string | null | undefined) => string;
    formatByoyomi: (ms: number | string | null | undefined) => string;
    updateElement: (id: string, value: unknown) => void;
    warnSoftFailure: DashboardCore['warnSoftFailure'];
    findCardById: (cardId: LiveCardId) => LiveCardState | undefined;
    getCardList: () => LiveCardState[];
    parseLiveCardId: (raw: string) => LiveCardId;
    computeAutoViewPly: (data: WorkerSnapshotRecord, includeTerminal?: boolean) => number;
    syncWorkerViewFromCard: (card: LiveCardState) => void;
    goToMoveCard: (cardId: LiveCardId, target: number) => Promise<void> | void;
    resolveCardData: (card: LiveCardState) => Promise<WorkerSnapshotRecord | null>;
}

export function createKifuHandlers(deps: KifuDeps): KifuHandlers {
    let kifuOverlay: HTMLDivElement | null = null;
    let kifuPopover: HTMLDivElement | null = null;
    let kifuOpenCard: LiveCardId | null = null;
    let kifuOpenWorker: number | null = null;
    let kifuPopoverDelegated = false;

    function ensureKifuLayers(): void {
        if (!kifuOverlay) {
            const overlay = document.createElement('div');
            overlay.className = 'kifu-overlay';
            overlay.addEventListener('click', () => closeKifuPopover());
            document.body.appendChild(overlay);
            kifuOverlay = overlay;
        }
        if (!kifuPopover) {
            const popover = document.createElement('div');
            popover.className = 'kifu-popover';
            document.body.appendChild(popover);
            kifuPopover = popover;
        }
        if (kifuPopover && !kifuPopoverDelegated) {
            kifuPopover.addEventListener('click', handleKifuLineClick);
            kifuPopoverDelegated = true;
        }
    }

    function positionKifuPopover(anchorEl: Element | null): void {
        if (!kifuPopover || !anchorEl) return;
        const rect = anchorEl.getBoundingClientRect();
        const margin = 6;
        let top = rect.bottom + margin;
        const left = Math.min(rect.left, deps.owner.innerWidth - 340);
        if (top + 360 > deps.owner.innerHeight) {
            top = Math.max(margin, rect.top - 360 - margin);
        }
        kifuPopover.style.top = `${Math.round(top)}px`;
        kifuPopover.style.left = `${Math.round(left)}px`;
    }

    function scrollKifuToCurrent(): void {
        if (!kifuPopover || kifuPopover.style.display === 'none') return;
        const current = kifuPopover.querySelector<HTMLElement>('.kifu-line.current');
        if (!current) return;
        const container = kifuPopover;
        const targetTop = current.offsetTop - (container.clientHeight / 2 - current.clientHeight / 2);
        const maxScroll = container.scrollHeight - container.clientHeight;
        const nextScrollTop = Math.max(0, Math.min(maxScroll, targetTop));
        container.scrollTop = nextScrollTop;
    }

    function handleKifuLineClick(event: MouseEvent): void {
        if (!kifuPopover || kifuPopover.style.display === 'none') return;
        const target = (event.target as Element | null)?.closest<HTMLElement>('.kifu-line');
        if (!target) return;
        const moveRaw = target.dataset.move;
        if (moveRaw == null) return;
        const moveNumber = Number(moveRaw);
        if (!Number.isFinite(moveNumber)) return;
        const move = Math.max(0, Math.trunc(moveNumber));

        const cardIdRaw = target.dataset.cardId;
        if (cardIdRaw != null) {
            const liveCardId = deps.parseLiveCardId(cardIdRaw);
            void deps.goToMoveCard(liveCardId, move);
            return;
        }

        const workerIdxRaw = target.dataset.workerIdx;
        if (workerIdxRaw != null) {
            const workerIdx = Number(workerIdxRaw);
            if (Number.isFinite(workerIdx)) {
                const card = deps
                    .getCardList()
                    .find((c) => typeof c.source === 'string' && c.source === `worker-latest:${workerIdx}`);
                if (!card) {
                    throw new Error(`No worker card found for worker index ${workerIdx}`);
                }
                void deps.goToMoveCard(card.id, move);
            }
        }
    }

    function renderKifuLinesCard(cardId: LiveCardId, data: WorkerSnapshotRecord): string {
        const startNo = deps.getStartingPlyNumber(data.initial_sfen);
        const maxPly = Array.isArray(data.moves) ? data.moves.length : 0;
        const hasTerminal = typeof data.result_code === 'number' && Number.isFinite(data.result_code);
        const terminal = hasTerminal ? formatTerminalLineWithStats(data, maxPly, 'popover') : null;
        const lines: string[] = [];
        for (let i = 0; i < maxPly; i++) {
            const idx1 = i + 1;
            const label = String(startNo + (idx1 - 1)).padStart(3, '0');
            const moveText = formatKifuLineSimple(data, idx1, { includeNumber: false });
            const stats = formatMoveSummaryWithStats(label, moveText, data, idx1, 'popover');
            lines.push(`<div class="kifu-line" data-card-id="${cardId}" data-move="${idx1}">${stats}</div>`);
        }
        if (terminal) {
            lines.push(
                `<div class="kifu-line terminal" data-card-id="${cardId}" data-move="${maxPly + 1}">${terminal}</div>`,
            );
        }
        return lines.join('');
    }

    function renderKifuLines(workerIdx: number): string {
        const card = deps.getCardList().find((c) => c.source === `worker-latest:${workerIdx}`);
        if (!card) return '';
        const snapshot = deps.getWorkerSnapshot(workerIdx);
        return renderKifuLinesCard(card.id, snapshot);
    }

    function updateKI2MovesCard(cardId: LiveCardId, data: WorkerSnapshotRecord): void {
        const container = document.getElementById(`ki2-${cardId}`);
        if (!container) return;
        const moves = Array.isArray(data.ki2_moves) ? data.ki2_moves : [];
        const rows: string[] = [];
        for (let i = 0; i < moves.length; i++) {
            const idx1 = i + 1;
            const move = moves[i] || '-';
            rows.push(`<div class="ki2-line" data-move="${idx1}">${idx1.toString().padStart(3, '0')} ${move}</div>`);
        }
        container.innerHTML = rows.join('');
    }

    function updateKI2Moves(workerIdx: number): void {
        const card = deps.getCardList().find((c) => c.source === `worker-latest:${workerIdx}`);
        if (!card) return;
        const snapshot = deps.getWorkerSnapshot(workerIdx);
        updateKI2MovesCard(card.id, snapshot);
    }

    function getMovePrefix(workerIdx: number, moveIdx1: number): '▲' | '△' {
        const data = deps.getWorkerSnapshot(workerIdx);
        const sfen = deps.normalizeSFEN(data.initial_sfen);
        const parts = sfen.split(' ');
        const startIsBlack = parts.length > 1 ? parts[1] === 'b' : true;
        const isSenteMove = startIsBlack ? moveIdx1 % 2 === 1 : moveIdx1 % 2 === 0;
        return isSenteMove ? '▲' : '△';
    }

    function getMovePrefixFromData(data: WorkerSnapshotRecord, moveIdx1: number): '▲' | '△' {
        const sfen = deps.normalizeSFEN(data.initial_sfen);
        const parts = sfen.split(' ');
        const startIsBlack = parts.length > 1 ? parts[1] === 'b' : true;
        const isSenteMove = startIsBlack ? moveIdx1 % 2 === 1 : moveIdx1 % 2 === 0;
        return isSenteMove ? '▲' : '△';
    }

    function formatKifuLineSimple(
        data: WorkerSnapshotRecord,
        idx1: number,
        options?: { includeNumber?: boolean },
    ): string {
        const includeNumber = options?.includeNumber ?? true;
        const startNo = deps.getStartingPlyNumber(data.initial_sfen);
        const num = String(startNo + (idx1 - 1)).padStart(3, '0');
        const ki2 = Array.isArray(data.ki2_moves) ? data.ki2_moves[idx1 - 1] : null;
        const moveTextRaw = ki2 || (Array.isArray(data.moves) ? String(data.moves[idx1 - 1] || '-') : '-');
        const hasPrefix = /^[▲△]/.test(String(moveTextRaw));
        const prefix = hasPrefix ? '' : getMovePrefixFromData(data, idx1);
        const moveText = prefix + moveTextRaw;
        return includeNumber ? `${num} ${moveText}` : moveText;
    }

    function formatTerminalLineSimple(
        data: WorkerSnapshotRecord,
        lastPly: number,
        options?: { includeNumber?: boolean },
    ): string {
        const includeNumber = options?.includeNumber ?? true;
        const startNo = deps.getStartingPlyNumber(data.initial_sfen);
        const num = String(startNo + lastPly).padStart(3, '0');
        const moveText = resultCodeToKifJP(data.result_code, data);
        return includeNumber ? `${num} ${moveText}` : moveText;
    }

    function formatMoveSummaryWithStats(
        labelNum: string,
        moveText: string,
        data: WorkerSnapshotRecord,
        ply: number,
        variant: 'summary' | 'popover' = 'summary',
    ): string {
        const stats = extractSearchStats(data, ply);
        const evalValue = data.eval_black?.[ply - 1];
        const evalText = typeof evalValue === 'number' && Number.isFinite(evalValue) ? evalValue.toFixed(0) : '---';
        const timeText = formatMs(stats.time_ms);
        const nodesText = formatNodes(stats.nodes);
        const depthText = formatDepth(stats.depth, stats.seldepth);
        const dimmed = isDimmedByHighlight(data);
        return renderSummaryRow({
            label: labelNum,
            move: moveText,
            evalText,
            timeText,
            nodesText,
            depthText,
            dimmed,
            variant,
        });
    }

    function formatTerminalLineWithStats(
        data: WorkerSnapshotRecord,
        lastPly: number,
        variant: 'summary' | 'popover' = 'summary',
    ): string {
        const labelNum = String(deps.getStartingPlyNumber(data.initial_sfen) + lastPly).padStart(3, '0');
        const reason = resultCodeToKifJP(data.result_code, data);
        return renderSummaryRow({
            label: labelNum,
            move: reason,
            evalText: '---',
            timeText: '----ms',
            nodesText: '---n',
            depthText: '-/-',
            terminal: true,
            dimmed: isDimmedByHighlight(data),
            variant,
        });
    }

    function extractSearchStats(
        data: WorkerSnapshotRecord,
        ply: number,
    ): {
        nodes: number | null;
        depth: number | null;
        seldepth: number | null;
        time_ms: number | null;
    } {
        const index = ply - 1;
        return {
            nodes: data.nodes_values?.[index] ?? null,
            depth: data.depth_values?.[index] ?? null,
            seldepth: data.seldepth_values?.[index] ?? null,
            time_ms: data.move_times_ms?.[index] ?? null,
        };
    }

    function formatNodes(nodes: number | null | undefined): string {
        const n = Number(nodes || 0);
        const abs = Math.abs(n);
        if (abs >= 1_000_000_000) {
            return `${(n / 1_000_000_000).toFixed(2)}Gn`;
        }
        if (abs >= 1_000_000) {
            return `${(n / 1_000_000).toFixed(2)}Mn`;
        }
        return `${(n / 1_000).toFixed(2)}Kn`;
    }

    function formatDepth(depth: number | null | undefined, seldepth: number | null | undefined): string {
        const d = typeof depth === 'number' ? depth : null;
        const sd = typeof seldepth === 'number' ? seldepth : null;
        if (d !== null && sd !== null) return `${d}/${sd}`;
        if (d !== null) return `${d}/-`;
        if (sd !== null) return `-/${sd}`;
        return `-/-`;
    }

    function formatMs(ms: number | null | undefined): string {
        const formatted = formatDurationMs(ms ?? null);
        return formatted === '-' ? '--' : formatted;
    }

    function isDimmedByHighlight(data: WorkerSnapshotRecord): boolean {
        const highlight = deps.state.highlightEngine;
        if (!highlight) return false;
        const target = String(highlight).trim();
        if (!target) return false;
        const candidates = [
            data.engine,
            data.engine_name,
            data.engineName,
            data.black_engine,
            data.white_engine,
            data.engine_black,
            data.engine_white,
            data.black_name,
            data.white_name,
            data.black_player,
            data.white_player,
        ];
        return !candidates.some((value) => value && String(value).trim() === target);
    }

    type MoveControlTarget = 'first' | 'prev' | 'next' | 'last';

    const CONTROL_METADATA: Record<MoveControlTarget, { icon: string; aria: string }> = {
        first: { icon: '⏮', aria: '最初の局面へ' },
        prev: { icon: '◀', aria: '前の手へ' },
        next: { icon: '▶', aria: '次の手へ' },
        last: { icon: '⏭', aria: '最新局面へ' },
    };

    function renderControlButton(target: MoveControlTarget): string {
        const meta = CONTROL_METADATA[target];
        return `<button type="button" class="inline-control ${target}" data-ui-action="move-control" data-move-target="${target}" aria-label="${meta.aria}">${meta.icon}</button>`;
    }

    function escapeHtml(value: string): string {
        return value.replace(/[&<>"']/g, (char) => {
            switch (char) {
                case '&':
                    return '&amp;';
                case '<':
                    return '&lt;';
                case '>':
                    return '&gt;';
                case '"':
                    return '&quot;';
                case "'":
                    return '&#39;';
                default:
                    return char;
            }
        });
    }

    function renderMoveContent(move: string): string {
        const raw = move != null ? String(move) : '';
        const prefixChar = raw.startsWith('▲') || raw.startsWith('△') ? raw[0] : '';
        const bodyRaw = prefixChar ? raw.slice(prefixChar.length).trimStart() : raw;
        const prefixHtml = prefixChar ? escapeHtml(prefixChar) : '&nbsp;';
        const bodyText = bodyRaw || (prefixChar ? '-' : raw || '-');
        const bodyHtml = escapeHtml(bodyText);
        return `<span class="move-cell"><span class="move-prefix" aria-hidden="true">${prefixHtml}</span><span class="move-body">${bodyHtml}</span></span>`;
    }

    function renderSummaryRow(options: {
        label: string;
        move: string;
        evalText: string;
        timeText: string;
        nodesText: string;
        depthText: string;
        dimmed?: boolean;
        terminal?: boolean;
        variant?: 'summary' | 'popover';
    }): string {
        const variant = options.variant ?? 'summary';
        const classes = ['stats'];
        if (options.terminal) classes.push('terminal');
        if (options.dimmed) classes.push('dimmed');
        if (variant === 'popover') classes.push('stats--popover');
        const labelHtml = escapeHtml(options.label);
        const evalHtml = escapeHtml(options.evalText);
        const timeHtml = escapeHtml(options.timeText);
        const nodesHtml = escapeHtml(options.nodesText);
        const depthHtml = escapeHtml(options.depthText);
        const moveHtml = renderMoveContent(options.move);
        if (variant === 'popover') {
            const className = classes.join(' ');
            return `<div class="${className}">
                <span class="kifu-col kifu-col--num">${labelHtml}</span>
                <span class="kifu-col kifu-col--mv">${moveHtml}</span>
                <span class="kifu-col kifu-col--eval">${evalHtml}</span>
                <span class="kifu-col kifu-col--ms">${timeHtml}</span>
                <span class="kifu-col kifu-col--nodes">${nodesHtml}</span>
                <span class="kifu-col kifu-col--depth">${depthHtml}</span>
            </div>`;
        }
        return `
            <span class="${classes.join(' ')}">
                <span class="control-group control-group--left">
                    ${renderControlButton('first')}
                    ${renderControlButton('prev')}
                </span>
                <span class="summary-fields">
                    <span class="field num">${labelHtml}</span>
                    <span class="field mv">${moveHtml}</span>
                    <span class="field eval">${evalHtml}</span>
                    <span class="field ms">${timeHtml}</span>
                    <span class="field nodes">${nodesHtml}</span>
                    <span class="field depth">${depthHtml}</span>
                </span>
                <span class="control-group control-group--right">
                    ${renderControlButton('next')}
                    ${renderControlButton('last')}
                </span>
            </span>
        `;
    }

    // Unified sync design: throttling is now controlled at the frame level via syncTempo.
    // EVAL_CHART_MIN_INTERVAL_MS is set to 0 to disable per-chart throttling.
    const EVAL_CHART_MIN_INTERVAL_MS = 0;
    const EVAL_CHART_MAX_RENDERED_PLIES = 512;
    const EVAL_CHART_EVAL_CLAMP = 2000;
    type EvalChartSignature = {
        width: number;
        height: number;
        viewPly: number;
        movesLen: number;
        evalBlackLen: number;
        evalWhiteLen: number;
        evalBlackAtView: number | null;
        evalWhiteAtView: number | null;
    };
    type EvalChartLayerCanvas = HTMLCanvasElement | OffscreenCanvas;
    type EvalChartLayerCtx = CanvasRenderingContext2D | OffscreenCanvasRenderingContext2D;

    type EvalChartLayers = {
        base: { canvas: EvalChartLayerCanvas; ctx: EvalChartLayerCtx; key: string };
        axis: { canvas: EvalChartLayerCanvas; ctx: EvalChartLayerCtx; key: string };
        series: { canvas: EvalChartLayerCanvas; ctx: EvalChartLayerCtx; key: string };
        shift: { canvas: EvalChartLayerCanvas; ctx: EvalChartLayerCtx; key: string };
        lastMaxPlies: number;
        lastWindowStart: number;
        lastWindowLen: number;
    };

    type EvalChartRenderState = {
        drawn: EvalChartSignature | null;
        seen: EvalChartSignature | null;
        lastDrawAtMs: number;
        layers?: EvalChartLayers;
    };
    const evalChartStateByCard = new Map<LiveCardId, EvalChartRenderState>();

    function createLayerCanvas(width: number, height: number): EvalChartLayerCanvas {
        if (typeof OffscreenCanvas === 'function') {
            return new OffscreenCanvas(width, height);
        }
        const c = document.createElement('canvas');
        c.width = width;
        c.height = height;
        return c;
    }

    function get2dContext(canvas: EvalChartLayerCanvas): EvalChartLayerCtx | null {
        // OffscreenCanvas + HTMLCanvasElement both support getContext('2d')
        const ctx = (canvas as unknown as { getContext?: (type: string) => unknown }).getContext?.('2d');
        return (ctx as EvalChartLayerCtx) ?? null;
    }

    function ensureLayers(state: EvalChartRenderState, width: number, height: number): EvalChartLayers | null {
        const key = `${width}x${height}`;
        const existing = state.layers;
        if (existing && existing.base.key === key) {
            return existing;
        }
        const baseCanvas = createLayerCanvas(width, height);
        const axisCanvas = createLayerCanvas(width, height);
        const seriesCanvas = createLayerCanvas(width, height);
        const shiftCanvas = createLayerCanvas(width, height);
        const baseCtx = get2dContext(baseCanvas);
        const axisCtx = get2dContext(axisCanvas);
        const seriesCtx = get2dContext(seriesCanvas);
        const shiftCtx = get2dContext(shiftCanvas);
        if (!baseCtx || !axisCtx || !seriesCtx || !shiftCtx) {
            return null;
        }
        const layers: EvalChartLayers = {
            base: { canvas: baseCanvas, ctx: baseCtx, key },
            axis: { canvas: axisCanvas, ctx: axisCtx, key: '' },
            series: { canvas: seriesCanvas, ctx: seriesCtx, key: '' },
            shift: { canvas: shiftCanvas, ctx: shiftCtx, key },
            lastMaxPlies: 0,
            lastWindowStart: 0,
            lastWindowLen: 0,
        };
        state.layers = layers;
        return layers;
    }

    function clampEval(value: number): number {
        if (!Number.isFinite(value)) return 0;
        if (value > EVAL_CHART_EVAL_CLAMP) return EVAL_CHART_EVAL_CLAMP;
        if (value < -EVAL_CHART_EVAL_CLAMP) return -EVAL_CHART_EVAL_CLAMP;
        return value;
    }

    function valueToY(value: number, chartBottom: number, chartHeight: number): number {
        const clamped = clampEval(value);
        const normalized = clamped / EVAL_CHART_EVAL_CLAMP; // [-1, 1]
        return chartBottom - ((normalized + 1) / 2) * chartHeight;
    }

    function determineTickInterval(maxLabel: number): number {
        if (!Number.isFinite(maxLabel) || maxLabel <= 0) {
            return 1;
        }
        if (maxLabel < 5) return 1;
        if (maxLabel < 10) return 5;
        if (maxLabel <= 100) return 10;
        if (maxLabel <= 200) return 20;
        return 50;
    }

    function updateEvalChartCard(cardId: LiveCardId, data: WorkerSnapshotRecord): void {
        const canvas = document.getElementById(`evalChart-${cardId}`) as HTMLCanvasElement | null;
        if (!canvas || !data.eval_black || !data.eval_white) return;

        const cardState = deps.findCardById(cardId);
        if (!cardState) return;

        const ctx = canvas.getContext('2d');
        if (!ctx) return;
        const width = canvas.offsetWidth;
        const height = canvas.offsetHeight;
        if (width <= 0 || height <= 0) return;
        const getNow = () =>
            typeof performance !== 'undefined' && typeof performance.now === 'function'
                ? performance.now()
                : Date.now();
        const now = getNow();

        const viewPly = cardState.viewPly ?? 0;
        const movesLen = Array.isArray(data.moves) ? data.moves.length : 0;
        const evalBlackLen = data.eval_black.length;
        const evalWhiteLen = data.eval_white.length;
        const evalIdx = viewPly > 0 ? Math.min(viewPly - 1, Math.max(0, Math.min(evalBlackLen, evalWhiteLen) - 1)) : -1;
        const evalBlackAtView = evalIdx >= 0 ? (data.eval_black[evalIdx] ?? null) : null;
        const evalWhiteAtView = evalIdx >= 0 ? (data.eval_white[evalIdx] ?? null) : null;

        const maxEvalPlies = Math.max(evalBlackLen, evalWhiteLen);
        if (maxEvalPlies === 0) {
            // If we have no evaluation series yet, avoid re-rendering the chart on every move.
            // (Flood tests may not include eval at all; drawing the axis/series would be wasted work.)
            const signature: EvalChartSignature = {
                width,
                height,
                viewPly: 0,
                movesLen: 0,
                evalBlackLen: 0,
                evalWhiteLen: 0,
                evalBlackAtView: null,
                evalWhiteAtView: null,
            };

            const state = evalChartStateByCard.get(cardId) ?? { drawn: null, seen: null, lastDrawAtMs: 0 };
            state.seen = signature;
            evalChartStateByCard.set(cardId, state);

            const prev = state.drawn;
            const changed =
                !prev ||
                prev.width !== signature.width ||
                prev.height !== signature.height ||
                prev.evalBlackLen !== 0 ||
                prev.evalWhiteLen !== 0;
            if (!changed) {
                return;
            }
            if (canvas.width !== width) canvas.width = width;
            if (canvas.height !== height) canvas.height = height;
            ctx.clearRect(0, 0, width, height);
            state.drawn = signature;
            state.lastDrawAtMs = now;
            return;
        }

        const signature: EvalChartSignature = {
            width,
            height,
            viewPly,
            movesLen,
            evalBlackLen,
            evalWhiteLen,
            evalBlackAtView,
            evalWhiteAtView,
        };

        const state = evalChartStateByCard.get(cardId) ?? { drawn: null, seen: null, lastDrawAtMs: 0 };
        state.seen = signature;
        evalChartStateByCard.set(cardId, state);

        const prev = state.drawn;
        const sizeChanged = !prev || prev.width !== signature.width || prev.height !== signature.height;
        const viewChanged = !prev || prev.viewPly !== signature.viewPly;

        const evalJustArrived =
            Boolean(prev) &&
            prev?.viewPly === signature.viewPly &&
            ((prev?.evalBlackAtView == null && signature.evalBlackAtView != null) ||
                (prev?.evalWhiteAtView == null && signature.evalWhiteAtView != null));

        const changed =
            !prev ||
            prev.movesLen !== signature.movesLen ||
            prev.evalBlackLen !== signature.evalBlackLen ||
            prev.evalWhiteLen !== signature.evalWhiteLen ||
            prev.evalBlackAtView !== signature.evalBlackAtView ||
            prev.evalWhiteAtView !== signature.evalWhiteAtView;

        if (!changed) {
            return;
        }
        const force = sizeChanged || viewChanged || evalJustArrived;
        if (!force && now - state.lastDrawAtMs < EVAL_CHART_MIN_INTERVAL_MS) {
            return;
        }

        if (canvas.width !== width) canvas.width = width;
        if (canvas.height !== height) canvas.height = height;

        const maxPly = Array.isArray(data.moves) ? data.moves.length : 0;
        const maxPlies = Math.max(maxPly, maxEvalPlies);
        if (maxPlies === 0) return;

        const startPlyNumber = deps.getStartingPlyNumber(data.initial_sfen);
        const plotLeft = 34;
        const plotRight = 6;
        const plotWidth = Math.max(1, width - plotLeft - plotRight);

        const chartTop = 24;
        const tickSpace = 22;
        const chartBottom = Math.max(chartTop, height - tickSpace);
        const chartHeight = Math.max(1, chartBottom - chartTop);

        const maxRenderedPlies = EVAL_CHART_MAX_RENDERED_PLIES;
        const windowLen = Math.min(maxPlies, maxRenderedPlies);
        const windowStart = Math.max(0, maxPlies - windowLen);
        const startPlyAbs = startPlyNumber + windowStart;
        const endPlyAbs = startPlyNumber + (windowStart + windowLen - 1);
        const step = windowLen <= 1 ? 0 : plotWidth / Math.max(1, windowLen - 1);
        const isScrollWindow = maxPlies > maxRenderedPlies;
        const axisStartPly = isScrollWindow ? startPlyAbs : Math.max(0, Math.floor(startPlyAbs / 10) * 10);
        const axisSpan = Math.max(0, endPlyAbs - axisStartPly);
        const xStep = isScrollWindow ? step : axisSpan === 0 ? 0 : plotWidth / axisSpan;
        const xForIndex = (index: number): number => {
            const absolute = startPlyNumber + index;
            return plotLeft + xStep * (absolute - axisStartPly);
        };

        // Fallback for test environments / canvas mocks that don't implement drawImage.
        if (typeof (ctx as unknown as { drawImage?: unknown }).drawImage !== 'function') {
            ctx.clearRect(0, 0, width, height);

            // y grid + labels
            ctx.strokeStyle = '#e2e8f0';
            ctx.lineWidth = 0.5;
            ctx.fillStyle = '#a0aec0';
            ctx.font = '10px sans-serif';
            const prevValueAlign = ctx.textAlign;
            const prevValueBaseline = ctx.textBaseline;
            ctx.textAlign = 'left';
            ctx.textBaseline = 'middle';
            for (let v = -EVAL_CHART_EVAL_CLAMP; v <= EVAL_CHART_EVAL_CLAMP; v += 500) {
                const y = valueToY(v, chartBottom, chartHeight);
                ctx.beginPath();
                ctx.moveTo(plotLeft, y);
                ctx.lineTo(width, y);
                ctx.stroke();
                ctx.fillText(String(v), 4, Math.max(chartTop, Math.min(chartBottom, y)));
            }
            ctx.textAlign = prevValueAlign ?? 'left';
            ctx.textBaseline = prevValueBaseline ?? 'alphabetic';

            const zeroY = valueToY(0, chartBottom, chartHeight);
            ctx.strokeStyle = '#cbd5e0';
            ctx.lineWidth = 1;
            ctx.beginPath();
            ctx.moveTo(plotLeft, zeroY);
            ctx.lineTo(width, zeroY);
            ctx.stroke();

            // x axis ticks
            const tickInterval = determineTickInterval(Math.max(5, axisSpan || 1));
            const endTickValue = Math.ceil(endPlyAbs / tickInterval) * tickInterval;
            const tickValues: number[] = [];
            const firstTick = isScrollWindow
                ? Math.max(0, Math.floor(startPlyAbs / tickInterval) * tickInterval)
                : axisStartPly;
            let tickValue = firstTick;
            while (tickValue <= endTickValue + 1e-9) {
                tickValues.push(Math.round(tickValue));
                tickValue += tickInterval;
            }
            ctx.strokeStyle = '#d1d5db';
            ctx.lineWidth = 1;
            const prevTextAlign = ctx.textAlign;
            const prevTextBaseline = ctx.textBaseline;
            const prevFillStyle = ctx.fillStyle;
            ctx.textAlign = 'center';
            ctx.textBaseline = 'top';
            ctx.fillStyle = '#4a5568';
            const axisY = chartBottom;
            const tickLength = 4;
            for (const tv of tickValues) {
                const relative = tv - axisStartPly;
                const x = xStep === 0 ? plotLeft : plotLeft + xStep * relative;
                if (x < plotLeft - 4 || x > width + 4) continue;
                ctx.beginPath();
                ctx.moveTo(x, axisY);
                ctx.lineTo(x, axisY + tickLength);
                ctx.stroke();
                const label = String(tv).padStart(3, '0');
                ctx.fillText(label, x, axisY + tickLength + 2);
            }
            ctx.textAlign = prevTextAlign ?? 'left';
            ctx.textBaseline = prevTextBaseline ?? 'alphabetic';
            ctx.fillStyle = prevFillStyle;

            const normalizedInitial = deps.normalizeSFEN(data.initial_sfen);
            const parts = normalizedInitial.split(' ');
            const startIsBlack = parts.length > 1 ? parts[1] === 'b' : true;
            const isBlackMoveIndex = (index: number): boolean => (startIsBlack ? index % 2 === 0 : index % 2 === 1);

            const drawSeriesWindow = (
                values: (number | null | undefined)[],
                color: string,
                shouldUse: (index: number) => boolean,
                transform: (value: number) => number = (value) => value,
            ): void => {
                ctx.strokeStyle = color;
                ctx.lineWidth = 1.5;
                ctx.beginPath();
                let hasPoint = false;
                const endIndex = Math.min(values.length - 1, windowStart + windowLen - 1);
                for (let i = windowStart; i <= endIndex; i++) {
                    if (!shouldUse(i)) continue;
                    const rawValue = Number(values[i]);
                    if (!Number.isFinite(rawValue)) continue;
                    const x = xForIndex(i);
                    const y = valueToY(transform(rawValue), chartBottom, chartHeight);
                    if (!hasPoint) {
                        ctx.moveTo(x, y);
                        hasPoint = true;
                    } else {
                        ctx.lineTo(x, y);
                    }
                }
                if (hasPoint) {
                    ctx.stroke();
                }
            };

            drawSeriesWindow(data.eval_black, '#4c51bf', (index) => isBlackMoveIndex(index));
            drawSeriesWindow(
                data.eval_white,
                '#9f7aea',
                (index) => !isBlackMoveIndex(index),
                (value) => -value,
            );

            // marker
            const cardStateAuto = deps.findCardById(cardId);
            if (cardStateAuto) {
                const rawViewPly = Number(cardStateAuto.viewPly || 0);
                let absolutePly = startPlyNumber + Math.max(rawViewPly - 1, 0);
                const maxAbsolutePly = startPlyNumber + Math.max(0, maxPlies - 1);
                if (rawViewPly <= 0) {
                    absolutePly = startPlyNumber;
                } else if (rawViewPly > maxPly) {
                    absolutePly = maxAbsolutePly;
                }
                const clampedAbs = Math.max(axisStartPly, Math.min(endPlyAbs, absolutePly));
                const markerXRaw = plotLeft + xStep * (clampedAbs - axisStartPly);
                const markerX = Math.max(plotLeft, Math.min(plotLeft + plotWidth, markerXRaw));
                ctx.strokeStyle = '#2b6cb0';
                ctx.beginPath();
                ctx.moveTo(markerX, 0);
                ctx.lineTo(markerX, height);
                ctx.stroke();
            }

            state.drawn = signature;
            state.lastDrawAtMs = now;
            return;
        }

        const layers = ensureLayers(state, width, height);
        if (!layers) return;

        // (1) base layer (y grid + labels) is size-dependent only
        const baseKey = `${width}x${height}`;
        if (layers.base.key !== baseKey) {
            layers.base.key = baseKey;
        }
        if (layers.base.key === baseKey && layers.axis.key === '' && layers.series.key === '') {
            // first time for this size: draw base
            const bctx = layers.base.ctx;
            bctx.clearRect(0, 0, width, height);
            bctx.strokeStyle = '#e2e8f0';
            bctx.lineWidth = 0.5;
            bctx.fillStyle = '#a0aec0';
            bctx.font = '10px sans-serif';
            const prevValueAlign = bctx.textAlign;
            const prevValueBaseline = bctx.textBaseline;
            bctx.textAlign = 'left';
            bctx.textBaseline = 'middle';
            for (let v = -EVAL_CHART_EVAL_CLAMP; v <= EVAL_CHART_EVAL_CLAMP; v += 500) {
                const y = valueToY(v, chartBottom, chartHeight);
                bctx.beginPath();
                bctx.moveTo(plotLeft, y);
                bctx.lineTo(width, y);
                bctx.stroke();
                bctx.fillText(String(v), 4, Math.max(chartTop, Math.min(chartBottom, y)));
            }
            bctx.textAlign = prevValueAlign ?? 'left';
            bctx.textBaseline = prevValueBaseline ?? 'alphabetic';

            const zeroY = valueToY(0, chartBottom, chartHeight);
            bctx.strokeStyle = '#cbd5e0';
            bctx.lineWidth = 1;
            bctx.beginPath();
            bctx.moveTo(plotLeft, zeroY);
            bctx.lineTo(width, zeroY);
            bctx.stroke();
        }

        // (2) axis layer (x ticks) changes with window range
        const axisKey = `${width}x${height}:${axisStartPly}:${endPlyAbs}:${isScrollWindow ? 1 : 0}`;
        if (layers.axis.key !== axisKey) {
            layers.axis.key = axisKey;
            const actx = layers.axis.ctx;
            actx.clearRect(0, 0, width, height);
            const tickInterval = determineTickInterval(Math.max(5, axisSpan || 1));
            const endTickValue = Math.ceil(endPlyAbs / tickInterval) * tickInterval;
            const tickValues: number[] = [];
            const firstTick = isScrollWindow
                ? Math.max(0, Math.floor(startPlyAbs / tickInterval) * tickInterval)
                : axisStartPly;
            let tickValue = firstTick;
            while (tickValue <= endTickValue + 1e-9) {
                tickValues.push(Math.round(tickValue));
                tickValue += tickInterval;
            }
            actx.strokeStyle = '#d1d5db';
            actx.lineWidth = 1;
            const prevTextAlign = actx.textAlign;
            const prevTextBaseline = actx.textBaseline;
            const prevFillStyle = actx.fillStyle;
            actx.textAlign = 'center';
            actx.textBaseline = 'top';
            actx.fillStyle = '#4a5568';
            const axisY = chartBottom;
            const tickLength = 4;
            for (const tv of tickValues) {
                const relative = tv - axisStartPly;
                const x = xStep === 0 ? plotLeft : plotLeft + xStep * relative;
                if (x < plotLeft - 4 || x > width + 4) continue;
                actx.beginPath();
                actx.moveTo(x, axisY);
                actx.lineTo(x, axisY + tickLength);
                actx.stroke();
                const label = String(tv).padStart(3, '0');
                actx.fillText(label, x, axisY + tickLength + 2);
            }
            actx.textAlign = prevTextAlign ?? 'left';
            actx.textBaseline = prevTextBaseline ?? 'alphabetic';
            actx.fillStyle = prevFillStyle;
        }

        const normalizedInitial = deps.normalizeSFEN(data.initial_sfen);
        const parts = normalizedInitial.split(' ');
        const startIsBlack = parts.length > 1 ? parts[1] === 'b' : true;
        const isBlackMoveIndex = (index: number): boolean => (startIsBlack ? index % 2 === 0 : index % 2 === 1);

        const sctx = layers.series.ctx;
        const seriesKey = `${width}x${height}:${windowLen}:${startIsBlack ? 'b' : 'w'}:${isScrollWindow ? 'scroll' : 'full'}`;

        const canScrollIncremental =
            layers.series.key === seriesKey &&
            layers.lastWindowLen === windowLen &&
            layers.lastMaxPlies + 1 === maxPlies &&
            windowStart === layers.lastWindowStart + 1 &&
            windowLen === maxRenderedPlies &&
            step > 0;

        const drawFullSeries = (): void => {
            sctx.clearRect(0, 0, width, height);
            const drawSeriesWindow = (
                values: (number | null | undefined)[],
                color: string,
                shouldUse: (index: number) => boolean,
                transform: (value: number) => number = (value) => value,
            ): void => {
                sctx.strokeStyle = color;
                sctx.lineWidth = 1.5;
                sctx.beginPath();
                let hasPoint = false;
                const endIndex = Math.min(values.length - 1, windowStart + windowLen - 1);
                for (let i = windowStart; i <= endIndex; i++) {
                    if (!shouldUse(i)) continue;
                    const raw = values[i];
                    if (raw == null) continue;
                    const rawValue = Number(raw);
                    if (!Number.isFinite(rawValue)) continue;
                    const x = xForIndex(i);
                    const y = valueToY(transform(rawValue), chartBottom, chartHeight);
                    if (!hasPoint) {
                        sctx.moveTo(x, y);
                        hasPoint = true;
                    } else {
                        sctx.lineTo(x, y);
                    }
                }
                if (hasPoint) {
                    sctx.stroke();
                }
            };
            drawSeriesWindow(data.eval_black, '#4c51bf', (index) => isBlackMoveIndex(index));
            drawSeriesWindow(
                data.eval_white,
                '#9f7aea',
                (index) => !isBlackMoveIndex(index),
                (value) => -value,
            );
        };

        if (canScrollIncremental) {
            // shift the series bitmap left by one step and draw only the new tail segment
            const tctx = layers.shift.ctx;
            tctx.clearRect(0, 0, width, height);
            tctx.drawImage(layers.series.canvas as unknown as CanvasImageSource, -step, 0);
            sctx.clearRect(0, 0, width, height);
            sctx.drawImage(layers.shift.canvas as unknown as CanvasImageSource, 0, 0);
            // Clear the rightmost "new space" strip, but keep the last shifted point at x=(plotLeft+plotWidth-step).
            sctx.clearRect(plotLeft + plotWidth - step + 1, 0, step + 4, height);

            const lastIndex = maxPlies - 1;
            const prevSameIndex = lastIndex - 2;
            const x1 = plotLeft + plotWidth;
            const x0 = x1 - step * 2;
            const drawSegment = (
                color: string,
                prevVal: number | null,
                nextVal: number | null,
                transform: (v: number) => number = (v) => v,
            ): void => {
                if (prevVal == null || nextVal == null) return;
                if (!Number.isFinite(prevVal) || !Number.isFinite(nextVal)) return;
                sctx.strokeStyle = color;
                sctx.lineWidth = 1.5;
                sctx.beginPath();
                sctx.moveTo(x0, valueToY(transform(prevVal), chartBottom, chartHeight));
                sctx.lineTo(x1, valueToY(transform(nextVal), chartBottom, chartHeight));
                sctx.stroke();
            };

            const evalBlackPrev =
                prevSameIndex >= 0 && prevSameIndex < data.eval_black.length ? data.eval_black[prevSameIndex] : null;
            const evalBlackNow =
                lastIndex >= 0 && lastIndex < data.eval_black.length ? data.eval_black[lastIndex] : null;
            const evalWhitePrev =
                prevSameIndex >= 0 && prevSameIndex < data.eval_white.length ? data.eval_white[prevSameIndex] : null;
            const evalWhiteNow =
                lastIndex >= 0 && lastIndex < data.eval_white.length ? data.eval_white[lastIndex] : null;

            if (isBlackMoveIndex(lastIndex)) {
                drawSegment('#4c51bf', evalBlackPrev ?? null, evalBlackNow ?? null);
            } else {
                drawSegment('#9f7aea', evalWhitePrev ?? null, evalWhiteNow ?? null, (v) => -v);
            }
        } else {
            layers.series.key = seriesKey;
            drawFullSeries();
        }

        layers.lastMaxPlies = maxPlies;
        layers.lastWindowStart = windowStart;
        layers.lastWindowLen = windowLen;

        // (3) composite layers onto the visible canvas
        ctx.clearRect(0, 0, width, height);
        ctx.drawImage(layers.base.canvas as unknown as CanvasImageSource, 0, 0);
        ctx.drawImage(layers.series.canvas as unknown as CanvasImageSource, 0, 0);
        ctx.drawImage(layers.axis.canvas as unknown as CanvasImageSource, 0, 0);

        const cardStateAuto = deps.findCardById(cardId);
        if (cardStateAuto) {
            const rawViewPly = Number(cardStateAuto.viewPly || 0);
            let absolutePly = startPlyNumber + Math.max(rawViewPly - 1, 0);
            const maxAbsolutePly = startPlyNumber + Math.max(0, maxPlies - 1);
            if (rawViewPly <= 0) {
                absolutePly = startPlyNumber;
            } else if (rawViewPly > maxPly) {
                absolutePly = maxAbsolutePly;
            }
            const clampedAbs = Math.max(axisStartPly, Math.min(endPlyAbs, absolutePly));
            const markerXRaw = plotLeft + xStep * (clampedAbs - axisStartPly);
            const markerX = Math.max(plotLeft, Math.min(plotLeft + plotWidth, markerXRaw));
            ctx.strokeStyle = '#2b6cb0';
            ctx.beginPath();
            ctx.moveTo(markerX, 0);
            ctx.lineTo(markerX, height);
            ctx.stroke();
        }

        state.drawn = signature;
        state.lastDrawAtMs = now;
    }

    function updateMoveSummaryCard(cardId: LiveCardId, data: WorkerSnapshotRecord): void {
        const el = document.getElementById(`moveSummary-${cardId}`);
        if (!el || !data) return;

        const cardState = deps.findCardById(cardId);
        if (!cardState) return;

        let ply = Number(cardState.viewPly || 0);
        const maxPly = Array.isArray(data.moves) ? data.moves.length : 0;
        const startNo = deps.getStartingPlyNumber(data.initial_sfen);
        const hasTerminal = typeof data.result_code === 'number' && Number.isFinite(data.result_code);
        const maxView = maxPly + (hasTerminal ? 1 : 0);

        if (cardState.autoSync && hasTerminal && ply < maxView) {
            cardState.viewPly = maxView;
            ply = maxView;
            deps.updateElement(`ply-${cardId}`, maxView);
            deps.syncWorkerViewFromCard(cardState);
        }

        if (hasTerminal && ply === maxPly + 1) {
            const labelNum = String(startNo + maxPly).padStart(3, '0');
            const moveText = resultCodeToKifJP(data.result_code, data);
            el.innerHTML = renderSummaryRow({
                label: labelNum,
                move: moveText,
                evalText: '---',
                timeText: '----ms',
                nodesText: '---n',
                depthText: '-/-',
                terminal: true,
            });
            return;
        }

        if (!ply) {
            el.innerHTML = renderSummaryRow({
                label: '---',
                move: '開始局面',
                evalText: '---',
                timeText: '----ms',
                nodesText: '---n',
                depthText: '-/-',
            });
            return;
        }
        if (ply > maxPly) ply = maxPly;

        const ki2 = Array.isArray(data.ki2_moves) ? data.ki2_moves[ply - 1] : null;
        const labelNum = String(startNo + (ply - 1)).padStart(3, '0');
        const moveText = ki2 || (Array.isArray(data.moves) ? String(data.moves[ply - 1] || '-') : '-');
        const hasPrefix = /^[▲△]/.test(String(moveText));
        const prefix = hasPrefix ? '' : getMovePrefixFromData(data, ply);
        const formattedText = formatMoveSummaryWithStats(labelNum, prefix + moveText, data, ply);
        el.innerHTML = formattedText;
    }

    function updateMoveSummary(workerIdx: number): void {
        const card = deps.getCardList().find((c) => c.source === `worker-latest:${workerIdx}`);
        if (!card) return;
        const snapshot = deps.getWorkerSnapshot(workerIdx);
        updateMoveSummaryCard(card.id, snapshot);
    }

    function toggleKifu(workerIdx: number): void {
        ensureKifuLayers();
        if (!kifuPopover || !kifuOverlay) return;

        if (kifuOpenWorker === workerIdx && kifuPopover.style.display !== 'none') {
            closeKifuPopover();
            return;
        }

        const card = deps.getCardList().find((c) => c.source === `worker-latest:${workerIdx}`);
        if (!card) return;

        const snapshot = deps.getWorkerSnapshot(workerIdx);
        const html = renderKifuLinesCard(card.id, snapshot);

        kifuPopover.innerHTML = html;
        kifuPopover.dataset.kifuContext = 'worker';
        kifuPopover.dataset.workerIdx = String(workerIdx);
        delete kifuPopover.dataset.cardId;

        const anchor = document.getElementById(`moveSummary-${card.id}`);
        if (anchor) positionKifuPopover(anchor);

        kifuOverlay.style.display = 'block';
        kifuPopover.style.display = 'block';
        kifuOpenWorker = workerIdx;
        kifuOpenCard = null;
        scrollKifuToCurrent();
    }

    function toggleKifuCard(cardId: LiveCardId): void {
        ensureKifuLayers();
        if (!kifuPopover || !kifuOverlay) return;

        if (kifuOpenCard === cardId && kifuPopover.style.display !== 'none') {
            closeKifuPopover();
            return;
        }

        const card = deps.findCardById(cardId);
        if (!card) return;

        deps.resolveCardData(card)
            .then((data) => {
                if (!data) return;
                if (!kifuPopover || !kifuOverlay) {
                    throw new Error('KIFU popover elements not initialized');
                }
                const html = renderKifuLinesCard(cardId, data);
                kifuPopover.innerHTML = html;
                kifuPopover.dataset.kifuContext = 'card';
                kifuPopover.dataset.cardId = String(cardId);
                delete kifuPopover.dataset.workerIdx;
                const anchor = document.getElementById(`moveSummary-${cardId}`);
                if (anchor) positionKifuPopover(anchor);
                kifuOverlay.style.display = 'block';
                kifuPopover.style.display = 'block';
                kifuOpenCard = cardId;
                kifuOpenWorker = null;
                scrollKifuToCurrent();
            })
            .catch((error) => {
                deps.warnSoftFailure('Failed to render KIFU for card', error);
            });
    }

    function closeKifuPopover(): void {
        if (kifuPopover) {
            kifuPopover.style.display = 'none';
            kifuPopover.innerHTML = '';
        }
        if (kifuOverlay) {
            kifuOverlay.style.display = 'none';
        }
        kifuOpenCard = null;
        kifuOpenWorker = null;
    }

    return {
        resultCodeToKifJP,
        ensureKifuLayers,
        positionKifuPopover,
        scrollKifuToCurrent,
        renderKifuLinesCard,
        renderKifuLines,
        updateKI2MovesCard,
        updateKI2Moves,
        getMovePrefix,
        getMovePrefixFromData,
        formatKifuLineSimple,
        formatTerminalLineSimple,
        updateMoveSummaryCard,
        formatMoveSummaryWithStats,
        formatTerminalLineWithStats,
        extractSearchStats,
        formatNodes,
        formatDepth,
        formatMs,
        isDimmedByHighlight,
        updateEvalChartCard,
        updateMoveSummary,
        toggleKifu,
        toggleKifuCard,
        closeKifuPopover,
    } satisfies KifuHandlers;
}
