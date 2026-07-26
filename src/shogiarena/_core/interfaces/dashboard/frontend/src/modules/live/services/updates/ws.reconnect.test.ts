import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { LiveCardState, LiveCardsApi, LiveTimeApi } from '@/modules/live/types';
import type { LiveUpdatesContext } from '@/modules/live/types/updates';
import { installDashboardCore } from '@/modules/shared/services/core';
import { createWsSetup } from './ws';
import type { LiveUpdateHandlers } from './handlers';

type SubscribeMessage = {
    type: 'subscribe';
    topics: string[];
};

class FakeWorker {
    addEventListener(): void {}
    removeEventListener(): void {}
    postMessage(): void {}
    terminate(): void {}
}

class FakeWebSocket {
    static readonly CONNECTING = 0;
    static readonly OPEN = 1;
    static readonly CLOSED = 3;
    static readonly instances: FakeWebSocket[] = [];

    readonly sent: string[] = [];
    readyState = FakeWebSocket.CONNECTING;
    onopen: ((event: Event) => void) | null = null;
    onerror: ((event: Event) => void) | null = null;
    onclose: ((event: CloseEvent) => void) | null = null;
    onmessage: ((event: MessageEvent) => void) | null = null;

    constructor(readonly url: string | URL) {
        FakeWebSocket.instances.push(this);
    }

    send(payload: string): void {
        this.sent.push(payload);
    }

    close(): void {
        this.readyState = FakeWebSocket.CLOSED;
    }

    open(): void {
        this.readyState = FakeWebSocket.OPEN;
        this.onopen?.(new Event('open'));
    }

    serverClose(): void {
        this.readyState = FakeWebSocket.CLOSED;
        this.onclose?.(new CloseEvent('close'));
    }

    message(payload: unknown): void {
        this.onmessage?.(new MessageEvent('message', { data: JSON.stringify(payload) }));
    }
}

function isSubscribeMessage(value: unknown): value is SubscribeMessage {
    return (
        typeof value === 'object' &&
        value !== null &&
        'type' in value &&
        value.type === 'subscribe' &&
        'topics' in value &&
        Array.isArray(value.topics)
    );
}

function subscriptions(socket: FakeWebSocket): SubscribeMessage[] {
    const messages: SubscribeMessage[] = [];
    for (const payload of socket.sent) {
        const parsed: unknown = JSON.parse(payload);
        if (isSubscribeMessage(parsed)) {
            messages.push(parsed);
        }
    }
    return messages;
}

function latestSubscription(socket: FakeWebSocket): SubscribeMessage {
    const messages = subscriptions(socket);
    const latest = messages.at(-1);
    if (!latest) {
        throw new Error('Expected a subscribe message');
    }
    return latest;
}

function requireSocket(index: number): FakeWebSocket {
    const socket = FakeWebSocket.instances[index];
    if (!socket) {
        throw new Error(`Expected WebSocket instance ${index}`);
    }
    return socket;
}

function createCardsApi(): LiveCardsApi {
    return {
        initializeCards: vi.fn(),
        updateWorkerOptionLabels: vi.fn(),
        computeAutoViewPly: vi.fn(() => 0),
        syncWorkerViewFromCard: vi.fn(),
        focusGameOnLiveTab: vi.fn(async () => {}),
        populateSourceDropdown: vi.fn(async () => {}),
        handleSummaryEvent: vi.fn(),
    };
}

function createTimeApi(): LiveTimeApi {
    return {
        normalizeSFEN: (sfen) => sfen ?? '',
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

function createHandlers(): LiveUpdateHandlers {
    return {
        emitEvent: vi.fn(),
        applyClockSnapshot: vi.fn(),
        applyClockIncrement: vi.fn(),
        onWorkerUpdate: vi.fn(),
        onSummaryUpdate: vi.fn(),
    };
}

let activeSetup: ReturnType<typeof createWsSetup> | null = null;

beforeEach(() => {
    vi.useFakeTimers();
    FakeWebSocket.instances.length = 0;
    vi.stubGlobal('Worker', FakeWorker);
    vi.stubGlobal('WebSocket', FakeWebSocket);
});

afterEach(() => {
    activeSetup?.stop();
    activeSetup = null;
    vi.useRealTimers();
    vi.unstubAllGlobals();
});

describe('createWsSetup raw panel reconnect lifecycle', () => {
    it('rebuilds only explicit raw topics after a system disconnect without duplicate refcounts', () => {
        const openCard: LiveCardState = {
            id: 'open-card',
            source: 'db-game:game-open',
            autoSync: false,
            engineLogGameKey: 'game-open',
            engineLogPreference: { black: true, white: false },
        };
        const closedCard: LiveCardState = {
            id: 'closed-card',
            source: 'db-game:game-closed',
            autoSync: false,
            engineLogGameKey: 'game-closed',
            engineLogPreference: { black: false, white: false },
        };
        const core = installDashboardCore(window);
        core.state.cards = [openCard, closedCard];
        core.state.runtimeMode = 'tournament';
        const cards = createCardsApi();
        const timeApi = createTimeApi();
        const context: LiveUpdatesContext = {
            owner: window,
            core,
            live: { cards, time: timeApi },
            cards,
            timeApi,
            normalizeSFEN: timeApi.normalizeSFEN,
            state: core.state,
            events: core.events,
            warnSoftFailure: core.warnSoftFailure,
            notifyDashboardServerStopped: vi.fn(),
            getApiBase: () => 'http://localhost:8000',
        };
        const setup = createWsSetup(context, createHandlers());
        activeSetup = setup;

        setup.start();
        const first = requireSocket(0);
        first.open();
        expect(latestSubscription(first).topics).toContain('live.engine.game-open.black.io.diff');
        expect(latestSubscription(first).topics).not.toContain('live.engine.game-closed.black.io.diff');

        first.serverClose();
        vi.advanceTimersByTime(1000);
        const reconnected = requireSocket(1);
        reconnected.open();
        const reconnectedTopics = latestSubscription(reconnected).topics;
        expect(reconnectedTopics.filter((topic) => topic === 'live.engine.game-open.black.io.diff')).toHaveLength(1);
        expect(reconnectedTopics.some((topic) => topic.includes('game-closed'))).toBe(false);

        openCard.engineLogPreference = { black: false, white: false };
        openCard.engineLogGameKey = null;
        core.events.emit('live:engine-log-toggle', {
            gid: 'game-open',
            role: 'black',
            open: false,
        });
        expect(latestSubscription(reconnected).topics).not.toContain('live.engine.game-open.black.io.diff');

        reconnected.serverClose();
        vi.advanceTimersByTime(1000);
        const closedPanelReconnect = requireSocket(2);
        closedPanelReconnect.open();
        expect(latestSubscription(closedPanelReconnect).topics.some((topic) => topic.startsWith('live.engine.'))).toBe(
            false,
        );

        core.state.cards = [];
    });

    it('does not reconnect after receiving an intentional terminal summary', () => {
        const core = installDashboardCore(window);
        core.state.cards = [];
        core.state.runtimeMode = 'tournament';
        const cards = createCardsApi();
        const timeApi = createTimeApi();
        const notifyDashboardServerStopped = vi.fn();
        const handlers = createHandlers();
        const context: LiveUpdatesContext = {
            owner: window,
            core,
            live: { cards, time: timeApi },
            cards,
            timeApi,
            normalizeSFEN: timeApi.normalizeSFEN,
            state: core.state,
            events: core.events,
            warnSoftFailure: core.warnSoftFailure,
            notifyDashboardServerStopped,
            getApiBase: () => 'http://localhost:8000',
        };
        const setup = createWsSetup(context, handlers);
        activeSetup = setup;

        setup.start();
        const socket = requireSocket(0);
        socket.open();
        socket.message({
            topic: 'live.summary.snapshot.tournament',
            seq: 1,
            ts: Date.now(),
            payload: {
                games: { completed: 8, total: 8 },
                games_completed: 8,
                games_scheduled: 8,
                tournament_ended: true,
                live_view: null,
            },
        });
        socket.onerror?.(new Event('error'));
        socket.serverClose();
        vi.advanceTimersByTime(1000);

        expect(handlers.onSummaryUpdate).toHaveBeenCalledWith(
            expect.objectContaining({ games_completed: 8, tournament_ended: true }),
        );
        expect(FakeWebSocket.instances).toHaveLength(1);
        expect(notifyDashboardServerStopped).not.toHaveBeenCalled();
    });
});
