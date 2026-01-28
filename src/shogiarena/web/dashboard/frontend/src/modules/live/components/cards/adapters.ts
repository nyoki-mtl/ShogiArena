import type { LiveBoardAdapter } from '@/modules/live/types';
import type { DashboardCoreState } from '@/types/dashboard';
import { createOffscreenBoardAdapterCtor, supportsOffscreenCanvas } from './worker/offscreenBoardAdapter';
import { recordLiveDiagnosticsMetric } from '@/modules/live/utils/liveNamespace/metrics';

type ShogiBoardAdapterCtor = new () => LiveBoardAdapter;

export interface BoardAdapterDeps {
    ownerAdapterCtor?: ShogiBoardAdapterCtor | null;
    resizeObserverCtor?: typeof ResizeObserver;
    deleteBoardAdapter: (state: DashboardCoreState, cardId: string | number) => LiveBoardAdapter | undefined;
    setBoardAdapter: (state: DashboardCoreState, cardId: string | number, adapter: LiveBoardAdapter) => void;
    warnSoftFailure: (context: string, error: unknown) => void;
    state: DashboardCoreState;
    allowMissingAdapter?: boolean;
}

export interface BoardAdapterManager {
    mount: (cardId: string | number, card: HTMLElement) => void;
    teardown: (cardId: string | number) => void;
    teardownAll: () => void;
}

export function createBoardAdapterManager(deps: BoardAdapterDeps): BoardAdapterManager {
    const {
        ownerAdapterCtor,
        resizeObserverCtor,
        deleteBoardAdapter,
        setBoardAdapter,
        warnSoftFailure,
        state,
        allowMissingAdapter = false,
    } = deps;
    const resizeObservers = new Map<string | number, ResizeObserver>();

    const offscreenEnabled = Boolean(ownerAdapterCtor && supportsOffscreenCanvas());
    const adapterCtor: ShogiBoardAdapterCtor | null =
        offscreenEnabled && ownerAdapterCtor
            ? createOffscreenBoardAdapterCtor(ownerAdapterCtor)
            : (ownerAdapterCtor ?? null);
    let offscreenMetricEmitted = false;

    const mount = (cardId: string | number, card: HTMLElement): void => {
        if (!adapterCtor) {
            if (allowMissingAdapter) {
                if (typeof console !== 'undefined') {
                    console.warn?.('ShogiBoardAdapter constructor is not provided; skipping board mount');
                }
                return;
            }
            throw new Error('ShogiBoardAdapter constructor is not provided; cannot render live board');
        }
        setTimeout(() => {
            try {
                const boardContainer = card.querySelector<HTMLElement>('.board-container');
                if (!boardContainer) {
                    throw new Error('Board container not found for live card');
                }
                const AdapterCtor = adapterCtor;
                const adapter = new AdapterCtor();
                if (typeof adapter.mount !== 'function') {
                    throw new Error('ShogiBoardAdapter.mount is not a function');
                }
                if (offscreenEnabled && !offscreenMetricEmitted) {
                    offscreenMetricEmitted = true;
                    recordLiveDiagnosticsMetric('live.board.offscreen', { enabled: 1 });
                }
                const previous = deleteBoardAdapter(state, cardId);
                if (previous && typeof previous.destroy === 'function') {
                    try {
                        previous.destroy();
                    } catch (destroyError) {
                        warnSoftFailure('Failed to destroy previous board adapter before remount', destroyError);
                    }
                }
                adapter.mount(boardContainer);
                adapter.setPositionFromSFEN?.('startpos');
                adapter.setMoves?.([]);
                adapter.goTo?.(0);
                requestAnimationFrame(() => adapter?.resize?.());
                const ResizeObserverCtor = resizeObserverCtor ?? window.ResizeObserver;
                if (typeof ResizeObserverCtor === 'function') {
                    const ro = new ResizeObserverCtor(() => {
                        adapter?.resize?.();
                    });
                    ro.observe(boardContainer);
                    resizeObservers.set(cardId, ro);
                }
                setBoardAdapter(state, cardId, adapter);
            } catch (error) {
                warnSoftFailure('Failed to initialize ShogiBoardAdapter for live card', error);
            }
        }, 0);
    };

    const teardown = (cardId: string | number): void => {
        const observer = resizeObservers.get(cardId);
        if (observer) {
            observer.disconnect();
            resizeObservers.delete(cardId);
        }
        const adapter = deleteBoardAdapter(state, cardId);
        if (adapter && typeof adapter.destroy === 'function') {
            try {
                adapter.destroy();
            } catch (error) {
                warnSoftFailure(`Failed to destroy board adapter for card ${cardId}`, error);
            }
        }
    };

    const teardownAll = (): void => {
        for (const key of Array.from(resizeObservers.keys())) {
            teardown(key);
        }
    };

    return { mount, teardown, teardownAll };
}
