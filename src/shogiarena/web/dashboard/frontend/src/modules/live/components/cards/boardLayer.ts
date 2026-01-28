import type { LiveBoardAdapter } from '@/modules/live/types';
import type { DashboardCoreState } from '@/types/dashboard';
import { createBoardAdapterManager, type BoardAdapterManager, type BoardAdapterDeps } from './adapters';
import { deleteBoardAdapter, listBoardAdapterIds, setBoardAdapter } from './state';

type ShogiBoardAdapterCtor = new () => LiveBoardAdapter;

export interface BoardLayerOwner {
    ShogiBoardAdapter?: ShogiBoardAdapterCtor | null;
    ResizeObserver?: typeof ResizeObserver;
}

export interface BoardAdapterLayer {
    manager: BoardAdapterManager;
    clearBoardAdapters: () => void;
    disposeBoardAdapter: (cardId: string | number) => void;
}

function createManagerDeps(
    owner: BoardLayerOwner,
    state: DashboardCoreState,
    warnSoftFailure: (context: string, error: unknown) => void,
): BoardAdapterDeps {
    const allowMissingAdapter = (typeof process !== 'undefined' && process.env?.NODE_ENV === 'test') || false;

    return {
        ownerAdapterCtor: owner.ShogiBoardAdapter ?? null,
        resizeObserverCtor: owner.ResizeObserver,
        deleteBoardAdapter,
        setBoardAdapter,
        warnSoftFailure,
        state,
        allowMissingAdapter,
    };
}

export function createBoardAdapterLayer(
    owner: BoardLayerOwner,
    state: DashboardCoreState,
    warnSoftFailure: (context: string, error: unknown) => void,
): BoardAdapterLayer {
    const manager = createBoardAdapterManager(createManagerDeps(owner, state, warnSoftFailure));

    const clearBoardAdapters = (): void => {
        for (const cardId of listBoardAdapterIds(state)) {
            manager.teardown(cardId);
        }
    };

    const disposeBoardAdapter = (cardId: string | number): void => {
        manager.teardown(cardId);
    };

    return {
        manager,
        clearBoardAdapters,
        disposeBoardAdapter,
    };
}
