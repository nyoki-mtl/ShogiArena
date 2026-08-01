import { isAbortError } from './errors';
import { type getState, registerVisibilityHandler } from '../state';
import type { DashboardCore } from '@/types/dashboard';
import type { SpsaApiCallbacks, SpsaApiOptions } from './types';

// SPSA専用のライフサイクルコントローラ。接続/切断とUIエラー通知を集約する。
export type LifecycleDeps = {
    core: DashboardCore;
    document: SpsaApiOptions['document'];
    callbacks: SpsaApiCallbacks;
    state: ReturnType<typeof getState>;
    beginBootstrapSequence: () => Promise<void>;
    resetHydrationState: () => void;
    resetUpdates: () => void;
    recomputeTrend: () => void;
    setActive: (active: boolean) => void;
    setVisibilityPaused: (paused: boolean) => void;
    hideRevisionStream: () => void;
    markRevisionOffline: () => void;
    closeRevisionStream: () => void;
    cancelOngoingRequests: () => void;
};

export type LifecycleApi = {
    connect: () => void;
    pause: () => void;
    disconnect: (reason?: string) => void;
    setupVisibilityRefresh: (autoVisibilityRefresh: boolean) => void;
};

export function createLifecycle(deps: LifecycleDeps): LifecycleApi {
    const {
        core,
        document,
        callbacks,
        state,
        beginBootstrapSequence,
        resetHydrationState,
        resetUpdates,
        recomputeTrend,
        setActive,
        setVisibilityPaused,
        hideRevisionStream,
        markRevisionOffline,
        closeRevisionStream,
        cancelOngoingRequests,
    } = deps;

    const reportUiError = (title: string, detail: unknown): void => {
        try {
            core.showApiError?.(title, detail);
        } catch (uiError) {
            console.error('[SPSA] Failed to emit dashboard error notice', uiError, { title, detail });
        }
        callbacks.onError?.(title);
    };

    const runBootstrap = (): void => {
        void beginBootstrapSequence().catch((error) => {
            if (isAbortError(error)) {
                return;
            }
            reportUiError('Failed to bootstrap SPSA dashboard', error);
        });
    };

    const connect = () => {
        if (state.active) {
            return;
        }
        setActive(true);
        resetHydrationState();
        resetUpdates();
        recomputeTrend();
        runBootstrap();
    };

    const pause = () => {
        if (!state.active) {
            return;
        }
        setActive(false);
        closeRevisionStream();
        cancelOngoingRequests();
        resetHydrationState();
    };

    const disconnect = (reason?: string) => {
        if (reason) {
            reportUiError('SPSA disconnect', reason);
        }
        setActive(false);
        closeRevisionStream();
        cancelOngoingRequests();
        resetHydrationState();
    };

    const setupVisibilityRefresh = (autoVisibilityRefresh: boolean) => {
        if (!autoVisibilityRefresh) {
            return;
        }
        registerVisibilityHandler(document, (visible) => {
            setVisibilityPaused(!visible);
            if (!visible) {
                hideRevisionStream();
                cancelOngoingRequests();
                resetHydrationState();
                return;
            }
            if (!state.active) {
                return;
            }
            runBootstrap();
        });
        window.addEventListener('offline', () => {
            if (!state.active) {
                return;
            }
            markRevisionOffline();
            cancelOngoingRequests();
            resetHydrationState();
        });
        window.addEventListener('online', () => {
            if (!state.active || document.hidden) {
                return;
            }
            runBootstrap();
        });
    };

    return { connect, pause, disconnect, setupVisibilityRefresh };
}
