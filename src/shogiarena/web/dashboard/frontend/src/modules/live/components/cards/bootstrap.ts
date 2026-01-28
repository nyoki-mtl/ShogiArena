import type { LiveCardId, LiveCardState } from '@/modules/live/types';

interface BootstrapDependencies {
    getCards: () => LiveCardState[];
    setCards: (cards: LiveCardState[]) => void;
    getNumWorkers: () => number;
    getMaxLiveBoardLimit: () => number | null;
    getSelectedCardId: () => LiveCardId | null;
    isOfflineNotified: () => boolean;
    allocateNextCardId: () => LiveCardId;
    createCardElement: (card: LiveCardState) => HTMLElement;
    isSoftLimitReached: (additional?: number) => boolean;
    notifySoftLimit: () => void;
    resetSoftLimitNotice: () => void;
    updateAddCardTileState: () => void;
    appendAddCardTile: () => void;
    setSelectedCard: (id: LiveCardId | null) => void;
    refreshCardsForWorker: (workerIdx: number) => void;
    freezeAllWorkerClocks: () => void;
    clearBoardAdapters: () => void;
    saveLayout: () => void;
    updateCardData: (card: LiveCardState, options?: { forceBootstrap?: boolean }) => Promise<void>;
    warnSoftFailure: (message: string, error: unknown) => void;
}

export function createCardsBootstrap(deps: BootstrapDependencies): {
    bootstrapVisibleWorkerCards: () => Promise<void>;
    initializeCards: () => void;
    openAllWorkerCards: () => void;
} {
    const {
        getCards,
        setCards,
        getNumWorkers,
        getMaxLiveBoardLimit,
        getSelectedCardId,
        isOfflineNotified,
        allocateNextCardId,
        createCardElement,
        isSoftLimitReached,
        notifySoftLimit,
        resetSoftLimitNotice,
        updateAddCardTileState,
        appendAddCardTile,
        setSelectedCard,
        refreshCardsForWorker,
        freezeAllWorkerClocks,
        clearBoardAdapters,
        saveLayout,
        updateCardData,
        warnSoftFailure,
    } = deps;

    async function bootstrapVisibleWorkerCards(): Promise<void> {
        const workerCards = getCards().filter((card) => card?.source?.startsWith('worker-latest:'));
        if (!workerCards.length) {
            return;
        }
        const jobs = workerCards.map((card) =>
            updateCardData(card, { forceBootstrap: true }).catch((error) => {
                warnSoftFailure('Failed to bootstrap live worker card', error);
            }),
        );
        await Promise.allSettled(jobs);
    }

    function initializeCards(): void {
        const grid = document.getElementById('workersGrid');
        if (!grid) return;

        grid.innerHTML = '';
        clearBoardAdapters();
        setCards([]);
        const cards = getCards();

        const workerCount = getNumWorkers();
        const limit = getMaxLiveBoardLimit();
        const createCount = limit == null ? workerCount : Math.min(workerCount, limit);

        for (let i = 0; i < createCount; i++) {
            const cardState: LiveCardState = {
                id: allocateNextCardId(),
                source: `worker-latest:${i}`,
                autoSync: true,
                viewPly: 0,
            };
            cards.push(cardState);
            const cardEl = createCardElement(cardState);
            grid.appendChild(cardEl);
        }
        if (limit != null && workerCount > limit) {
            notifySoftLimit();
        }

        saveLayout();

        appendAddCardTile();
        updateAddCardTileState();

        if (cards.length && getSelectedCardId() == null) {
            setSelectedCard(cards[0].id);
        }
        const owner = window as typeof window & { ARENA_DASHBOARD_STOPPED?: boolean };
        if (isOfflineNotified() || owner.ARENA_DASHBOARD_STOPPED === true) {
            freezeAllWorkerClocks();
        }
    }

    function openAllWorkerCards(): void {
        const grid = document.getElementById('workersGrid');
        if (!grid) return;

        const total = getNumWorkers();
        let created = false;

        for (let workerIdx = 0; workerIdx < total; workerIdx += 1) {
            const source = `worker-latest:${workerIdx}`;
            if (getCards().some((card) => card?.source === source)) {
                continue;
            }
            if (isSoftLimitReached(1)) {
                notifySoftLimit();
                break;
            }
            const cardState: LiveCardState = {
                id: allocateNextCardId(),
                source,
                autoSync: true,
                viewPly: 0,
            };
            const cards = getCards();
            cards.push(cardState);
            const cardEl = createCardElement(cardState);
            const addTile = document.getElementById('add-card-tile');
            if (addTile && addTile.parentNode === grid) {
                grid.insertBefore(cardEl, addTile);
            } else {
                grid.appendChild(cardEl);
            }
            created = true;
        }

        if (created) {
            appendAddCardTile();
            updateAddCardTileState();
            saveLayout();
        }

        for (let workerIdx = 0; workerIdx < total; workerIdx += 1) {
            refreshCardsForWorker(workerIdx);
        }

        resetSoftLimitNotice();
    }

    return {
        bootstrapVisibleWorkerCards,
        initializeCards,
        openAllWorkerCards,
    };
}
