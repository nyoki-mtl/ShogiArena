import { afterEach, describe, expect, it, vi } from 'vitest';
import type { LiveCardState, LiveTimeApi } from '@/modules/live/types';
import { registerLiveApi, resetLiveNamespace } from '@/modules/live/utils/liveNamespace';
import type {
    DashboardCore,
    DashboardCoreState,
    DashboardCoreStateKey,
    DashboardEventHandler,
    DashboardStateUpdater,
} from '@/types/dashboard';
import { type CardsWindow, createLiveCardsApi } from '../cards-api';

type EmittedEvent = {
    name: string;
    payload: unknown;
};

function createCore(cards: LiveCardState[]): { core: DashboardCore; emitted: EmittedEvent[] } {
    const emitted: EmittedEvent[] = [];
    const state: DashboardCoreState = {
        cards,
        workers: new Map(),
        boardAdapters: new Map(),
        workerSnapshots: new Map(),
        numWorkers: cards.length,
        maxLiveBoards: 8,
        nextCardId: cards.length + 1,
        gameDataCache: new Map(),
        gamesSignature: '',
        gamesSignatureByCard: new Map(),
        gameMetadata: new Map(),
        offlineNotified: false,
        liveStreamingEnabled: true,
        engineFinalRatings: new Map(),
        highlightEngine: null,
        prevRatings: new Map(),
        ratingDeltas: new Map(),
        ratingDeltaTimerId: null,
        pendingDeltaHint: new Map(),
        pentaCache: new Map(),
        gamesList: null,
        popoverTimer: null,
        popoverPinned: false,
        popoverInvokerEl: null,
        expanded: null,
        standingsTCMap: new Map(),
        runtimeMode: 'tournament',
    };

    function on<T = unknown>(_eventName: string, _handler: DashboardEventHandler<T>): () => void {
        return () => {};
    }

    function off<T = unknown>(_eventName: string, _handler: DashboardEventHandler<T>): void {}

    function emit<T = unknown>(eventName: string, payload?: T): void {
        emitted.push({ name: eventName, payload });
    }

    function mutateState<K extends DashboardCoreStateKey>(
        key: K,
        _updater: DashboardStateUpdater<K>,
    ): DashboardCoreState[K] {
        return state[key];
    }

    function getStateSlice<K extends DashboardCoreStateKey>(_keys: readonly K[]): Pick<DashboardCoreState, K> {
        throw new Error('getStateSlice is not used in this fixture');
    }

    const events: DashboardCore['events'] = {
        on,
        off,
        emit,
    };
    const core: DashboardCore = {
        state,
        events,
        mutateState,
        getStateSlice,
        registerFetcher: vi.fn(),
        getFetcher: vi.fn(() => null),
        warnSoftFailure: vi.fn((_context: string, error: unknown): never => {
            throw error instanceof Error ? error : new Error(String(error));
        }),
        showApiError: vi.fn(),
        showSpsaDetailStreamError: vi.fn(),
        showNotice: vi.fn(),
        notifyDashboardServerStopped: vi.fn(),
        setDisplay: vi.fn(),
        getApiBase: vi.fn(() => ''),
        updateElement: vi.fn(),
    };

    return { core, emitted };
}

function createTimeApi(): LiveTimeApi {
    return {
        normalizeSFEN: (sfen: string | null | undefined) => sfen ?? '',
        getStartingPlyNumber: () => 1,
        formatRemain: () => '',
        formatInc: () => '',
        formatCountUp: () => '',
        formatByoyomi: () => '',
        parseTimeControlSpec: () => ({
            mode: 'time',
            initial: 0,
            byoyomi: 0,
            increment: 0,
            fixedMs: 0,
            depth: null,
            nodes: null,
            marginMs: null,
            allowTimeout: false,
            maxWaitMs: null,
        }),
        formatTimeControlShort: () => '',
        computeClocksAtPly: () => ({
            blackRemainMs: 0,
            whiteRemainMs: 0,
            incSide: null,
            incMs: 0,
            byoyomiBlack: 0,
            byoyomiWhite: 0,
        }),
        updateStaticClocksForCard: vi.fn(),
    };
}

afterEach(() => {
    const owner: CardsWindow = window;
    resetLiveNamespace(owner);
    delete owner.DashboardCore;
    document.body.innerHTML = '';
});

describe('Live raw panel tab lifecycle', () => {
    it('unsubscribes every open role on exit and does not reopen it on activation', () => {
        const cards = [
            {
                id: 'card-a',
                source: 'worker-latest:0',
                autoSync: true,
                engineLogPreference: { black: true, white: true },
                engineLogGameKey: 'game-a',
            },
            {
                id: 'card-b',
                source: 'worker-latest:1',
                autoSync: true,
                engineLogPreference: { black: false, white: true },
                engineLogGameKey: 'game-b',
            },
        ] satisfies LiveCardState[];
        const { core, emitted } = createCore(cards);
        const owner: CardsWindow = window;
        owner.DashboardCore = core;
        registerLiveApi(owner, 'time', createTimeApi(), { provider: 'test/time' });
        const api = createLiveCardsApi(owner);

        api.onTabDeactivate?.();
        api.onTabActivate?.();
        api.onTabDeactivate?.();

        const rawToggles = emitted.filter((event) => event.name === 'live:engine-log-toggle');
        expect(rawToggles).toEqual([
            { name: 'live:engine-log-toggle', payload: { gid: 'game-a', role: 'black', open: false } },
            { name: 'live:engine-log-toggle', payload: { gid: 'game-a', role: 'white', open: false } },
            { name: 'live:engine-log-toggle', payload: { gid: 'game-b', role: 'white', open: false } },
        ]);
        expect(rawToggles.some((event) => (event.payload as { open?: boolean }).open === true)).toBe(false);
        expect(cards.map((card) => card.engineLogPreference)).toEqual([
            { black: false, white: false },
            { black: false, white: false },
        ]);
        expect(cards.map((card) => card.engineLogGameKey)).toEqual([null, null]);

        api.teardown?.();
    });
});
