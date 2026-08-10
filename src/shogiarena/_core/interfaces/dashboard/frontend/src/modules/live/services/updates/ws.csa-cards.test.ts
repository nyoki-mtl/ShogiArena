import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { LiveCardState, LiveCardsApi, LiveTimeApi } from '@/modules/live/types';
import type { LiveUpdatesContext } from '@/modules/live/types/updates';
import { installDashboardCore } from '@/modules/shared/services/core';
import type { LiveUpdateHandlers } from './handlers';
import { createWsSetup } from './ws';

/**
 * Live View shows what is running.
 *
 * A CSA log directory accumulates runs for as long as it is used, so a board per
 * discovered run means the game actually being played is pushed off the screen
 * by the ones that finished hours ago. Every other profile fixes its worker set
 * before the page loads and never has this problem; this is the CSA profile
 * being brought to the same shape.
 *
 * A run remains alive between games. Its board stays visible as a waiting
 * surface until `bridge_stop` or stale-run classification completes the run.
 */

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

    message(payload: unknown): void {
        this.onmessage?.(new MessageEvent('message', { data: JSON.stringify(payload) }));
    }
}

/** Frozen so silence can be expressed as an offset the test controls. */
const NOW = 1_800_000_000_000;

interface CsaRunEntry {
    run_id: string;
    worker_idx: number;
    phase: string;
    stopped?: boolean;
    emits_liveness?: boolean;
    last_event_ts?: number;
    current_game_id?: string | null;
}

function summaryPayload(runs: readonly CsaRunEntry[]): Record<string, unknown> {
    return {
        is_summary_ready: true,
        summary_source: 'csa',
        mode: 'csa',
        tournament_type: 'csa',
        num_engines: 0,
        engines: [],
        games: { completed: 0, total: 0, cancelled: 0 },
        csa_runs: runs.map((run) => ({
            wins: 0,
            losses: 0,
            draws: 0,
            games: 0,
            alerts: 0,
            bridge_version: '0.2.0',
            phase_since_ts: NOW,
            emits_liveness: true,
            last_event_ts: NOW,
            stopped: false,
            current_game_id:
                run.current_game_id === undefined
                    ? run.phase === 'playing'
                        ? `${run.run_id}-game`
                        : null
                    : run.current_game_id,
            alert_entries: [],
            ...run,
        })),
        live_view: {
            version: 1,
            mode: 'csa',
            progress: {
                kind: 'games',
                unit_label: 'games',
                completed: 0,
                total: 0,
                cancelled: 0,
                is_final: false,
                state: 'normal',
                updated_at: '2026-08-07T00:00:00+00:00',
            },
        },
    };
}

let activeSetup: ReturnType<typeof createWsSetup> | null = null;

beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(NOW);
    FakeWebSocket.instances.length = 0;
    vi.stubGlobal('Worker', FakeWorker);
    vi.stubGlobal('WebSocket', FakeWebSocket);
    document.body.dataset.dashboardProfile = 'csa';
});

afterEach(() => {
    activeSetup?.stop();
    activeSetup = null;
    delete document.body.dataset.dashboardProfile;
    vi.useRealTimers();
    vi.unstubAllGlobals();
});

function harness() {
    const core = installDashboardCore(window);
    const cards: LiveCardState[] = [];
    core.state.cards = cards;
    core.state.runtimeMode = 'csa';
    core.state.numWorkers = 0;
    let nextId = 1;

    const cardsApi = {
        initializeCards: vi.fn(),
        updateWorkerOptionLabels: vi.fn(),
        computeAutoViewPly: vi.fn(() => 0),
        syncWorkerViewFromCard: vi.fn(),
        focusGameOnLiveTab: vi.fn(async () => {}),
        populateSourceDropdown: vi.fn(async () => {}),
        updateCardData: vi.fn(async () => {}),
        handleSummaryEvent: vi.fn(),
        createCardForSource: vi.fn((source: string) => {
            const card: LiveCardState = { id: nextId++, source, autoSync: true, viewPly: 0 };
            cards.push(card);
            return card;
        }),
        deleteCard: vi.fn((cardId: number) => {
            const index = cards.findIndex((card) => card.id === cardId);
            if (index >= 0) cards.splice(index, 1);
        }),
    } as unknown as LiveCardsApi;

    const timeApi = { normalizeSFEN: (sfen: string | null) => sfen ?? '' } as unknown as LiveTimeApi;
    const handlers: LiveUpdateHandlers = {
        emitEvent: vi.fn(),
        applyClockSnapshot: vi.fn(),
        applyClockIncrement: vi.fn(),
        onWorkerUpdate: vi.fn(),
        onSummaryUpdate: vi.fn(),
    };
    const context: LiveUpdatesContext = {
        owner: window,
        core,
        live: { cards: cardsApi, time: timeApi },
        cards: cardsApi,
        timeApi,
        normalizeSFEN: timeApi.normalizeSFEN,
        state: core.state,
        events: core.events,
        warnSoftFailure: core.warnSoftFailure,
        notifyDashboardServerStopped: vi.fn(),
        getApiBase: () => 'http://localhost:8000',
    };

    const setup = createWsSetup(context, handlers);
    activeSetup = setup;
    setup.start();
    const socket = FakeWebSocket.instances[0];
    if (!socket) throw new Error('expected a socket');
    socket.open();

    const publish = (runs: readonly CsaRunEntry[]): void => {
        socket.message({
            topic: 'live.summary.snapshot.csa',
            seq: FakeWebSocket.instances.length,
            ts: 1_800_000_000_000,
            payload: summaryPayload(runs),
        });
    };

    return { cards, cardsApi, core, publish, sources: () => cards.map((card) => card.source) };
}

describe('CSA Live View card lifecycle', () => {
    it('gives a running run a board', () => {
        const { publish, sources } = harness();
        publish([{ run_id: 'r0', worker_idx: 0, phase: 'playing' }]);
        expect(sources()).toEqual(['worker-latest:0']);
    });

    it('keeps the board waiting when the current game ends while the run stays alive', () => {
        const { publish, sources } = harness();
        publish([{ run_id: 'r0', worker_idx: 0, phase: 'playing' }]);
        publish([{ run_id: 'r0', worker_idx: 0, phase: 'idle' }]);
        expect(sources()).toEqual(['worker-latest:0']);
    });

    it('takes the board back when the bridge says it stopped', () => {
        const { publish, sources } = harness();
        publish([{ run_id: 'r0', worker_idx: 0, phase: 'playing' }]);
        publish([{ run_id: 'r0', worker_idx: 0, phase: 'playing', stopped: true }]);
        expect(sources()).toEqual([]);
    });

    it('hands a waiting run a board but not a stopped run', () => {
        const { publish, sources } = harness();
        publish([
            { run_id: 'old', worker_idx: 0, phase: 'idle', stopped: true },
            { run_id: 'now', worker_idx: 1, phase: 'playing' },
        ]);
        expect(sources()).toEqual(['worker-latest:1']);
    });

    it('leaves a card the reader added alone, even after that run ends', () => {
        const { publish, cards, cardsApi, sources } = harness();
        // The reader opens a finished run to replay it. Nothing in the summary
        // may take that away, or "past games via card addition" does not work.
        (cardsApi.createCardForSource as unknown as (source: string) => LiveCardState)('worker-latest:0');
        expect(cards).toHaveLength(1);
        publish([{ run_id: 'r0', worker_idx: 0, phase: 'idle' }]);
        expect(sources()).toEqual(['worker-latest:0']);
    });

    /**
     * The case that actually bit: a log directory holding four runs killed two
     * days earlier. None wrote a terminal record, so all four sat in `playing`
     * forever and every one of them opened a board — the exact wall of corpses
     * this profile's redesign exists to remove.
     */
    it('refuses a board to a run whose log went quiet days ago', () => {
        const { publish, sources } = harness();
        publish([
            {
                run_id: 'corpse',
                worker_idx: 0,
                phase: 'playing',
                emits_liveness: false,
                last_event_ts: NOW - 48 * 3600_000,
            },
            { run_id: 'now', worker_idx: 1, phase: 'playing' },
        ]);
        expect(sources()).toEqual(['worker-latest:1']);
    });

    it('still gives a board to a pre-heartbeat bridge in a long think', () => {
        // 180s is the heartbeat threshold and means nothing for a bridge that
        // never promised a record. Denying this one would be a false negative in
        // the single scenario the tool exists for.
        const { publish, sources } = harness();
        publish([
            {
                run_id: 'thinking',
                worker_idx: 0,
                phase: 'playing',
                emits_liveness: false,
                last_event_ts: NOW - 240_000,
            },
        ]);
        expect(sources()).toEqual(['worker-latest:0']);
    });

    it('keeps the board of a bridge that went silent while playing', () => {
        // The bridge was killed: no terminal record, so the phase stays `playing`
        // forever. Retiring it on silence would hide a game that might be live.
        const { publish, sources } = harness();
        publish([{ run_id: 'r0', worker_idx: 0, phase: 'playing' }]);
        publish([{ run_id: 'r0', worker_idx: 0, phase: 'playing' }]);
        expect(sources()).toEqual(['worker-latest:0']);
    });

    it('keeps idle and closing on the waiting board', () => {
        const { publish, core, sources, cardsApi } = harness();
        publish([{ run_id: 'r0', worker_idx: 0, phase: 'idle', current_game_id: null }]);
        expect(sources()).toEqual(['worker-latest:0']);
        expect(core.state.csaRuns?.[0]).toMatchObject({ phase: 'idle', stopped: false });
        expect(cardsApi.updateCardData).toHaveBeenCalled();

        publish([{ run_id: 'r0', worker_idx: 0, phase: 'closing', current_game_id: null }]);
        expect(sources()).toEqual(['worker-latest:0']);
        expect(core.state.csaRuns?.[0]).toMatchObject({ phase: 'closing', stopped: false });
    });

    it('reuses the waiting board when the same run receives its next game', () => {
        const { publish, sources } = harness();
        publish([{ run_id: 'r0', worker_idx: 0, phase: 'playing', current_game_id: 'game-1' }]);
        publish([{ run_id: 'r0', worker_idx: 0, phase: 'idle', current_game_id: null }]);
        publish([{ run_id: 'r0', worker_idx: 0, phase: 'playing', current_game_id: 'game-2' }]);
        expect(sources()).toEqual(['worker-latest:0']);
    });

    it('lets the reader re-add a stopped board permanently', () => {
        const { publish, cardsApi, sources } = harness();
        publish([{ run_id: 'r0', worker_idx: 0, phase: 'playing' }]);
        publish([{ run_id: 'r0', worker_idx: 0, phase: 'idle', stopped: true }]);
        expect(sources()).toEqual([]);
        (cardsApi.createCardForSource as unknown as (source: string) => LiveCardState)('worker-latest:0');
        publish([{ run_id: 'r0', worker_idx: 0, phase: 'idle', stopped: true }]);
        expect(sources()).toEqual(['worker-latest:0']);
    });
});
