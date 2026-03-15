import type { LiveCardId, LiveCardState } from '@/modules/live/types';
import type { LiveBoardAdapter } from '@/modules/live/types';
import type { DashboardTabId, DashboardTabsApi } from '@/types/globals';
import type { LiveGameNavigationOptions } from '@/types/globals';
import type { LiveDashboardNamespace } from '@/types/live';
import { hasTerminalResult } from '@/modules/live/utils';
import type { WorkerSnapshotRecord } from './types';
import type { GameMeta } from './metadata';

type FocusLiveTabOptions = LiveGameNavigationOptions;

type CardsWindow = Window & {
    DashboardTabs?: DashboardTabsApi;
    DashboardLive?: LiveDashboardNamespace;
};

export interface CardsNavigationDeps {
    owner: CardsWindow;
    getCards: () => LiveCardState[];
    findCardById: (id: LiveCardId) => LiveCardState | undefined;
    findCardBySource: (source: string) => LiveCardState | undefined;
    createCardForSource: (source: string, options?: { autoSync?: boolean }) => LiveCardState | null;
    changeCardSource: (cardId: LiveCardId, newSource: string) => Promise<void>;
    updateCardData: (card: LiveCardState, options?: { forceBootstrap?: boolean }) => Promise<void>;
    getGameData: (gameId: string) => Promise<WorkerSnapshotRecord>;
    setSelectedCard: (id: LiveCardId | null) => void;
    getGameMetadata: (gameId: string) => GameMeta | null;
    formatVariantDisplay: (variantId: string | null, phase: string | null) => string | null;
    findWorkerIndexByGameId: (gid: string) => number | null | undefined;
    peekWorkerSnapshotRecord: (workerIdx: number) => WorkerSnapshotRecord | null | undefined;
    getCachedWorkerSnapshot: (workerIdx: number) => WorkerSnapshotRecord | null | undefined;
    ensureWorkerSnapshot: (snapshot: WorkerSnapshotRecord | null | undefined) => WorkerSnapshotRecord;
    createEmptySnapshot: () => WorkerSnapshotRecord;
    warnSoftFailure: (message: string, error: unknown) => void;
    getStartingPlyNumber: (sfen: string | undefined) => number;
    updateElement: (id: string, value: unknown) => void;
    updateMoveSummaryCard: (cardId: LiveCardId, data: WorkerSnapshotRecord) => void;
    updateKI2MovesCard: (cardId: LiveCardId, data: WorkerSnapshotRecord) => void;
    updateEvalChartCard: (cardId: LiveCardId, data: WorkerSnapshotRecord) => void;
    updateStaticClocksForCard: (cardId: LiveCardId, data: WorkerSnapshotRecord) => void;
    syncWorkerViewFromCard: (cardState: LiveCardState) => void;
    getBoardAdapterForCard: (cardId: LiveCardId) => LiveBoardAdapter | null | undefined;
}

export interface CardsNavigationApi {
    ensureSelectOption: (select: HTMLSelectElement | null, value: string, label: string) => HTMLOptionElement | null;
    ensureArchivedGameCard: (
        gameId: string | number,
        options?: { forceNew?: boolean },
    ) => Promise<LiveCardState | null>;
    focusGameOnLiveTab: (gameId: string | number, options?: FocusLiveTabOptions) => Promise<void>;
    resolveCardData: (cardState: LiveCardState) => Promise<WorkerSnapshotRecord | null>;
    handleEvalChartClickCard: (cardId: LiveCardId, event: MouseEvent) => Promise<void>;
    goToMoveCard: (
        cardId: LiveCardId,
        target: number | 'first' | 'prev' | 'next' | 'last',
        options?: { data?: WorkerSnapshotRecord },
    ) => Promise<void>;
}

export function createCardsNavigation(deps: CardsNavigationDeps): CardsNavigationApi {
    const {
        owner,
        getCards,
        findCardById,
        findCardBySource,
        createCardForSource,
        changeCardSource,
        updateCardData,
        getGameData,
        setSelectedCard,
        getGameMetadata,
        formatVariantDisplay,
        findWorkerIndexByGameId,
        peekWorkerSnapshotRecord,
        getCachedWorkerSnapshot,
        ensureWorkerSnapshot,
        createEmptySnapshot,
        warnSoftFailure,
        getStartingPlyNumber,
        updateElement,
        updateMoveSummaryCard,
        updateKI2MovesCard,
        updateEvalChartCard,
        updateStaticClocksForCard,
        syncWorkerViewFromCard,
        getBoardAdapterForCard,
    } = deps;

    function ensureSelectOption(select: HTMLSelectElement | null, value: string, label: string) {
        if (!select) return null;
        const optionsList = Array.from(select.options || []);
        let option = optionsList.find((opt) => opt.value === value) ?? null;
        if (!option) {
            option = document.createElement('option');
            option.value = value;
            option.dataset.injected = 'true';
            select.appendChild(option);
        }
        option.textContent = label;
        option.title = label;

        if (value.startsWith('db-game:')) {
            const gid = value.split(':')[1] || '';
            const meta = getGameMetadata(gid);
            if (meta?.variantId) {
                option.dataset.variantId = meta.variantId;
            } else {
                delete option.dataset.variantId;
            }
            if (meta?.phase) {
                option.dataset.phase = meta.phase;
            } else {
                delete option.dataset.phase;
            }
            const variantLabel = formatVariantDisplay(meta?.variantId ?? null, meta?.phase ?? null);
            if (variantLabel && (!label || label === gid)) {
                option.textContent = variantLabel;
                option.title = variantLabel;
            }
        }

        return option;
    }

    async function ensureArchivedGameCard(
        gameId: string | number,
        options: { forceNew?: boolean } = {},
    ): Promise<LiveCardState | null> {
        const gid = String(gameId || '').trim();
        if (!gid) return null;
        const source = `db-game:${gid}`;
        const forceNew = options.forceNew === true;
        let card: LiveCardState | null = null;
        if (!forceNew) {
            card = findCardBySource(source) || null;
        }
        if (!card) {
            card = createCardForSource(source, { autoSync: false });
            if (!card) {
                return null;
            }
        }

        const select = document.getElementById(`source-${card.id}`) as HTMLSelectElement | null;
        ensureSelectOption(select, source, gid || '-');
        if (select) select.value = source;

        if (card.source !== source) {
            await changeCardSource(card.id, source);
        } else {
            card.autoSync = false;
            await updateCardData(card);
        }

        return card;
    }

    async function focusGameOnLiveTab(gameId: string | number, options: FocusLiveTabOptions = {}): Promise<void> {
        const gid = String(gameId || '').trim();
        if (!gid) return;

        if (owner.DashboardTabs && typeof owner.DashboardTabs.setActive === 'function') {
            owner.DashboardTabs.setActive('live' as DashboardTabId);
        }

        const workerIdx = findWorkerIndexByGameId(gid);
        if (getCards().length === 0) {
            if (workerIdx != null) {
                const workerSource = `worker-latest:${workerIdx}`;
                const workerCard = createCardForSource(workerSource, { autoSync: true });
                if (!workerCard) {
                    return;
                }
            } else {
                const archivedCard = createCardForSource(`db-game:${gid}`, { autoSync: false });
                if (!archivedCard) {
                    return;
                }
            }
        }

        let cardToFocus: LiveCardState | null = null;

        if (workerIdx != null) {
            const workerSource = `worker-latest:${workerIdx}`;
            let workerCard = findCardBySource(workerSource) || null;
            if (!workerCard) {
                workerCard = createCardForSource(workerSource, { autoSync: true });
                if (!workerCard) {
                    return;
                }
            }

            const workerSelect = document.getElementById(`source-${workerCard.id}`) as HTMLSelectElement | null;
            if (workerSelect) {
                workerSelect.value = workerSource;
            }

            if (workerCard.source !== workerSource) {
                await changeCardSource(workerCard.id, workerSource);
            } else {
                workerCard.autoSync = true;
                await updateCardData(workerCard);
            }

            const workerData = peekWorkerSnapshotRecord(workerIdx);
            const currentGid = workerData?.game_id ?? workerData?.gameId ?? null;
            if (currentGid && String(currentGid) !== gid) {
                const archivedCard = await ensureArchivedGameCard(gid, { forceNew: options.forceNewArchived === true });
                cardToFocus = archivedCard || workerCard;
            } else {
                cardToFocus = workerCard;
            }
        } else {
            cardToFocus = await ensureArchivedGameCard(gid, { forceNew: options.forceNewArchived === true });
        }

        if (!cardToFocus) return;

        setSelectedCard(cardToFocus.id);
        requestAnimationFrame(() => {
            const cardEl = document.getElementById(`card-${cardToFocus.id}`);
            if (cardEl && typeof cardEl.scrollIntoView === 'function') {
                try {
                    cardEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
                } catch (error) {
                    console.debug('scrollIntoView failed', error);
                }
            }
        });
    }

    async function resolveCardData(cardState: LiveCardState): Promise<WorkerSnapshotRecord | null> {
        if (cardState.source.startsWith('worker-latest:')) {
            const workerIdx = Number(cardState.source.split(':')[1]);
            if (Number.isFinite(workerIdx)) {
                const snapshot = getCachedWorkerSnapshot(workerIdx);
                if (snapshot) {
                    return ensureWorkerSnapshot(snapshot);
                }
            }
            return createEmptySnapshot();
        }

        if (cardState.source.startsWith('db-game:')) {
            const gameId = cardState.source.split(':')[1];
            if (gameId) {
                try {
                    return await getGameData(gameId);
                } catch (error) {
                    warnSoftFailure(`Failed to load game data for card ${cardState.id}`, error);
                    return null;
                }
            }
        }

        if (cardState.source === 'empty') {
            return createEmptySnapshot();
        }

        return null;
    }

    async function handleEvalChartClickCard(cardId: LiveCardId, event: MouseEvent): Promise<void> {
        const cardState = findCardById(cardId);
        if (!cardState) return;

        const canvas = event.currentTarget as HTMLCanvasElement | null;
        if (!canvas) return;

        const rect = canvas.getBoundingClientRect();
        const rawX = event.clientX - rect.left;
        const plotLeft = 34;
        const plotRight = 6;
        const width = canvas.offsetWidth || rect.width;
        const plotWidth = width - plotLeft - plotRight;
        if (plotWidth <= 0) {
            return;
        }

        let normalized = 0;
        if (rawX <= plotLeft) {
            normalized = 0;
        } else if (rawX >= width - plotRight) {
            normalized = 1;
        } else {
            normalized = (rawX - plotLeft) / plotWidth;
            normalized = Math.max(0, Math.min(1, normalized));
        }

        const data = await resolveCardData(cardState);
        if (!data) return;

        const moveCount = Array.isArray(data.moves) ? data.moves.length : 0;
        const evalBlackLen = Array.isArray(data.eval_black) ? data.eval_black.length : 0;
        const evalWhiteLen = Array.isArray(data.eval_white) ? data.eval_white.length : 0;
        const maxPlies = Math.max(moveCount, evalBlackLen, evalWhiteLen);
        const hasTerminal = hasTerminalResult(data);
        const maxView = moveCount + (hasTerminal ? 1 : 0);
        const startPlyNumber = getStartingPlyNumber(data.initial_sfen);
        const startOffset = Math.max(0, startPlyNumber - 1);
        const spanPlies = Math.max(maxPlies, maxView);
        const totalSpan = Math.max(1, startOffset + spanPlies);

        if (maxPlies === 0) {
            await goToMoveCard(cardId, 0, { data });
            return;
        }

        const domainPoint = normalized * totalSpan;
        const snappedMoveNumber = Math.round(domainPoint);
        const clampedMoveNumber = Math.max(0, Math.min(totalSpan, snappedMoveNumber));
        let targetPly = 0;
        if (clampedMoveNumber >= startPlyNumber) {
            targetPly = clampedMoveNumber - startPlyNumber + 1;
        }
        const clampUpper = maxView > 0 ? maxView : 0;
        targetPly = Math.max(0, Math.min(clampUpper, targetPly));
        await goToMoveCard(cardId, targetPly, { data });
    }

    async function goToMoveCard(
        cardId: LiveCardId,
        target: number | 'first' | 'prev' | 'next' | 'last',
        options: { data?: WorkerSnapshotRecord } = {},
    ): Promise<void> {
        const cardState = findCardById(cardId);
        if (!cardState) return;

        const adapter = getBoardAdapterForCard(cardId);
        if (!adapter) return;
        if (typeof adapter.goTo !== 'function') return;
        const goTo = adapter.goTo.bind(adapter);
        const highlightSquares =
            typeof adapter.highlightSquares === 'function' ? adapter.highlightSquares.bind(adapter) : null;

        const preloaded = options?.data ?? null;
        const data = preloaded || (await resolveCardData(cardState));

        if (!data || !data.moves) return;

        let newPly = cardState.viewPly || 0;
        const maxPly = data.moves.length;
        const hasTerminal = hasTerminalResult(data);
        const maxView = maxPly + (hasTerminal ? 1 : 0);

        if (typeof target === 'number') {
            newPly = target;
        } else {
            switch (target) {
                case 'first':
                    newPly = 0;
                    break;
                case 'prev':
                    newPly = Math.max(0, newPly - 1);
                    break;
                case 'next':
                    newPly = Math.min(maxView, newPly + 1);
                    break;
                case 'last':
                    newPly = maxView;
                    cardState.autoSync = true;
                    break;
            }
        }

        const displayPly = Math.min(newPly, maxPly);
        goTo(displayPly);
        if (displayPly === 0 && highlightSquares) {
            highlightSquares([]);
        }
        cardState.viewPly = newPly;
        syncWorkerViewFromCard(cardState);

        cardState.autoSync = newPly >= maxView;

        updateElement(`ply-${cardId}`, newPly);
        updateMoveSummaryCard(cardId, data);
        updateKI2MovesCard(cardId, data);
        updateEvalChartCard(cardId, data);

        const card = findCardById(cardId);
        if (card) {
            if (card.source.startsWith('db-game:')) {
                updateStaticClocksForCard(cardId, data);
            } else if (card.source.startsWith('worker-latest:')) {
                const maxPlyWorker = Array.isArray(data.moves) ? data.moves.length : 0;
                const liveDisplayPly = Math.min(Number(data.current_ply || 0), maxPlyWorker);
                const hasTerminal = hasTerminalResult(data);
                const atTerminal = hasTerminal && Number(card.viewPly || 0) === maxPlyWorker + 1;
                const isLiveView =
                    Boolean(card.autoSync) && Number(card.viewPly || 0) === liveDisplayPly && !atTerminal;
                if (!isLiveView) {
                    updateStaticClocksForCard(cardId, data);
                }
            }
        }
    }

    return {
        ensureSelectOption,
        ensureArchivedGameCard,
        focusGameOnLiveTab,
        resolveCardData,
        handleEvalChartClickCard,
        goToMoveCard,
    };
}
