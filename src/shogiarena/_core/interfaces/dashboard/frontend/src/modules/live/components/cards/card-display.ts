import type { LiveCardState } from '@/modules/live/types';
import type { DashboardCoreState } from '@/types/dashboard';
import { peekWorkerState } from './state';
import type { WorkerSnapshotRecord } from './types';
import { recordLiveDiagnosticsMetric } from '@/modules/live/utils/liveNamespace/metrics';

export interface CardDisplayDeps {
    state: DashboardCoreState;
    updateElement: (id: string, value: unknown) => void;
    formatTimeControlShort: (tc: string | null | undefined) => string;
    updateMoveSummaryCard: (cardId: number | string, data: WorkerSnapshotRecord) => void;
    updateEvalChartCard: (cardId: number | string, data: WorkerSnapshotRecord) => void;
    syncBoard: BoardSyncFn;
    syncClocks: ClockSyncFn;
    closeKifuPopover: () => void;
}

export type BoardSyncFn = (args: {
    cardState: LiveCardState;
    data: WorkerSnapshotRecord;
    newGameDetected: boolean;
}) => void;

export type ClockSyncFn = (cardState: LiveCardState, data: WorkerSnapshotRecord) => void;

function updateCardNamesAndColors(
    updateElement: CardDisplayDeps['updateElement'],
    cardState: LiveCardState,
    data: WorkerSnapshotRecord,
): void {
    updateElement(`black-name-${cardState.id}`, data.black_name || '-');
    updateElement(`white-name-${cardState.id}`, data.white_name || '-');

    const senteColor = '#4c51bf';
    const goteColor = '#9f7aea';
    const blackVal = document.getElementById(`black-name-${cardState.id}`);
    const whiteVal = document.getElementById(`white-name-${cardState.id}`);
    if (blackVal) (blackVal as HTMLElement).style.color = senteColor;
    if (whiteVal) (whiteVal as HTMLElement).style.color = goteColor;
}

function updateCardTimeControls(
    deps: Pick<CardDisplayDeps, 'state' | 'formatTimeControlShort'>,
    cardState: LiveCardState,
    data: WorkerSnapshotRecord,
): void {
    const { state, formatTimeControlShort } = deps;

    if (cardState.source.startsWith('db-game:')) {
        if (data && (data.time_control_black || data.time_control_white)) {
            const btc = document.getElementById(`black-tc-${cardState.id}`);
            const wtc = document.getElementById(`white-tc-${cardState.id}`);
            if (btc) btc.textContent = formatTimeControlShort(data.time_control_black);
            if (wtc) wtc.textContent = formatTimeControlShort(data.time_control_white);
        }
        return;
    }

    const workerIdxVal = parseInt(cardState.source.split(':')[1], 10);
    const ws = Number.isNaN(workerIdxVal) ? undefined : peekWorkerState(state, workerIdxVal);
    if (ws?.timeControlBlack || ws?.timeControlWhite) {
        const btc = document.getElementById(`black-tc-${cardState.id}`);
        const wtc = document.getElementById(`white-tc-${cardState.id}`);
        if (btc) btc.textContent = formatTimeControlShort(ws?.timeControlBlack);
        if (wtc) wtc.textContent = formatTimeControlShort(ws?.timeControlWhite);
    }
}

function updateCardEval(
    updateElement: CardDisplayDeps['updateElement'],
    cardState: LiveCardState,
    data: WorkerSnapshotRecord,
): void {
    const viewPly = cardState.viewPly ?? 0;
    if (data.eval_black?.length && viewPly > 0) {
        const evalIdx = Math.min(viewPly - 1, data.eval_black.length - 1);
        const evalValue = data.eval_black[evalIdx];
        updateElement(
            `eval-${cardState.id}`,
            typeof evalValue === 'number' && Number.isFinite(evalValue) ? evalValue : '---',
        );
    }
}

function updateCardBoardAndClocks(
    deps: Pick<CardDisplayDeps, 'syncBoard' | 'syncClocks' | 'updateMoveSummaryCard' | 'updateEvalChartCard'>,
    cardState: LiveCardState,
    data: WorkerSnapshotRecord,
    options: { newGameDetected: boolean },
): void {
    const { syncBoard, syncClocks, updateMoveSummaryCard, updateEvalChartCard } = deps;
    syncBoard({ cardState, data, newGameDetected: options.newGameDetected });

    const cache = cardState as unknown as {
        _moveSummaryKey?: string;
        _moveSummaryAtMs?: number;
        _evalChartKey?: string;
        _evalChartAtMs?: number;
    };
    const getNow = () =>
        typeof performance !== 'undefined' && typeof performance.now === 'function' ? performance.now() : Date.now();
    const now = getNow();
    const deferUntil = (cardState as { _deferBoardMovesUntil?: number })._deferBoardMovesUntil;
    if (typeof deferUntil === 'number' && now < deferUntil) {
        syncClocks(cardState, data);
        return;
    }

    const cacheMoves = cardState as unknown as {
        _movesCache?: string[];
        _movesCacheLen?: number;
        _movesCacheRev?: number;
    };
    const historyRev = (data as { __history_rev?: unknown }).__history_rev;
    const rev = typeof historyRev === 'number' && Number.isFinite(historyRev) ? historyRev : 0;
    let movesLen = 0;
    let lastMove = '';
    if (cacheMoves._movesCache && cacheMoves._movesCacheRev === rev) {
        movesLen = cacheMoves._movesCacheLen ?? cacheMoves._movesCache.length;
        if (movesLen > 0) {
            lastMove = String(cacheMoves._movesCache[movesLen - 1] ?? '');
        }
    } else {
        const movesRaw = Array.isArray(data.moves) ? data.moves : [];
        for (let i = 0; i < movesRaw.length; i += 1) {
            const mv = String(movesRaw[i] ?? '').trim();
            if (!mv) break;
            movesLen += 1;
            lastMove = mv;
        }
    }
    const ki2Moves = Array.isArray(data.ki2_moves) ? data.ki2_moves : [];
    const lastKi2 = ki2Moves.length ? String(ki2Moves[ki2Moves.length - 1] ?? '') : '';
    // End-of-game result can arrive without changing moves.
    // Include it in the signature so the move summary refreshes and can show the terminal line.
    const gameResult = String(data.game_result ?? '');
    // Unified sync design: always update move summary and eval chart together without throttling.
    // Throttling is now controlled at the frame level via syncTempo setting.
    const moveKey = `${rev}:${movesLen}:${lastMove}:${lastKi2}:${gameResult}`;
    if (options.newGameDetected || cache._moveSummaryKey !== moveKey) {
        cache._moveSummaryKey = moveKey;
        cache._moveSummaryAtMs = now;
        updateMoveSummaryCard(cardState.id, data);
    }

    const evalBlack = Array.isArray(data.eval_black) ? data.eval_black : [];
    const evalWhite = Array.isArray(data.eval_white) ? data.eval_white : [];
    const viewPly = cardState.viewPly ?? 0;
    const evalIdx =
        viewPly > 0 ? Math.min(viewPly - 1, Math.max(0, Math.min(evalBlack.length, evalWhite.length) - 1)) : -1;
    const evalBlackAtView = evalIdx >= 0 ? (evalBlack[evalIdx] ?? null) : null;
    const evalWhiteAtView = evalIdx >= 0 ? (evalWhite[evalIdx] ?? null) : null;
    const evalKey = `${viewPly}:${movesLen}:${evalBlack.length}:${evalWhite.length}:${evalBlackAtView ?? ''}:${evalWhiteAtView ?? ''}`;
    if (options.newGameDetected || cache._evalChartKey !== evalKey) {
        cache._evalChartKey = evalKey;
        cache._evalChartAtMs = now;
        updateEvalChartCard(cardState.id, data);
    }
    syncClocks(cardState, data);
}

export function updateCardDisplay(
    deps: CardDisplayDeps,
    cardState: LiveCardState,
    data: WorkerSnapshotRecord,
    options: { newGameDetected: boolean },
): void {
    const {
        state,
        updateElement,
        formatTimeControlShort,
        updateMoveSummaryCard,
        updateEvalChartCard,
        syncBoard,
        syncClocks,
        closeKifuPopover,
    } = deps;
    const { newGameDetected } = options;

    const getNow = () =>
        typeof performance !== 'undefined' && typeof performance.now === 'function' ? performance.now() : Date.now();

    if (newGameDetected) {
        closeKifuPopover();
        const ki2Container = document.getElementById(`ki2-${cardState.id}`);
        if (ki2Container) {
            ki2Container.innerHTML = '';
        }
        updateElement(`eval-${cardState.id}`, 0);
    }

    const t0 = getNow();
    updateCardNamesAndColors(updateElement, cardState, data);
    const t1 = getNow();

    updateCardTimeControls({ state, formatTimeControlShort }, cardState, data);
    const t2 = getNow();

    updateElement(`ply-${cardState.id}`, cardState.viewPly || 0);
    updateCardEval(updateElement, cardState, data);
    const t3 = getNow();

    const boardStart = getNow();
    updateCardBoardAndClocks({ syncBoard, syncClocks, updateMoveSummaryCard, updateEvalChartCard }, cardState, data, {
        newGameDetected,
    });
    const boardEnd = getNow();

    const totalMs = Math.max(0, boardEnd - t0);
    const boardMs = Math.max(0, boardEnd - boardStart);

    recordLiveDiagnosticsMetric('live.cards.update_card.detail', {
        triggered: 1,
        meta_ms: Math.max(0, t1 - t0),
        time_ms: Math.max(0, t2 - t1),
        eval_ms: Math.max(0, t3 - t2),
        board_ms: boardMs,
        total_ms: totalMs,
    });

    recordLiveDiagnosticsMetric('live.cards.update_dom', {
        triggered: 1,
        names_ms: Math.max(0, t1 - t0),
        time_controls_ms: Math.max(0, t2 - t1),
        eval_text_ms: Math.max(0, t3 - t2),
        board_sync_ms: boardMs,
        total_ms: totalMs,
    });

    const slowThresholdMs = 20;
    if (totalMs >= slowThresholdMs || boardMs >= slowThresholdMs) {
        const movesRaw = Array.isArray(data.moves) ? data.moves : [];
        let movesLen = 0;
        for (let i = 0; i < movesRaw.length; i += 1) {
            const mv = String(movesRaw[i] ?? '').trim();
            if (!mv) break;
            movesLen += 1;
        }
        const source = typeof cardState.source === 'string' ? cardState.source : '';
        const sourceKind = source.startsWith('worker-latest:') ? 1 : source.startsWith('db-game:') ? 2 : 0;
        recordLiveDiagnosticsMetric('live.cards.update_card.slow', {
            triggered: 1,
            total_ms: totalMs,
            board_ms: boardMs,
            moves_len: movesLen,
            view_ply: cardState.viewPly ?? 0,
            auto_sync: cardState.autoSync ? 1 : 0,
            new_game: newGameDetected ? 1 : 0,
            source_kind: sourceKind,
        });
    }
}
