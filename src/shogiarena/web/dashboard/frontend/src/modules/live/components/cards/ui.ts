import { escapeHtml } from '@/modules/shared/utils/html';

type MoveTarget = number | 'first' | 'prev' | 'next' | 'last';

export interface CardElementDeps {
    resolveSourceDisplay: (source: string) => string;
    enableDragAndDropForCard: (card: HTMLElement) => void;
    handleEvalChartClickCard: (cardId: number | string, event: MouseEvent) => void;
    setSelectedCard: (cardId: number | string | null) => void;
    deleteCard: (cardId: number | string) => void;
    changeCardSource: (cardId: number | string, newSource: string) => Promise<void> | void;
    goToMoveCard: (cardId: number | string, target: MoveTarget) => Promise<void> | void;
    toggleKifuCard: (cardId: number | string) => Promise<void> | void;
    isInteractiveElement: (node: Element | null | undefined) => boolean;
    populateSourceDropdown: (cardId: number | string) => void;
    mountBoardAdapter?: (cardId: number | string, card: HTMLElement) => void;
}

export function createCardElement(
    cardState: { id: number | string; source: string },
    deps: CardElementDeps,
): HTMLElement {
    const {
        resolveSourceDisplay,
        enableDragAndDropForCard,
        handleEvalChartClickCard,
        setSelectedCard,
        deleteCard,
        changeCardSource,
        goToMoveCard,
        toggleKifuCard,
        isInteractiveElement,
        populateSourceDropdown,
        mountBoardAdapter,
    } = deps;

    const card = document.createElement('div');
    card.className = 'worker-card';
    card.id = `card-${cardState.id}`;
    card.dataset.cardId = String(cardState.id);

    enableDragAndDropForCard(card);

    const sourceDisplay = resolveSourceDisplay(cardState.source);
    const escapedSourceDisplay = escapeHtml(sourceDisplay);
    const escapedSourceValue = escapeHtml(cardState.source);

    card.innerHTML = `
        <div class="worker-header">
            <div class="worker-header__title">
                <label class="worker-source worker-source--primary" for="source-${cardState.id}">
                    <span class="worker-source__label sr-only">カードソース</span>
                    <select
                        class="source-select source-select--primary"
                        id="source-${cardState.id}"
                        data-ui-action="source-select"
                        aria-label="カードソース"
                        title="${escapedSourceDisplay}"
                    >
                        <option value="${escapedSourceValue}" title="${escapedSourceDisplay}">${escapedSourceDisplay}</option>
                    </select>
                </label>
            </div>
            <div class="worker-header__controls">
                <button
                    type="button"
                    class="delete-card-btn"
                    data-ui-action="delete-card"
                    aria-label="カードを削除"
                    title="カードを削除"
                ><span aria-hidden="true">×</span></button>
            </div>
        </div>
        <div class="clock-row top" id="row-white-${cardState.id}">
            <span class="side-header"><span class="side-label">White:</span><span class="side-name" id="white-name-${cardState.id}">-</span></span>
            <span class="side-tc" id="white-tc-${cardState.id}"></span>
            <span class="side-inc" id="white-inc-${cardState.id}">&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;</span>
            <span class="side-remaining" id="white-remaining-${cardState.id}">-</span>
        </div>
        <div class="board-container" id="board-${cardState.id}"></div>
        <div class="clock-row bottom" id="row-black-${cardState.id}">
            <span class="side-header"><span class="side-label">Black:</span><span class="side-name" id="black-name-${cardState.id}">-</span></span>
            <span class="side-tc" id="black-tc-${cardState.id}"></span>
            <span class="side-inc" id="black-inc-${cardState.id}">&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;</span>
            <span class="side-remaining" id="black-remaining-${cardState.id}">-</span>
        </div>
        <div class="eval-chart">
            <canvas class="eval-canvas" id="evalChart-${cardState.id}"></canvas>
        </div>
        <div class="move-summary" id="moveSummary-${cardState.id}"
             data-ui-action="toggle-kifu">(開始局面)</div>
        <div class="ki2-container" id="ki2-${cardState.id}"></div>
    `;

    const evalCanvas = card.querySelector('.eval-canvas');
    if (evalCanvas) {
        evalCanvas.addEventListener('click', (event: MouseEvent) => {
            void handleEvalChartClickCard(cardState.id, event);
        });
    }

    card.addEventListener('pointerdown', (event) => {
        if (typeof event.button === 'number' && event.button !== 0) return;
        const target = event.target as Element | null;
        if (isInteractiveElement(target)) return;
        setSelectedCard(cardState.id);
    });

    const deleteButton = card.querySelector<HTMLButtonElement>('[data-ui-action="delete-card"]');
    deleteButton?.addEventListener('click', () => {
        deleteCard(cardState.id);
    });

    const sourceSelect = card.querySelector<HTMLSelectElement>('[data-ui-action="source-select"]');
    sourceSelect?.addEventListener('change', (event) => {
        const selectEl = event.currentTarget as HTMLSelectElement;
        void changeCardSource(cardState.id, selectEl.value);
    });

    card.addEventListener('click', (event) => {
        const targetButton = (event.target as Element | null)?.closest<HTMLButtonElement>(
            '[data-ui-action="move-control"]',
        );
        if (!targetButton || !card.contains(targetButton)) return;
        event.stopPropagation();
        const moveTarget = targetButton.dataset.moveTarget;
        let target: MoveTarget = 'first';
        if (moveTarget && ['first', 'prev', 'next', 'last'].includes(moveTarget)) {
            target = moveTarget as MoveTarget;
        } else if (moveTarget != null) {
            const parsed = Number(moveTarget);
            if (Number.isFinite(parsed)) {
                target = parsed;
            }
        }
        void goToMoveCard(cardState.id, target);
    });

    const summary = card.querySelector<HTMLElement>('[data-ui-action="toggle-kifu"]');
    summary?.addEventListener('click', (event) => {
        if (event.defaultPrevented) return;
        const pathTargets = typeof event.composedPath === 'function' ? event.composedPath() : [];
        const interactiveTarget = pathTargets.some((target) => {
            if (!(target instanceof Element)) return false;
            if (target.matches('[data-ui-action="move-control"]')) return true;
            if (target.matches('button, a, input, select, textarea')) return true;
            return false;
        });
        if (interactiveTarget) {
            return;
        }
        void toggleKifuCard(cardState.id);
    });

    // Board adapter mount (if provided)
    if (typeof mountBoardAdapter === 'function') {
        mountBoardAdapter(cardState.id, card);
    }

    // Populate source dropdown (async to allow DOM paint)
    setTimeout(() => populateSourceDropdown(cardState.id), 100);

    return card;
}

export function renderAddCardTile(onAddCard: () => void, atCapacity: boolean): void {
    const grid = document.getElementById('workersGrid');
    if (!grid) return;
    const existing = document.getElementById('add-card-tile');
    if (existing) existing.remove();
    const tile = document.createElement('div');
    tile.className = 'add-card-tile';
    tile.id = 'add-card-tile';
    tile.addEventListener('click', () => {
        if (atCapacity) return;
        onAddCard();
    });
    tile.innerHTML = '<div class="add-card-plus">＋</div>';
    tile.classList.toggle('add-card-tile--disabled', atCapacity);
    if (atCapacity) {
        tile.setAttribute('aria-disabled', 'true');
    } else {
        tile.removeAttribute('aria-disabled');
    }
    grid.appendChild(tile);
}
