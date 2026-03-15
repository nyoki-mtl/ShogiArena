import type { LiveBoardAdapter, LiveCardState } from '@/modules/live/types';
import { hasTerminalResult } from '@/modules/live/utils';
import type { DashboardCoreState } from '@/types/dashboard';
import type { WorkerSnapshotRecord } from './types';
import { recordLiveDiagnosticsMetric } from '@/modules/live/utils/liveNamespace/metrics';

interface BoardSyncDeps {
    normalizeSFEN: (sfen: string | null | undefined) => string;
    computeAutoViewPly: (data: WorkerSnapshotRecord, includeTerminal?: boolean) => number;
    syncWorkerViewFromCard: (cardState: LiveCardState) => void;
    warnSoftFailure: (context: string, error: unknown) => void;
    getBoardAdapter: (state: DashboardCoreState, cardId: number | string) => LiveBoardAdapter | null | undefined;
    state: DashboardCoreState;
}

interface BoardSyncInput {
    cardState: LiveCardState;
    data: WorkerSnapshotRecord;
    newGameDetected: boolean;
}

export function createBoardSync({
    normalizeSFEN,
    computeAutoViewPly,
    syncWorkerViewFromCard,
    warnSoftFailure,
    getBoardAdapter,
    state,
}: BoardSyncDeps) {
    return function syncBoard({ cardState, data, newGameDetected }: BoardSyncInput) {
        const adapter = getBoardAdapter(state, cardState.id);
        if (!adapter) return;
        const setPositionFromSFEN =
            typeof adapter.setPositionFromSFEN === 'function' ? adapter.setPositionFromSFEN.bind(adapter) : null;
        const setMoves = typeof adapter.setMoves === 'function' ? adapter.setMoves.bind(adapter) : null;
        const goTo = typeof adapter.goTo === 'function' ? adapter.goTo.bind(adapter) : null;
        const highlightSquares =
            typeof adapter.highlightSquares === 'function' ? adapter.highlightSquares.bind(adapter) : null;
        if (!setPositionFromSFEN || !setMoves || !goTo) return;

        const now =
            typeof performance !== 'undefined' && typeof performance.now === 'function'
                ? () => performance.now()
                : () => Date.now();
        const started = now();
        let tPosition = started;
        let tMoves = started;
        let tGoto = started;
        let tHighlight = started;
        let tEnd = started;
        const cache = cardState as unknown as { _boardGotoPly?: number };

        const initSfen = data.initial_sfen || 'startpos';

        const source = cardState.source;
        if (source.startsWith('db-game:')) {
            setPositionFromSFEN(initSfen);
            tPosition = now();
            if (highlightSquares) {
                highlightSquares([]);
            }
            tHighlight = now();
            cardState.initial_sfen = initSfen;
        } else if (source.startsWith('worker-latest:')) {
            const currentStable = data.game_id || initSfen;
            if (!cardState.lastGameId) {
                cardState.lastGameId = currentStable;
            }
            if (newGameDetected) {
                setPositionFromSFEN(initSfen);
                tPosition = now();
                if (highlightSquares) {
                    highlightSquares([]);
                }
                tHighlight = now();
                try {
                    setMoves([]);
                    goTo(0);
                    tMoves = tGoto = now();
                    cache._boardGotoPly = 0;
                } catch (error) {
                    warnSoftFailure(`Failed to reset board for card ${cardState.id}`, error);
                }
                cardState.initial_sfen = initSfen;
            } else if (cardState.initial_sfen !== initSfen) {
                setPositionFromSFEN(initSfen);
                tPosition = now();
                cardState.initial_sfen = initSfen;
                cardState.viewPly = 0;
                syncWorkerViewFromCard(cardState);
                cache._boardGotoPly = 0;
            }
        } else if (cardState.initial_sfen !== initSfen) {
            setPositionFromSFEN(initSfen);
            tPosition = now();
            cardState.initial_sfen = initSfen;
            cache._boardGotoPly = 0;
        }

        const deferUntil = (cardState as { _deferBoardMovesUntil?: number })._deferBoardMovesUntil;
        const deferJustEnded = typeof deferUntil === 'number' && now() >= deferUntil;
        const pendingRebuild = (cardState as { _pendingInitialRebuild?: boolean })._pendingInitialRebuild;
        if (deferJustEnded) {
            delete (cardState as { _deferBoardMovesUntil?: number })._deferBoardMovesUntil;
            // Force rebuild from initial_sfen after defer period ends.
            // During defer, we set the board to `data.sfen` (current position) for quick visual feedback.
            // Now that defer has ended, we must reset to the true initial position before applying moves.
            // However, if moves are not yet available (WS snapshot pending, etc.), keep showing data.sfen
            // to avoid a jarring jump to the initial position, and set a flag to rebuild on next sync.
            if (!Array.isArray(data.moves) || data.moves.length === 0) {
                // Moves not ready yet; keep current position display and set flag for next sync.
                (cardState as { _pendingInitialRebuild?: boolean })._pendingInitialRebuild = true;
                if (source.startsWith('worker-latest:') && typeof data.sfen === 'string' && data.sfen.trim()) {
                    try {
                        setPositionFromSFEN(normalizeSFEN(data.sfen));
                        tPosition = now();
                        if (highlightSquares) {
                            highlightSquares([]);
                        }
                        tHighlight = now();
                    } catch (error) {
                        warnSoftFailure(`Failed to maintain position for card ${cardState.id}`, error);
                    }
                }
                syncWorkerViewFromCard(cardState);
                return;
            }
        }
        // Handle pending rebuild: when moves become available after deferred sync, force initial position reset.
        // Note: _pendingInitialRebuild is only set for worker-latest: sources, but we clear it unconditionally
        // to avoid flag residue if the source changes or if the flag is set by mistake in the future.
        const shouldForceRebuild = pendingRebuild && Array.isArray(data.moves) && data.moves.length > 0;
        if (deferJustEnded || shouldForceRebuild) {
            if (pendingRebuild) {
                delete (cardState as { _pendingInitialRebuild?: boolean })._pendingInitialRebuild;
            }
            setPositionFromSFEN(initSfen);
            tPosition = now();
            cardState.initial_sfen = initSfen;
            // Clear moves cache so setMoves will be called with fresh data.
            (cardState as { _boardMovesKey?: string })._boardMovesKey = undefined;
            cache._boardGotoPly = 0;
            if (highlightSquares) {
                highlightSquares([]);
            }
            tHighlight = now();
        }

        const shouldDeferMoves =
            typeof deferUntil === 'number' &&
            now() < deferUntil &&
            source.startsWith('worker-latest:') &&
            typeof data.sfen === 'string' &&
            data.sfen.trim().length > 0;
        if (shouldDeferMoves) {
            try {
                setPositionFromSFEN(normalizeSFEN(data.sfen));
                tPosition = now();
                if (highlightSquares) {
                    highlightSquares([]);
                }
                tHighlight = now();
            } catch (error) {
                warnSoftFailure(`Failed to fast-sync board for card ${cardState.id}`, error);
            }
            if (cardState.autoSync && source.startsWith('worker-latest:')) {
                cardState.viewPly = computeAutoViewPly(data, true);
                syncWorkerViewFromCard(cardState);
            } else {
                syncWorkerViewFromCard(cardState);
            }
            return;
        }

        if (!Array.isArray(data.moves)) return;

        const movesRaw = data.moves;
        const historyRev = (data as unknown as { __history_rev?: unknown }).__history_rev;
        const rev = typeof historyRev === 'number' && Number.isFinite(historyRev) ? historyRev : 0;

        let movesLen = movesRaw.length;
        while (movesLen > 0) {
            const mv = String(movesRaw[movesLen - 1] ?? '').trim();
            if (mv) break;
            movesLen -= 1;
        }
        const movesCache = cardState as unknown as {
            _movesCache?: string[];
            _movesCacheLen?: number;
            _movesCacheRev?: number;
        };
        let movesForBoard: string[] | null =
            movesCache._movesCache && movesCache._movesCacheRev === rev ? movesCache._movesCache : null;
        const cachedLen = movesCache._movesCacheLen ?? (movesForBoard ? movesForBoard.length : 0);

        if (!movesForBoard || cachedLen > movesLen || movesCache._movesCacheRev !== rev) {
            const rebuilt: string[] = [];
            for (let i = 0; i < movesLen; i += 1) {
                const mv = String(movesRaw[i] ?? '').trim();
                if (!mv) break;
                rebuilt.push(mv);
            }
            movesLen = rebuilt.length;
            movesForBoard = rebuilt;
            movesCache._movesCache = rebuilt;
            movesCache._movesCacheLen = movesLen;
            movesCache._movesCacheRev = rev;
        } else if (movesForBoard && cachedLen < movesLen) {
            for (let i = cachedLen; i < movesLen; i += 1) {
                const mv = String(movesRaw[i] ?? '').trim();
                if (!mv) {
                    movesLen = i;
                    break;
                }
                movesForBoard.push(mv);
            }
            movesCache._movesCacheLen = movesLen;
        }

        const first = movesForBoard.length ? String(movesForBoard[0] ?? '') : '';
        const last = movesForBoard.length ? String(movesForBoard[movesForBoard.length - 1] ?? '') : '';
        const movesKey = `${rev}:${movesForBoard.length}:${first}:${last}`;
        if ((cardState as { _boardMovesKey?: string })._boardMovesKey !== movesKey) {
            (cardState as { _boardMovesKey?: string })._boardMovesKey = movesKey;
            const revAware = (
                adapter as unknown as { setMovesWithRevision?: (moves: readonly string[], rev: number) => void }
            ).setMovesWithRevision;
            if (typeof revAware === 'function') {
                revAware.call(adapter, movesForBoard, rev);
            } else {
                setMoves(movesForBoard);
            }
            tMoves = now();
        }

        const maxPly = movesForBoard.length;
        const hasTerminal = hasTerminalResult(data);
        const maxView = maxPly + (hasTerminal ? 1 : 0);

        if (cardState.autoSync && source.startsWith('worker-latest:')) {
            const autoView = computeAutoViewPly(data, true);
            const displayPly = Math.min(autoView, maxPly);
            if (cache._boardGotoPly !== displayPly) {
                try {
                    goTo(displayPly);
                    tGoto = now();
                    cache._boardGotoPly = displayPly;
                } catch (error) {
                    warnSoftFailure(`Failed to advance board for card ${cardState.id}`, error);
                    if (source.startsWith('worker-latest:') && typeof data.sfen === 'string') {
                        try {
                            setPositionFromSFEN(normalizeSFEN(data.sfen));
                            tPosition = now();
                            setMoves(movesForBoard);
                            tMoves = now();
                            adapter.current_ply = displayPly;
                            cache._boardGotoPly = displayPly;
                        } catch (fallbackError) {
                            warnSoftFailure(`Failed to apply SFEN fallback for card ${cardState.id}`, fallbackError);
                        }
                    }
                }
            }
            cardState.viewPly = autoView;
            syncWorkerViewFromCard(cardState);
        } else {
            if ((cardState.viewPly ?? 0) > maxView) {
                cardState.viewPly = maxView;
            }
            const targetPly = cardState.viewPly ?? 0;
            const displayPly = Math.min(targetPly, maxPly);
            if (cache._boardGotoPly !== displayPly) {
                try {
                    goTo(displayPly);
                    tGoto = now();
                    cache._boardGotoPly = displayPly;
                } catch (error) {
                    warnSoftFailure(`Failed to render board for card ${cardState.id}`, error);
                    if (source.startsWith('worker-latest:') && typeof data.sfen === 'string') {
                        try {
                            setPositionFromSFEN(normalizeSFEN(data.sfen));
                            tPosition = now();
                            setMoves(movesForBoard);
                            tMoves = now();
                            adapter.current_ply = displayPly;
                            cache._boardGotoPly = displayPly;
                        } catch (fallbackError) {
                            warnSoftFailure(`Failed to apply SFEN fallback for card ${cardState.id}`, fallbackError);
                        }
                    }
                }
                if (displayPly === 0 && highlightSquares) {
                    highlightSquares([]);
                    tHighlight = now();
                }
            }
            syncWorkerViewFromCard(cardState);
        }

        if (!Array.isArray(data.moves) || data.moves.length === 0) {
            try {
                if (cache._boardGotoPly !== 0) {
                    goTo(0);
                    tGoto = now();
                    cache._boardGotoPly = 0;
                }
            } catch (error) {
                warnSoftFailure(`Failed to reset board for card ${cardState.id}`, error);
            }
            cardState.viewPly = 0;
            syncWorkerViewFromCard(cardState);
        }
        // Board resize is handled by the board adapter layer via ResizeObserver (and a one-shot rAF on mount).
        // Scheduling per-update rAF resizes here creates unnecessary backlog under high-rate updates.
        tEnd = now();
        const totalMs = Math.max(0, tEnd - started);
        recordLiveDiagnosticsMetric('live.cards.board_sync', {
            triggered: 1,
            pos_ms: Math.max(0, tPosition - started),
            moves_ms: Math.max(0, tMoves - tPosition),
            goto_ms: Math.max(0, tGoto - tMoves),
            highlight_ms: Math.max(0, tHighlight - (tGoto || tMoves || tPosition)),
            resize_ms: 0,
            total_ms: totalMs,
        });

        const slowThresholdMs = 20;
        if (totalMs >= slowThresholdMs) {
            const sourceKind = source.startsWith('worker-latest:') ? 1 : source.startsWith('db-game:') ? 2 : 0;
            recordLiveDiagnosticsMetric('live.cards.board_sync.slow', {
                triggered: 1,
                total_ms: totalMs,
                moves_len: movesForBoard.length,
                view_ply: cardState.viewPly ?? 0,
                auto_sync: cardState.autoSync ? 1 : 0,
                new_game: newGameDetected ? 1 : 0,
                source_kind: sourceKind,
                rev,
            });
        }
    };
}
