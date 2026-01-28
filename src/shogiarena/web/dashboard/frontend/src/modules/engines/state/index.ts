import type { EngineDetailContext, EngineDetailMode } from '../types/internal';

export interface EnginesLocalState {
    initialized: boolean;
    active: boolean;
    expanded: EngineDetailContext | null;
    summaryUnsubscribe: (() => void) | null;
}

export function createEnginesLocalState(): EnginesLocalState {
    return {
        initialized: false,
        active: false,
        expanded: null,
        summaryUnsubscribe: null,
    };
}

export function setActive(state: EnginesLocalState, active: boolean): void {
    state.active = active;
}

export function isInitialized(state: EnginesLocalState): boolean {
    return state.initialized;
}

export function markInitialized(state: EnginesLocalState): void {
    state.initialized = true;
}

export function setSummaryUnsubscribe(state: EnginesLocalState, unsubscribe: (() => void) | null): void {
    state.summaryUnsubscribe = unsubscribe;
}

export function clearSummarySubscription(state: EnginesLocalState): void {
    if (state.summaryUnsubscribe) {
        state.summaryUnsubscribe();
    }
    state.summaryUnsubscribe = null;
}

export function resetExpanded(state: EnginesLocalState): void {
    state.expanded = null;
}

export function toggleExpanded(
    state: EnginesLocalState,
    engine: string | null,
    mode: EngineDetailMode | null,
    context: Partial<EngineDetailContext> = {},
): void {
    if (!engine || !mode) {
        state.expanded = null;
        return;
    }

    const current = state.expanded;
    if (current && current.engine === engine && current.mode === mode) {
        state.expanded = null;
        return;
    }

    state.expanded = { engine, mode, ...context } as EngineDetailContext;
}
