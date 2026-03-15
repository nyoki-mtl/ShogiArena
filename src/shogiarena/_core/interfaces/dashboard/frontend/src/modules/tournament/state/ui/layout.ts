import type { LayoutUiState } from '@/modules/tournament/types';

export function createLayoutUiState(): LayoutUiState {
    return {
        popoverTimer: null,
        popoverPinned: false,
        popoverInvokerEl: null,
        expanded: null,
        openingBoardAdapter: null,
        openingBoardElement: null,
        openingBoardInfo: null,
        openingSelectedRow: null,
        openingDetailRow: null,
        selectedOpeningKey: null,
        openingDetailMode: null,
        openingRowLookup: null,
        pendingOpeningFocus: null,
    };
}
