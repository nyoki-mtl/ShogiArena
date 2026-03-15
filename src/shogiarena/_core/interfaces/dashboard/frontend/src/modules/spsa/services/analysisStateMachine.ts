/**
 * Analysis タブ用のステートマシン。
 *
 * 状態遷移: idle → loading → warming → ready | error
 * サーバーから status=warming/error が返った場合はサーバー駆動で遷移。
 */

import type { AnalysisStatus } from '@/modules/spsa/types';

export type AnalysisState = 'idle' | 'loading' | 'warming' | 'ready' | 'error';

export type AnalysisStateTransition = {
    from: AnalysisState;
    to: AnalysisState;
    reason?: string;
};

export type AnalysisStateMachineCallbacks = {
    onStateChange: (transition: AnalysisStateTransition) => void;
    onAutoRetry: () => void;
};

const LOADING_TO_WARMING_MS = 5_000;
const WARMING_TO_ERROR_MS = 30_000; // total from loading
const AUTO_RETRY_DELAY_MS = 10_000;

export type AnalysisStateMachine = {
    getState: () => AnalysisState;
    startLoading: () => void;
    handleResponse: (status: AnalysisStatus) => void;
    reset: () => void;
    destroy: () => void;
};

export function createAnalysisStateMachine(callbacks: AnalysisStateMachineCallbacks): AnalysisStateMachine {
    let state: AnalysisState = 'idle';
    let warmingTimer: number | null = null;
    let errorTimer: number | null = null;
    let retryTimer: number | null = null;

    const clearTimers = (): void => {
        if (warmingTimer !== null) {
            window.clearTimeout(warmingTimer);
            warmingTimer = null;
        }
        if (errorTimer !== null) {
            window.clearTimeout(errorTimer);
            errorTimer = null;
        }
        if (retryTimer !== null) {
            window.clearTimeout(retryTimer);
            retryTimer = null;
        }
    };

    const transition = (to: AnalysisState, reason?: string): void => {
        if (state === to) return;
        const from = state;
        state = to;
        callbacks.onStateChange({ from, to, reason });
    };

    const startLoading = (): void => {
        clearTimers();
        transition('loading');

        warmingTimer = window.setTimeout(() => {
            warmingTimer = null;
            if (state === 'loading') {
                transition('warming', 'timeout');
            }
        }, LOADING_TO_WARMING_MS);

        errorTimer = window.setTimeout(() => {
            errorTimer = null;
            if (state === 'loading' || state === 'warming') {
                transition('error', 'timeout');
                scheduleAutoRetry();
            }
        }, WARMING_TO_ERROR_MS);
    };

    const scheduleAutoRetry = (): void => {
        if (retryTimer !== null) return;
        retryTimer = window.setTimeout(() => {
            retryTimer = null;
            callbacks.onAutoRetry();
        }, AUTO_RETRY_DELAY_MS);
    };

    const handleResponse = (status: AnalysisStatus): void => {
        clearTimers();
        switch (status) {
            case 'ready':
                transition('ready');
                break;
            case 'warming':
                transition('warming', 'server');
                // Still set the error timeout from warming
                errorTimer = window.setTimeout(() => {
                    errorTimer = null;
                    if (state === 'warming') {
                        transition('error', 'timeout');
                        scheduleAutoRetry();
                    }
                }, WARMING_TO_ERROR_MS - LOADING_TO_WARMING_MS);
                break;
            case 'error':
                transition('error', 'server');
                scheduleAutoRetry();
                break;
        }
    };

    const reset = (): void => {
        clearTimers();
        transition('idle');
    };

    const destroy = (): void => {
        clearTimers();
    };

    return {
        getState: () => state,
        startLoading,
        handleResponse,
        reset,
        destroy,
    };
}
