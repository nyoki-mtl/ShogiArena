import { afterEach, describe, expect, it, vi } from 'vitest';

import { renderBookSummary } from '@/modules/book';
import { installBookModule } from '@/modules/book/services/main';
import type { BookSummaryPayload, BookWindow } from '@/modules/book/types';

const requestJsonMock = vi.fn(async (_url: string) => ({ books: [], book_count: 0 }) as BookSummaryPayload);
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
        owner.DashboardCore = {
            getApiBase: () => '',
            showApiError: () => {},
        } as unknown as NonNullable<BookWindow['DashboardCore']>;
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
});
