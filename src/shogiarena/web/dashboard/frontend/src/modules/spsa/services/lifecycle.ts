import { isAbortError } from './errors';
import { type getState, registerVisibilityHandler } from '../state';
import type { DashboardCore } from '@/types/dashboard';
import type { SpsaApiCallbacks, SpsaApiOptions } from './types';
import type { SpsaRefreshOptions } from '@/modules/spsa/types';

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
    refreshAll: (options?: SpsaRefreshOptions) => Promise<void>;
    setActive: (active: boolean) => void;
    setVisibilityPaused: (paused: boolean) => void;
    openStreams: {
        summary: () => void;
        updates: () => void;
        variantGames: () => void;
        ltcGames: () => void;
        correlation: () => void;
        convergence: () => void;
        ltcResults: () => void;
        ltcProgress: () => void;
    };
    openTabStreams?: () => void;
    closeStreams: {
        summary: () => void;
        updates: () => void;
        variantGames: () => void;
        ltcGames: () => void;
        correlation: () => void;
        convergence: () => void;
        ltcResults: () => void;
        ltcProgress: () => void;
    };
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
        refreshAll,
        setActive,
        setVisibilityPaused,
        openStreams,
        closeStreams,
        cancelOngoingRequests,
        openTabStreams,
    } = deps;

    const reportUiError = (title: string, detail: unknown): void => {
        try {
            core.showApiError?.(title, detail);
        } catch (uiError) {
            console.error('[SPSA] Failed to emit dashboard error notice', uiError, { title, detail });
        }
        callbacks.onError?.(title);
    };

    const runVisibilityRefresh = () => {
        if (!state.active) {
            return;
        }
        void refreshAll({ force: false }).catch((error) => {
            if (isAbortError(error)) {
                return;
            }
            reportUiError('Failed to refresh SPSA panel', error);
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
        beginBootstrapSequence().catch((error) => {
            if (isAbortError(error)) {
                return;
            }
            reportUiError('Failed to bootstrap SPSA dashboard', error);
        });
    };

    const pause = () => {
        if (!state.active) {
            return;
        }
        setActive(false);
        closeStreams.summary();
        closeStreams.variantGames();
        closeStreams.ltcGames();
        closeStreams.correlation();
        closeStreams.convergence();
        closeStreams.ltcResults();
        closeStreams.ltcProgress();
        resetHydrationState();
    };

    const disconnect = (reason?: string) => {
        if (reason) {
            reportUiError('SPSA disconnect', reason);
        }
        setActive(false);
        closeStreams.updates();
        cancelOngoingRequests();
        closeStreams.variantGames();
        closeStreams.ltcGames();
        closeStreams.correlation();
        closeStreams.convergence();
        closeStreams.summary();
        closeStreams.ltcResults();
        closeStreams.ltcProgress();
        resetHydrationState();
    };

    const setupVisibilityRefresh = (autoVisibilityRefresh: boolean) => {
        if (!autoVisibilityRefresh) {
            return;
        }
        registerVisibilityHandler(document as unknown as Document | undefined, (visible) => {
            setVisibilityPaused(!visible);
            if (!visible || !state.active) {
                return;
            }
            openStreams.summary();
            openStreams.updates();
            openTabStreams?.();
            runVisibilityRefresh();
        });
    };

    return { connect, pause, disconnect, setupVisibilityRefresh };
}
