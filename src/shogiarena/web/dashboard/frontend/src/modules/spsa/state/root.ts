import type { SpsaDashboardState } from '@/modules/spsa/types';

import { state } from './store';

export function getState(): SpsaDashboardState {
    return state;
}

export function bindRoot(element: HTMLElement | null): void {
    state.root = element;
}

export function setActive(active: boolean): void {
    state.active = Boolean(active);
}

export function setError(message: string | null): void {
    state.errorMessage = message;
    state.lastErrorAt = message ? Date.now() : null;
}

export function clearError(): void {
    setError(null);
}
