import { afterEach, describe, expect, it, vi } from 'vitest';

import { renderBookPairs, renderBookSummary } from '@/modules/book';
import { installBookModule } from '@/modules/book/services/main';
import type { BookPairPayload, BookSummaryPayload, BookWindow } from '@/modules/book/types';

const requestJsonMock = vi.fn(async (url: string) => {
    if (url.includes('/api/book/pairs')) {
        return makePairPayload();
    }
    return { books: [], book_count: 0 } as BookSummaryPayload;
});
vi.mock('@/modules/shared/services/api', () => ({
    requestJson: (url: string) => requestJsonMock(url),
}));

function makePayload(overrides: Partial<BookSummaryPayload> = {}): BookSummaryPayload {
    return {
        book_count: 1,
        note: 'out-of-book is a heuristic upper bound',
        books: [
            {
                key: 'abc123',
                label: 'user_book1.db',
                resolved_path: '/srv/book/user_book1.db',
                fingerprint: { basename: 'user_book1.db', hash_method: 'size_partial_sha256' },
                options: { BookFile: 'user_book1.db', BookMoves: 16 },
                overall: { games: 4, wins: 2, losses: 1, draws: 1, win_rate: 0.625 },
                by_color: {
                    black: { games: 2, wins: 1, losses: 1, draws: 0, win_rate: 0.5 },
                    white: { games: 2, wins: 1, losses: 0, draws: 1, win_rate: 0.75 },
                },
                engines: { E1: { games: 2, wins: 1, losses: 1, draws: 0, win_rate: 0.5 } },
                out_of_book: {
                    samples: 3,
                    unbounded: 1,
                    histogram: { '5': 2, '8': 1 },
                    mean_ply: 6,
                    max_ply: 8,
                    min_ply: 5,
                },
                validation: 'fingerprinted',
            },
        ],
        ...overrides,
    };
}

function makePairPayload(overrides: Partial<BookPairPayload> = {}): BookPairPayload {
    return {
        note: 'book prefix is diagnostic, not proof of engine book selection',
        summary: {
            pairs: 1,
            measured_pairs: 0,
            partial_pairs: 1,
            unmeasured_pairs: 0,
            invalid_pairs: 0,
            ambiguous_pairs: 0,
            unpaired_games: 0,
            mean_prefix_match_rate: 0.5,
        },
        pairs: [
            {
                pair_key: 'E1|E2|slot-0',
                matchup_key: 'E1|E2',
                pair_slot: 0,
                orders: [1, 2],
                game_ids: ['g0001-left', 'g0002-right'],
                same_sfen: true,
                initial_sfen: 'startpos',
                left: {
                    game_id: 'g0001-left',
                    order: 1,
                    black: 'E1',
                    white: 'E2',
                    book_prefix_usi: ['7g7f', '3c3d'],
                    book_prefix_length: 2,
                    measurement_source: 'db_book_hit',
                    measurement_status: 'measured',
                },
                right: {
                    game_id: 'g0002-right',
                    order: 2,
                    black: 'E2',
                    white: 'E1',
                    book_prefix_usi: ['7g7f'],
                    book_prefix_length: 1,
                    measurement_source: 'book_lookup',
                    measurement_status: 'partial',
                },
                measurement_status: 'partial',
                matched_prefix_plies: 1,
                first_diff_ply: 2,
                first_diff_reason: 'length_mismatch',
                prefix_match_rate: 0.5,
            },
        ],
        ...overrides,
    };
}

function unsupported(): never {
    throw new Error('DashboardCore test stub method was not expected to be called');
}

function makeDashboardCore(): NonNullable<BookWindow['DashboardCore']> {
    const state = {
        cards: [],
        workers: new Map(),
        boardAdapters: new Map(),
        workerSnapshots: new Map(),
        numWorkers: 0,
        maxLiveBoards: 0,
        nextCardId: 1,
        gameDataCache: new Map(),
        gamesSignature: '',
        gamesSignatureByCard: new Map(),
        gameMetadata: new Map(),
        offlineNotified: false,
        liveStreamingEnabled: false,
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
        runtimeMode: 'unknown',
    } satisfies NonNullable<BookWindow['DashboardCore']>['state'];
    return {
        state,
        events: {
            on: () => () => {},
            off: () => {},
            emit: () => {},
        },
        mutateState: unsupported,
        getStateSlice: unsupported,
        registerFetcher: () => {},
        getFetcher: () => null,
        warnSoftFailure: unsupported,
        showApiError: () => {},
        showSpsaDetailStreamError: () => {},
        showNotice: () => {},
        notifyDashboardServerStopped: () => {},
        setDisplay: () => {},
        getApiBase: () => '',
        updateElement: () => {},
    };
}

describe('renderBookSummary', () => {
    it('renders a card per book with win rate and histogram', () => {
        const container = document.createElement('div');
        renderBookSummary(makePayload(), container);

        expect(container.querySelectorAll('.book-card')).toHaveLength(1);
        expect(container.textContent).toContain('user_book1.db');
        expect(container.textContent).toContain('62.5%');
        expect(container.textContent).toContain('heuristic upper bound');
        // histogram bars for plies 5 and 8.
        expect(container.querySelectorAll('.book-oob-row')).toHaveLength(2);
        expect(container.textContent).toContain('E1');
    });

    it('escapes book labels/paths to avoid HTML injection', () => {
        const payload = makePayload();
        payload.books[0].label = '<img src=x onerror=alert(1)>';
        const container = document.createElement('div');
        renderBookSummary(payload, container);
        expect(container.querySelector('img')).toBeNull();
        expect(container.innerHTML).toContain('&lt;img');
    });

    it('renders an empty state when there are no books', () => {
        const container = document.createElement('div');
        renderBookSummary(makePayload({ book_count: 0, books: [] }), container);
        expect(container.querySelector('.book-empty')).not.toBeNull();
        expect(container.querySelectorAll('.book-card')).toHaveLength(0);
    });
});

describe('renderBookPairs', () => {
    it('renders pair diagnostics with status, source, and prefix details', () => {
        const container = document.createElement('div');
        renderBookPairs(makePairPayload(), container);

        expect(container.querySelectorAll('.book-pair-row')).toHaveLength(1);
        expect(container.textContent).toContain('diagnostic, not proof');
        expect(container.textContent).toContain('E1|E2');
        expect(container.textContent).toContain('g0001-left');
        expect(container.textContent).toContain('db_book_hit');
        expect(container.textContent).toContain('book_lookup');
        expect(container.textContent).toContain('prefix 2');
        expect(container.textContent).toContain('length_mismatch');
        expect(container.textContent).toContain('50.0%');
        expect(container.querySelector('.book-status')?.textContent).toBe('partial');
    });

    it('renders an empty state when there are no pairs', () => {
        const container = document.createElement('div');
        renderBookPairs(makePairPayload({ pairs: [], summary: { ...makePairPayload().summary, pairs: 0 } }), container);

        expect(container.querySelector('.book-empty')).not.toBeNull();
        expect(container.querySelectorAll('.book-pair-row')).toHaveLength(0);
    });
});

describe('installBookModule out-of-book forwarding', () => {
    afterEach(() => {
        const owner = window as BookWindow;
        owner.DashboardBook = undefined;
        owner.DashboardCore = undefined;
        document.body.innerHTML = '';
        requestJsonMock.mockClear();
    });

    function setup(): BookWindow {
        document.body.innerHTML =
            '<div id="bookSummaryContainer"></div>' + '<input type="checkbox" id="bookOutOfBookToggle" />';
        const owner = window as BookWindow;
        owner.DashboardCore = makeDashboardCore();
        return owner;
    }

    it('omits out_of_book by default and forwards it when the toggle is checked', async () => {
        const owner = setup();
        const api = installBookModule(owner);

        api.refresh();
        await vi.waitFor(() => expect(requestJsonMock).toHaveBeenCalledTimes(1));
        expect(requestJsonMock.mock.calls[0][0]).not.toContain('out_of_book');

        const toggle = document.getElementById('bookOutOfBookToggle') as HTMLInputElement;
        toggle.checked = true;
        api.refresh();
        await vi.waitFor(() => expect(requestJsonMock).toHaveBeenCalledTimes(2));
        expect(requestJsonMock.mock.calls[1][0]).toContain('out_of_book=1');
    });

    it('switches to the pairs subview and forwards the out-of-book query there too', async () => {
        document.body.innerHTML =
            '<button data-book-view="summary"></button>' +
            '<button data-book-view="pairs"></button>' +
            '<div id="bookSummaryContainer"></div>' +
            '<div id="bookPairsContainer" hidden></div>' +
            '<input type="checkbox" id="bookOutOfBookToggle" />';
        const owner = window as BookWindow;
        owner.DashboardCore = makeDashboardCore();
        const api = installBookModule(owner);

        try {
            api.setActive(true);
            await vi.waitFor(() => expect(requestJsonMock).toHaveBeenCalledTimes(1));
            expect(requestJsonMock.mock.calls[0][0]).toBe('/api/book/summary');

            const pairsButton = document.querySelector<HTMLButtonElement>('[data-book-view="pairs"]');
            pairsButton?.click();
            await vi.waitFor(() => expect(requestJsonMock).toHaveBeenCalledTimes(2));
            expect(requestJsonMock.mock.calls[1][0]).toBe('/api/book/pairs');

            const toggle = document.getElementById('bookOutOfBookToggle') as HTMLInputElement;
            toggle.checked = true;
            api.refresh();
            await vi.waitFor(() => expect(requestJsonMock).toHaveBeenCalledTimes(3));
            expect(requestJsonMock.mock.calls[2][0]).toBe('/api/book/pairs?out_of_book=1');
        } finally {
            api.setActive(false);
        }
    });
});
