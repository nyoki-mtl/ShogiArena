import type { LiveCardId, LiveCardState } from '@/modules/live/types';
import type { DashboardCore } from '@/types/dashboard';

export interface MutableRef<T> {
    value: T;
}

interface LayoutDependencies {
    getCards: () => LiveCardState[];
    setCards: (cards: LiveCardState[]) => void;
    warnSoftFailure: DashboardCore['warnSoftFailure'];
    parseLiveCardId: (raw: string) => LiveCardId;
    draggingCardRef: MutableRef<number | null>;
}

export interface LayoutController {
    saveLayout: () => void;
    applySavedLayout: () => void;
    updateStateOrderFromDOM: () => void;
    enableDragAndDropForCard: (cardEl: HTMLElement | null) => void;
}

export function createLayoutController(deps: LayoutDependencies): LayoutController {
    const { getCards, setCards, warnSoftFailure, parseLiveCardId, draggingCardRef } = deps;

    const saveLayout = (): void => {
        const sources = getCards().map((card) => card.source);
        try {
            localStorage.setItem('arenaCardSourcesV1', JSON.stringify(sources));
        } catch (error) {
            warnSoftFailure('Failed to persist arena card layout', error);
        }
    };

    const applySavedLayout = (): void => {
        const raw = localStorage.getItem('arenaCardSourcesV1');
        if (!raw) return;

        let desired: unknown;
        try {
            desired = JSON.parse(raw);
        } catch (error) {
            warnSoftFailure('Failed to parse persisted arena layout', error);
            return;
        }
        if (!Array.isArray(desired) || desired.length === 0) return;

        const used = new Set<number | string>();
        const bySource = new Map<string, LiveCardState[]>();
        for (const card of getCards()) {
            const bucket = bySource.get(card.source) ?? [];
            bucket.push(card);
            bySource.set(card.source, bucket);
        }

        const ordered: LiveCardState[] = [];
        for (const source of desired as unknown[]) {
            const list = bySource.get(String(source));
            if (list?.length) {
                const card = list.shift();
                if (card && !used.has(card.id)) {
                    used.add(card.id);
                    ordered.push(card);
                }
            }
        }
        for (const card of getCards()) {
            if (!used.has(card.id)) ordered.push(card);
        }

        setCards(ordered);

        const grid = document.getElementById('workersGrid');
        if (!grid) return;
        const addTile = document.getElementById('add-card-tile');
        for (const card of ordered) {
            const el = document.getElementById(`card-${card.id}`);
            if (el) {
                grid.insertBefore(el, addTile || null);
            }
        }
    };

    const updateStateOrderFromDOM = (): void => {
        const grid = document.getElementById('workersGrid');
        if (!grid) return;

        const ids = Array.from(grid.querySelectorAll<HTMLElement>('.worker-card'))
            .map((el) => parseLiveCardId(el.dataset.cardId ?? ''))
            .filter((value): value is number | string => value !== null && value !== undefined);

        const cards = getCards();
        const byId = new Map<LiveCardId, LiveCardState>(cards.map((card) => [card.id, card]));
        const nextOrder: LiveCardState[] = [];

        for (const id of ids) {
            const card = byId.get(id);
            if (card) {
                nextOrder.push(card);
            }
        }

        for (const card of cards) {
            if (!nextOrder.includes(card)) {
                nextOrder.push(card);
            }
        }

        setCards(nextOrder);
        saveLayout();
    };

    const enableDragAndDropForCard = (cardEl: HTMLElement | null): void => {
        if (!cardEl) return;
        cardEl.setAttribute('draggable', 'true');
        cardEl.addEventListener('dragstart', (event) => {
            const cardIdRaw = cardEl.dataset.cardId;
            const parsed = parseLiveCardId(cardIdRaw ?? '');
            draggingCardRef.value = typeof parsed === 'number' && Number.isFinite(parsed) ? parsed : null;
            cardEl.classList.add('dragging');
            const { dataTransfer } = event;
            if (dataTransfer) {
                dataTransfer.effectAllowed = 'move';
                const payload = draggingCardRef.value == null ? '' : String(draggingCardRef.value);
                dataTransfer.setData('text/plain', payload);
            }
        });

        cardEl.addEventListener('dragend', () => {
            draggingCardRef.value = null;
            cardEl.classList.remove('dragging');
            document.querySelectorAll('.worker-card.drag-over').forEach((el) => {
                el.classList.remove('drag-over');
            });
        });

        cardEl.addEventListener('dragover', (event) => {
            event.preventDefault();
            if (draggingCardRef.value === null) return;
            cardEl.classList.add('drag-over');
            const { dataTransfer } = event;
            if (dataTransfer) {
                dataTransfer.dropEffect = 'move';
            }
        });

        cardEl.addEventListener('dragleave', () => {
            cardEl.classList.remove('drag-over');
        });

        cardEl.addEventListener('drop', (event) => {
            event.preventDefault();
            cardEl.classList.remove('drag-over');

            const targetRaw = cardEl.dataset.cardId;
            const targetId = parseLiveCardId(targetRaw ?? '');
            const sourceId = draggingCardRef.value;

            if (sourceId == null || targetId == null || sourceId === targetId) {
                return;
            }

            const grid = document.getElementById('workersGrid');
            const srcEl = document.getElementById(`card-${String(sourceId)}`);
            const tgtEl = document.getElementById(`card-${String(targetId)}`);

            if (!grid || !srcEl || !tgtEl) {
                return;
            }

            grid.insertBefore(srcEl, tgtEl);
            updateStateOrderFromDOM();
        });
    };

    return {
        saveLayout,
        applySavedLayout,
        updateStateOrderFromDOM,
        enableDragAndDropForCard,
    } satisfies LayoutController;
}
