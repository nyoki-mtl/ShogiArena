import type { LiveCardId, LiveCardState } from '@/modules/live/types';
import type { DashboardCoreState } from '@/types/dashboard';
import { pushCard, setCardList } from './state';

interface CardsControllerDeps {
    state: DashboardCoreState;
    getCards: () => LiveCardState[];
    notifySoftLimit: () => void;
    isSoftLimitReached: (additional?: number) => boolean;
    resetSoftLimitNotice: () => void;
    updateAddCardTileState: () => void;
    appendAddCardTile: () => void;
    saveLayout: () => void;
    updateCardData: (card: LiveCardState) => Promise<void>;
    createCard: (card: LiveCardState) => HTMLElement;
    disposeBoardAdapter: (cardId: LiveCardId) => void;
    setSelectedCard: (id: LiveCardId | null) => void;
    onCardsChanged?: (reason: string) => void;
}

export interface CardsControllerApi {
    addCard: () => LiveCardState | null;
    createCardForSource: (source: string, options?: { autoSync?: boolean }) => LiveCardState | null;
    deleteCard: (cardId: LiveCardId) => void;
}

export function createCardsController(deps: CardsControllerDeps): CardsControllerApi {
    const {
        state,
        getCards,
        notifySoftLimit,
        isSoftLimitReached,
        resetSoftLimitNotice,
        updateAddCardTileState,
        appendAddCardTile,
        saveLayout,
        updateCardData,
        createCard,
        disposeBoardAdapter,
        setSelectedCard,
        onCardsChanged,
    } = deps;

    function addCard(): LiveCardState | null {
        if (isSoftLimitReached(1)) {
            notifySoftLimit();
            updateAddCardTileState();
            return null;
        }
        const cardState: LiveCardState = {
            id: state.nextCardId++,
            source: 'empty',
            autoSync: true,
            viewPly: 0,
        };
        pushCard(state, cardState);

        const grid = document.getElementById('workersGrid');
        const cardEl = createCard(cardState);
        const addTile = document.getElementById('add-card-tile');
        if (grid) {
            if (addTile && addTile.parentNode === grid) {
                grid.insertBefore(cardEl, addTile);
            } else {
                grid.appendChild(cardEl);
            }
        }

        setTimeout(() => void updateCardData(cardState), 0);
        saveLayout();
        updateAddCardTileState();
        onCardsChanged?.('add');
        return cardState;
    }

    function createCardForSource(source: string, options: { autoSync?: boolean } = {}): LiveCardState | null {
        if (isSoftLimitReached(1)) {
            notifySoftLimit();
            updateAddCardTileState();
            return null;
        }
        const autoSyncDefault = !source.startsWith('db-game:');
        const cardState: LiveCardState = {
            id: state.nextCardId++,
            source,
            autoSync: typeof options.autoSync === 'boolean' ? options.autoSync : autoSyncDefault,
            ...(source.startsWith('db-game:') ? {} : { viewPly: 0 }),
        };
        pushCard(state, cardState);

        const grid = document.getElementById('workersGrid');
        if (grid) {
            const cardEl = createCard(cardState);
            const addTile = document.getElementById('add-card-tile');
            if (addTile && addTile.parentNode === grid) {
                grid.insertBefore(cardEl, addTile);
            } else {
                grid.appendChild(cardEl);
            }
            appendAddCardTile();
            updateAddCardTileState();
        }

        saveLayout();
        onCardsChanged?.('add_source');
        return cardState;
    }

    function deleteCard(cardId: LiveCardId): void {
        const cards = getCards();
        const wasSelected = state.selectedCardId === cardId;

        const nextCards = cards.filter((c) => c.id !== cardId);
        setCardList(state, nextCards);

        disposeBoardAdapter(cardId);

        const cardEl = document.getElementById(`card-${cardId}`);
        if (cardEl) {
            cardEl.remove();
        }

        if (wasSelected) {
            if (nextCards.length) {
                setSelectedCard(nextCards[0].id);
            } else {
                setSelectedCard(null);
            }
        }

        resetSoftLimitNotice();
        updateAddCardTileState();
        saveLayout();
        onCardsChanged?.('delete');
    }

    return {
        addCard,
        createCardForSource,
        deleteCard,
    };
}
