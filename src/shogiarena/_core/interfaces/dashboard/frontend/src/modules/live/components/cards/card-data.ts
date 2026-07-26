import type { LiveBoardAdapter, LiveCardState } from '@/modules/live/types';
import type { EngineStatusSnapshot } from '@/modules/live/utils/engine-status';
import { asEngineStatusSnapshot, isEngineStateReady } from '@/modules/live/utils/engine-status';
import { hasTerminalResult } from '@/modules/live/utils';
import type { DashboardCoreState } from '@/types/dashboard';
import { createBoardSync } from './board-sync';
import { updateCardDisplay } from './card-display';
import { detectNewGame, initializeViewPlyIfNeeded } from './card-state';
import { createClockSync } from './clock-sync';
import { resolveSnapshotForDbGameCard, resolveSnapshotForWorkerCard } from './snapshot-resolvers';
import { getWorkerStateEntry } from './state';
import type { WorkerSnapshotRecord } from './types';

export interface UpdateCardDataOptions {
    forceBootstrap?: boolean;
}

export type EngineIoTail = EngineStatusSnapshot['black']['io_tail'];

/**
 * Does the lifecycle-derived io_tail already show the game kickoff (`usinewgame` / `position`)?
 *
 * Kickoff detection must never require the raw I/O topic: those lines are already carried by the
 * `engine_status` snapshot (task 0052 / Decision 12).
 */
export const hasKickoffCommand = (entries: EngineIoTail | undefined): boolean => {
    if (!Array.isArray(entries) || entries.length === 0) return false;
    return entries.some((entry) => {
        if (!entry || entry.dir !== 'out' || typeof entry.line !== 'string') return false;
        const line = entry.line.trim().toLowerCase();
        return line === 'usinewgame' || line.startsWith('position ');
    });
};

/**
 * Should the prep overlay be shown for this card?
 *
 * This is a pure display decision derived from lifecycle state only. It deliberately does not
 * touch the raw-I/O preference, so the default Live grid keeps zero raw subscriptions no matter
 * how many games run in parallel (review finding M7).
 */
export const isPrepOverlayVisibleFor = (input: {
    phase: 'pre' | 'in' | 'post';
    isBlackReady: boolean;
    isWhiteReady: boolean;
    blackIoTail: EngineIoTail | undefined;
    whiteIoTail: EngineIoTail | undefined;
}): boolean => {
    if (input.phase !== 'pre') return false;
    const kickoffStarted =
        input.isBlackReady &&
        input.isWhiteReady &&
        hasKickoffCommand(input.blackIoTail) &&
        hasKickoffCommand(input.whiteIoTail);
    return !kickoffStarted;
};

export interface CardDataDeps {
    state: DashboardCoreState;
    normalizeSFEN: (sfen: string | null | undefined) => string;
    computeAutoViewPly: (data: WorkerSnapshotRecord, includeTerminal?: boolean) => number;
    syncWorkerViewFromCard: (cardState: LiveCardState) => void;
    getStableGameKey: (snapshot: WorkerSnapshotRecord) => string | null;
    fetchAndCacheWorkerSnapshot: (workerIdx: number) => Promise<WorkerSnapshotRecord | null>;
    getCachedWorkerSnapshot: (workerIdx: number) => WorkerSnapshotRecord | null;
    getGameData: (gameId: string) => Promise<WorkerSnapshotRecord | null>;
    updateElement: (id: string, value: unknown) => void;
    formatTimeControlShort: (tc: string | null | undefined) => string;
    updateMoveSummaryCard: (cardId: number | string, data: WorkerSnapshotRecord) => void;
    updateEvalChartCard: (cardId: number | string, data: WorkerSnapshotRecord) => void;
    updateStaticClocksForCard: (cardId: number | string, data: WorkerSnapshotRecord) => void;
    getBoardAdapter: (state: DashboardCoreState, cardId: number | string) => LiveBoardAdapter | null | undefined;
    warnSoftFailure: (context: string, error: unknown) => void;
    closeKifuPopover: () => void;
    onEngineLogGameKeyChange?: (cardState: LiveCardState, prevKey: string | null, nextKey: string) => void;
    /**
     * Release the raw-I/O subscription of a finished game without closing its display.
     *
     * The topic is per game, so completion must drop the refcount to 0 (task 0052 / Decision 12).
     */
    releaseRawSubscriptions?: (cardState: LiveCardState) => void;
}

export function createUpdateCardData(deps: CardDataDeps) {
    const {
        state,
        normalizeSFEN,
        computeAutoViewPly,
        syncWorkerViewFromCard,
        getStableGameKey,
        fetchAndCacheWorkerSnapshot,
        getCachedWorkerSnapshot,
        getGameData,
        updateElement,
        formatTimeControlShort,
        updateMoveSummaryCard,
        updateEvalChartCard,
        updateStaticClocksForCard,
        getBoardAdapter,
        warnSoftFailure,
        closeKifuPopover,
        onEngineLogGameKeyChange,
        releaseRawSubscriptions,
    } = deps;

    const syncBoard = createBoardSync({
        normalizeSFEN,
        computeAutoViewPly,
        syncWorkerViewFromCard,
        warnSoftFailure,
        getBoardAdapter,
        state,
    });

    const syncClocks = createClockSync({ updateStaticClocksForCard });

    const hasEngineRoleState = (entry: unknown): boolean => {
        if (!entry || typeof entry !== 'object') return false;
        const role = entry as { state?: unknown; io_tail?: unknown };
        if (typeof role.state === 'string' && role.state.trim()) return true;
        if (Array.isArray(role.io_tail) && role.io_tail.length > 0) return true;
        return false;
    };

    const resolveEngineStatus = (
        cardState: LiveCardState,
        data: WorkerSnapshotRecord,
    ): EngineStatusSnapshot | undefined => {
        const direct = asEngineStatusSnapshot(data.engine_status);
        if (direct && (hasEngineRoleState(direct.black) || hasEngineRoleState(direct.white))) {
            return direct;
        }
        if (cardState.source.startsWith('worker-latest:')) {
            const workerIdx = Number(cardState.source.split(':')[1]);
            if (Number.isFinite(workerIdx)) {
                const ws = getWorkerStateEntry(state, workerIdx);
                return asEngineStatusSnapshot(ws.engineStatus);
            }
        }
        return direct;
    };

    const formatEngineLabel = (
        role: 'black' | 'white',
        name: string,
        _state: EngineStatusSnapshot['black']['state'] | undefined,
    ): string => {
        return name.trim() ? `${role === 'black' ? 'Black' : 'White'}: ${name.trim()}` : role;
    };

    const handshakePlaceholderByState = (state: EngineStatusSnapshot['black']['state'] | undefined): string => {
        if (state === 'queued') return 'エンジン資源を待機中...';
        if (state === 'waiting_for_usiok') return 'USI 初期化を待っています...';
        if (state === 'waiting_for_readyok') return 'readyok を待っています...';
        return 'エンジン応答を待っています...';
    };

    const updateEngineLogLabels = (
        cardId: number | string,
        data: WorkerSnapshotRecord,
        engineStatus: EngineStatusSnapshot | undefined,
    ): void => {
        const cardEl = document.getElementById(`card-${cardId}`);
        if (!cardEl) return;
        const labelMap: Array<{ role: 'black' | 'white'; label: string }> = [
            {
                role: 'white',
                label: formatEngineLabel('white', data.white_name ?? '', engineStatus?.white?.state),
            },
            {
                role: 'black',
                label: formatEngineLabel('black', data.black_name ?? '', engineStatus?.black?.state),
            },
        ];
        for (const item of labelMap) {
            const section = cardEl.querySelector<HTMLElement>(
                `.worker-card__overlay-section--${item.role} .worker-card__overlay-label`,
            );
            if (section) {
                section.textContent = item.label;
            }
        }
    };

    const renderPrepEngineLog = (
        cardId: number | string,
        data: WorkerSnapshotRecord,
        engineStatus: EngineStatusSnapshot | undefined,
        visibleRoles: { black: boolean; white: boolean },
    ): void => {
        const body = document.getElementById(`engine-log-body-${cardId}`);
        if (!body) return;
        const sections: Array<{
            role: 'black' | 'white';
            label: string;
            entries: EngineStatusSnapshot['black']['io_tail'];
            visible: boolean;
        }> = [
            {
                role: 'white',
                label: formatEngineLabel('white', data.white_name ?? '', engineStatus?.white?.state),
                entries: engineStatus?.white?.io_tail ?? [],
                visible: visibleRoles.white,
            },
            {
                role: 'black',
                label: formatEngineLabel('black', data.black_name ?? '', engineStatus?.black?.state),
                entries: engineStatus?.black?.io_tail ?? [],
                visible: visibleRoles.black,
            },
        ];

        for (const section of sections) {
            const sectionId = `engine-log-section-${section.role}-${cardId}`;
            let sectionEl = document.getElementById(sectionId);
            if (!sectionEl) {
                sectionEl = document.createElement('div');
                sectionEl.id = sectionId;
                sectionEl.className = `worker-card__overlay-section worker-card__overlay-section--${section.role}`;
                sectionEl.style.gridRow = section.role === 'black' ? '2 / 3' : '1 / 2';
                const labelEl = document.createElement('div');
                labelEl.className = 'worker-card__overlay-label';
                sectionEl.appendChild(labelEl);
                const entriesEl = document.createElement('div');
                entriesEl.className = 'worker-card__overlay-entries';
                entriesEl.id = `engine-log-entries-${section.role}-${cardId}`;
                sectionEl.appendChild(entriesEl);
                body.appendChild(sectionEl);
            }
            sectionEl.style.gridRow = section.role === 'black' ? '2 / 3' : '1 / 2';
            const labelEl = sectionEl.querySelector<HTMLElement>('.worker-card__overlay-label');
            if (labelEl) {
                labelEl.textContent = section.label;
            }
            const entriesEl = sectionEl.querySelector<HTMLElement>('.worker-card__overlay-entries');
            if (!entriesEl) continue;
            entriesEl.innerHTML = '';
            if (!section.visible) {
                continue;
            }
            if (!section.entries.length) {
                const placeholder = document.createElement('div');
                placeholder.className = 'worker-card__overlay-line worker-card__overlay-line--placeholder';
                placeholder.textContent = handshakePlaceholderByState(engineStatus?.[section.role]?.state);
                entriesEl.appendChild(placeholder);
            } else {
                for (const entry of section.entries) {
                    const lineEl = document.createElement('div');
                    lineEl.className = 'worker-card__overlay-line';
                    const arrow = entry.dir === 'out' ? '>' : '<';
                    const text = entry.line ? entry.line : '(no command)';
                    lineEl.textContent = `${arrow} ${text}`;
                    entriesEl.appendChild(lineEl);
                }
            }
        }
    };

    return async function updateCardData(cardState: LiveCardState, options: UpdateCardDataOptions = {}): Promise<void> {
        const forceBootstrap = options.forceBootstrap === true;
        let data: WorkerSnapshotRecord | null = null;
        let stableGameKey: string | null = null;
        let newGameDetected = false;
        const isWorkerCard = cardState.source.startsWith('worker-latest:');

        if (isWorkerCard) {
            const workerIdx = parseInt(cardState.source.split(':')[1], 10);
            data = await resolveSnapshotForWorkerCard(
                workerIdx,
                {
                    getCachedWorkerSnapshot,
                    fetchAndCacheWorkerSnapshot,
                },
                { forceBootstrap },
            );
            if (!data) {
                return;
            }
            stableGameKey = data ? getStableGameKey(data) : null;

            newGameDetected = detectNewGame(cardState, data, stableGameKey);
            if (!cardState.lastGameId || newGameDetected) {
                cardState.lastGameId = stableGameKey ?? 'startpos';
            }
            if (newGameDetected && Number.isFinite(workerIdx)) {
                if (!data) {
                    return;
                }
                const hasTerminal = hasTerminalResult(data);
                const includeTerminal = Boolean(cardState.autoSync) || hasTerminal;
                cardState.viewPly = cardState.autoSync ? computeAutoViewPly(data, includeTerminal) : 0;
                syncWorkerViewFromCard(cardState);
            }
        } else if (cardState.source.startsWith('db-game:')) {
            const gameId = cardState.source.split(':')[1];
            data = await resolveSnapshotForDbGameCard(gameId, getGameData);
            stableGameKey = data ? getStableGameKey(data) : null;
        }

        if (!data) {
            return;
        }

        const logKeyRaw = (data as { game_id?: unknown }).game_id ?? null;
        const logKey = logKeyRaw != null && String(logKeyRaw).trim() ? String(logKeyRaw).trim() : null;

        if (initializeViewPlyIfNeeded(cardState, data, computeAutoViewPly)) {
            syncWorkerViewFromCard(cardState);
        }

        updateCardDisplay(
            {
                state,
                updateElement,
                formatTimeControlShort,
                updateMoveSummaryCard,
                updateEvalChartCard,
                syncBoard,
                syncClocks,
                closeKifuPopover,
            },
            cardState,
            data,
            { newGameDetected },
        );

        // Update syncing overlay CSS classes based on isSyncing state.
        const cardEl = document.getElementById(`card-${cardState.id}`);
        if (cardEl) {
            const isSyncing = Boolean(cardState.isSyncing);
            const syncingStartedAt = cardState.syncingStartedAt;
            const isDelayed = isSyncing && typeof syncingStartedAt === 'number' && Date.now() - syncingStartedAt > 3000;
            cardEl.classList.toggle('worker-card--syncing', isSyncing);
            cardEl.classList.toggle('worker-card--sync-delayed', isDelayed);
        }

        const engineStatus = resolveEngineStatus(cardState, data);
        const moveCount = Array.isArray(data.moves) ? data.moves.filter((mv) => String(mv ?? '').trim()).length : 0;
        const hasGameActivity = moveCount > 0 || hasTerminalResult(data);
        const isTerminal = hasTerminalResult(data);
        const phase: 'pre' | 'in' | 'post' = isTerminal ? 'post' : hasGameActivity ? 'in' : 'pre';
        // Phase transitions must not *open* the raw-I/O preference (task 0052 / Decision 12).
        // The prep overlay is rendered from lifecycle-derived engine_status, so opening it on a
        // phase change would subscribe every card to both engines' raw log topics.
        //
        // Completion is different: the raw topic is per game, so a finished game must release its
        // subscription (refcount -> 0). The display preference stays on so the user keeps reading
        // the log that already arrived.
        const previousPhase = cardState.engineLogPhase ?? 'pre';
        cardState.engineLogPhase = phase;
        if (phase === 'post' && previousPhase !== 'post') {
            cardState.engineLogReleasedGameKey = logKey ?? null;
            releaseRawSubscriptions?.(cardState);
        } else if (phase !== 'post' && cardState.engineLogReleasedGameKey !== logKey) {
            cardState.engineLogReleasedGameKey = null;
        }
        const logPrefs = (cardState.engineLogPreference ?? {}) as { black?: boolean | null; white?: boolean | null };
        const manualOpenBlack = logPrefs.black === true;
        const manualOpenWhite = logPrefs.white === true;
        const hasManualOpen = manualOpenBlack || manualOpenWhite;
        const isReleasedGame = Boolean(logKey) && cardState.engineLogReleasedGameKey === logKey;
        if (hasManualOpen && logKey && !isReleasedGame) {
            const prevKey = cardState.engineLogGameKey ?? null;
            if (prevKey !== logKey) {
                cardState.engineLogGameKey = logKey;
                onEngineLogGameKeyChange?.(cardState, prevKey, logKey);
            }
        } else if (!hasManualOpen) {
            cardState.engineLogGameKey = null;
        }
        const readyLatch = (cardState.engineReadyLatch ?? {}) as {
            black?: boolean;
            white?: boolean;
            gameKey?: string | null;
        };
        if (readyLatch.gameKey !== logKey) {
            readyLatch.black = false;
            readyLatch.white = false;
            readyLatch.gameKey = logKey ?? null;
        }
        if (isEngineStateReady(engineStatus?.black?.state)) {
            readyLatch.black = true;
        }
        if (isEngineStateReady(engineStatus?.white?.state)) {
            readyLatch.white = true;
        }
        cardState.engineReadyLatch = readyLatch;
        // Kickoff detection uses the lifecycle-derived engine_status snapshot only; the raw I/O
        // topic is never needed for it (task 0052 / Decision 12).
        const isPrepOverlayVisible = isPrepOverlayVisibleFor({
            phase,
            isBlackReady: Boolean(readyLatch.black),
            isWhiteReady: Boolean(readyLatch.white),
            blackIoTail: engineStatus?.black?.io_tail,
            whiteIoTail: engineStatus?.white?.io_tail,
        });
        const refreshedPrefs = (cardState.engineLogPreference ?? {}) as {
            black?: boolean | null;
            white?: boolean | null;
        };
        const finalManualOpenBlack = refreshedPrefs.black === true;
        const finalManualOpenWhite = refreshedPrefs.white === true;
        if (cardEl) {
            // Display state is the union of the prep overlay and the explicitly opened raw panel;
            // only the latter drives a subscription.
            const visibleBlack = finalManualOpenBlack || isPrepOverlayVisible;
            const visibleWhite = finalManualOpenWhite || isPrepOverlayVisible;
            cardEl.classList.toggle('worker-card--log-visible-black', visibleBlack);
            cardEl.classList.toggle('worker-card--log-visible-white', visibleWhite);
        }
        if (phase === 'pre') {
            renderPrepEngineLog(cardState.id, data, engineStatus, {
                black: isPrepOverlayVisible,
                white: isPrepOverlayVisible,
            });
        }
        updateEngineLogLabels(cardState.id, data, engineStatus);
    };
}
