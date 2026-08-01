import type { SpsaRefreshOptions } from '@/modules/spsa/types';

import { state } from './store';

export function setEventSource(
    instance: EventSource | null,
    status: (typeof state)['connection']['eventSourceStatus'],
): void {
    state.connection.eventSource = instance;
    state.connection.eventSourceStatus = status;
}

export function registerAbortController(controller: AbortController): void {
    state.connection.abortControllers.add(controller);
}

export function unregisterAbortController(controller: AbortController): void {
    state.connection.abortControllers.delete(controller);
}

export function cancelOngoingRequests(): void {
    if (state.connection.abortControllers.size === 0) {
        return;
    }
    for (const controller of state.connection.abortControllers) {
        controller.abort();
    }
    state.connection.abortControllers.clear();
    state.connection.queuedRefresh = null;
}

export function isRefreshInFlight(): boolean {
    return state.connection.refreshInFlight;
}

export function getRefreshPromise(): Promise<void> | null {
    return state.connection.refreshPromise;
}

export function setRefreshPromise(promise: Promise<void> | null): void {
    state.connection.refreshPromise = promise ?? null;
    state.connection.refreshInFlight = promise !== null;
}

export function queueRefreshRequest(options?: SpsaRefreshOptions): void {
    const next: SpsaRefreshOptions = options ? { ...options } : {};
    const current = state.connection.queuedRefresh;
    if (!current) {
        state.connection.queuedRefresh = next;
        return;
    }
    state.connection.queuedRefresh = {
        force: Boolean(current.force || next.force),
    };
}

export function consumeQueuedRefreshRequest(): SpsaRefreshOptions | null {
    const next = state.connection.queuedRefresh;
    state.connection.queuedRefresh = null;
    return next;
}
