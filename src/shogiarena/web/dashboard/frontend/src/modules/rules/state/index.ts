import type { JsonObject } from '@/types/shared';

export interface RulesState {
    initialized: boolean;
    active: boolean;
    unsubscribe: (() => void) | null;
    interactionBound: boolean;
    gridClickHandler: ((event: MouseEvent) => void) | null;
    lastSummary: JsonObject | null;
    lastRenderSignature: string | null;
}

export function createInitialState(): RulesState {
    return {
        initialized: false,
        active: false,
        unsubscribe: null,
        interactionBound: false,
        gridClickHandler: null,
        lastSummary: null,
        lastRenderSignature: null,
    };
}
