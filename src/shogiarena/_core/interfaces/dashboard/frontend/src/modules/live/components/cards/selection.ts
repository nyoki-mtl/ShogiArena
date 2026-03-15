import type { LiveCardId } from '@/modules/live/types';
import type { DashboardCoreState } from '@/types/dashboard';

interface SelectionDeps {
    state: DashboardCoreState;
    warnSoftFailure: (message: string, error: unknown) => void;
}

export function createSelectionController(deps: SelectionDeps) {
    const { state, warnSoftFailure } = deps;

    function setSelectedCard(cardId: LiveCardId | null): void {
        if (state.selectedCardId === cardId) {
            return;
        }
        const prevId = state.selectedCardId;
        if (prevId != null && prevId !== cardId) {
            const prevEl = document.getElementById(`card-${prevId}`);
            if (prevEl) {
                prevEl.classList.remove('worker-card--selected');
            }
        }
        state.selectedCardId = cardId;
        if (cardId == null) {
            return;
        }
        const el = document.getElementById(`card-${cardId}`);
        if (!el) {
            warnSoftFailure(`Card element card-${cardId} not found; selection skipped`, null);
            return;
        }
        el.classList.add('worker-card--selected');
    }

    function isInteractiveElement(node: Element | null | undefined): boolean {
        if (!node) return false;
        return Boolean(
            node.closest(
                'button, select, input, textarea, a, label, summary, [role="button"], [data-skip-card-select="true"]',
            ),
        );
    }

    return {
        setSelectedCard,
        isInteractiveElement,
    };
}
