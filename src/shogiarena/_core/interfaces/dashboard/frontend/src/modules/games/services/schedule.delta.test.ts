import { describe, expect, it } from 'vitest';

import type { GamesScheduleEntry } from '@/modules/games/types';
import type { GamesModuleState } from '../state';
import type { GamesServiceContext } from './context';
import { createScheduleController, deltaRequiresFullRender } from './schedule';

function createState(): GamesModuleState {
    return {
        active: false,
        realtimeStarted: false,
        retryTimer: null,
        rowsByGame: new Map(),
        selectedGameId: null,
        instanceFilter: null,
        lastSnapshot: null,
        lastRevision: null,
        sortKey: 'order',
        sortDirection: 'asc',
        instanceOptions: [],
        assignmentGameId: null,
        gamesUnavailable: false,
        consecutiveErrors: 0,
        lastErrorMessage: null,
        fetchInFlight: false,
        searchQuery: '',
        sseConnected: false,
        sseRetryDelayMs: null,
    };
}

function createController(state: GamesModuleState) {
    // owner/utils/render are unused on the inactive delta path; stub them with per-field single
    // casts so the object is a typed GamesServiceContext without a double (unknown) assertion.
    const context: GamesServiceContext = {
        owner: {} as GamesServiceContext['owner'],
        state,
        utils: {} as GamesServiceContext['utils'],
        render: {} as GamesServiceContext['render'],
    };
    return createScheduleController({ context });
}

function schedule(state: GamesModuleState): GamesScheduleEntry[] {
    return Array.isArray(state.lastSnapshot?.schedule) ? [...state.lastSnapshot.schedule] : [];
}

describe('applyGamesDelta raw schedule maintenance', () => {
    it('keeps order-only rows (no game_id) on update instead of dropping them', async () => {
        // Backend identifies rows by game_id -> gameId -> id -> order -> display_order.
        // A row identified only by `order` must survive a row delta (regression for the
        // game_id-only keying that dropped such rows from nextSchedule).
        const state = createState();
        state.active = false;
        state.lastRevision = 0;
        state.lastSnapshot = {
            schedule: [{ order: 5, status: 'pending', black: 'A', white: 'B' }],
            revision: 0,
            base_revision: 0,
        };
        const controller = createController(state);

        await controller.applyGamesDelta({
            kind: 'delta',
            revision: 1,
            base_revision: 0,
            rows: [{ op: 'update', row: { order: 5, status: 'running', black: 'A', white: 'B' } }],
            snapshot_meta: {},
        });

        const rows = schedule(state);
        expect(rows).toHaveLength(1);
        expect(rows[0].status).toBe('running');
        // Raw entry preserved (order key intact, not normalized to order_index).
        expect(rows[0].order).toBe(5);
    });

    it('adds a new order-only row from a delta', async () => {
        const state = createState();
        state.active = false;
        state.lastRevision = 0;
        state.lastSnapshot = {
            schedule: [{ order: 0, status: 'pending', black: 'A', white: 'B' }],
            revision: 0,
            base_revision: 0,
        };
        const controller = createController(state);

        await controller.applyGamesDelta({
            kind: 'delta',
            revision: 1,
            base_revision: 0,
            rows: [{ op: 'add', row: { order: 1, status: 'pending', black: 'C', white: 'D' } }],
            snapshot_meta: {},
        });

        const rows = schedule(state);
        expect(rows).toHaveLength(2);
        expect(rows.map((row) => row.order).sort()).toEqual([0, 1]);
    });

    it('removes an order-only row by its resolved key', async () => {
        const state = createState();
        state.active = false;
        state.lastRevision = 0;
        state.lastSnapshot = {
            schedule: [
                { order: 0, status: 'pending', black: 'A', white: 'B' },
                { order: 1, status: 'pending', black: 'C', white: 'D' },
            ],
            revision: 0,
            base_revision: 0,
        };
        const controller = createController(state);

        await controller.applyGamesDelta({
            kind: 'delta',
            revision: 1,
            base_revision: 0,
            // Backend emits { op: 'remove', id: '<resolved-key>' }; for an order-only row that is "1".
            rows: [{ op: 'remove', id: '1' }],
            snapshot_meta: {},
        });

        const rows = schedule(state);
        expect(rows).toHaveLength(1);
        expect(rows[0].order).toBe(0);
    });

    it('still preserves order/round and base plies for game_id rows (no double normalization)', async () => {
        const state = createState();
        state.active = false;
        state.lastRevision = 0;
        state.lastSnapshot = {
            schedule: [
                { game_id: 'g1', order: 3, round: 2, status: 'pending', black: 'A', white: 'B', total_plies: 10 },
            ],
            revision: 0,
            base_revision: 0,
        };
        const controller = createController(state);

        await controller.applyGamesDelta({
            kind: 'delta',
            revision: 1,
            base_revision: 0,
            rows: [
                {
                    op: 'update',
                    row: {
                        game_id: 'g1',
                        order: 3,
                        round: 2,
                        status: 'completed',
                        black: 'A',
                        white: 'B',
                        total_plies: 10,
                    },
                },
            ],
            snapshot_meta: {},
        });

        const rows = schedule(state);
        expect(rows).toHaveLength(1);
        // Schedule still holds the raw entry (order/round present, total_plies not double-counted).
        expect(rows[0].order).toBe(3);
        expect(rows[0].round).toBe(2);
        expect(rows[0].total_plies).toBe(10);
        expect(rows[0].status).toBe('completed');
    });
});

describe('CSA game navigation', () => {
    it('prefers the persisted board for a completed game', () => {
        document.body.dataset.dashboardProfile = 'csa';
        const calls: Array<{ gameId: string; options: Record<string, unknown> }> = [];
        const owner = {
            document,
            DashboardNavigation: {
                openGame: (gameId: string, options: Record<string, unknown>) => {
                    calls.push({ gameId, options });
                    return Promise.resolve();
                },
            },
        } as GamesServiceContext['owner'];
        const context: GamesServiceContext = {
            owner,
            state: createState(),
            utils: {} as GamesServiceContext['utils'],
            render: {} as GamesServiceContext['render'],
        };

        createScheduleController({ context }).openGameInLiveView('finished-game', {
            source: 'games',
            status: 'completed',
        });

        expect(calls).toEqual([
            {
                gameId: 'finished-game',
                options: { source: 'games', status: 'completed', preferArchived: true },
            },
        ]);
        delete document.body.dataset.dashboardProfile;
    });
});

function setupDom(): HTMLElement {
    document.body.innerHTML = `
        <div id="gamesTable">
            <table class="games-table">
                <thead><tr><th>Order</th></tr></thead>
                <tbody></tbody>
            </table>
        </div>
        <div id="gamesSummary"></div>
    `;
    const tbody = document.querySelector<HTMLElement>('#gamesTable tbody');
    if (!tbody) throw new Error('tbody missing');
    return tbody;
}

function createActiveController(state: GamesModuleState) {
    const render = {
        buildRow: (normalized: { game_id: string | null; order_index: number | null; status: string }) => {
            const tr = document.createElement('tr');
            tr.dataset.gameId = normalized.game_id ?? '';
            tr.dataset.orderIndex = String(normalized.order_index ?? 0);
            tr.dataset.status = normalized.status;
            return tr;
        },
        sortRows: (rows: Array<{ order_index: number | null }>) => {
            rows.sort((a, b) => (a.order_index ?? 0) - (b.order_index ?? 0));
        },
        updateSortHeaders: () => {},
    };
    const context: GamesServiceContext = {
        owner: {} as GamesServiceContext['owner'],
        state,
        utils: {} as GamesServiceContext['utils'],
        render: render as GamesServiceContext['render'],
    };
    return createScheduleController({ context });
}

describe('applyGamesDelta active fast-path fallback', () => {
    it('falls back to a full render for an order-only delta so the active table is not stale', async () => {
        const tbody = setupDom();
        const state = createState();
        state.active = true;
        const controller = createActiveController(state);

        // Seed the active table with a single game_id row.
        controller.renderGamesTable({
            schedule: [{ game_id: 'g1', order: 0, status: 'pending', black: 'A', white: 'B' }],
            revision: 0,
            base_revision: 0,
        });
        expect(tbody.querySelectorAll('tr')).toHaveLength(1);

        // An order-only add cannot be placed by the game_id DOM fast-path; without the fallback
        // the fast-path would report success and leave the table stale at one row.
        await controller.applyGamesDelta({
            kind: 'delta',
            revision: 1,
            base_revision: 0,
            rows: [{ op: 'add', row: { order: 1, status: 'running', black: 'C', white: 'D' } }],
            snapshot_meta: {},
        });

        expect(tbody.querySelectorAll('tr')).toHaveLength(2);
        expect(tbody.querySelector('tr[data-order-index="1"]')).not.toBeNull();
    });
});

describe('deltaRequiresFullRender', () => {
    const noLookup = () => undefined;

    it('returns false when all add/update rows carry a game_id', () => {
        const rows = [
            { op: 'add', row: { game_id: 'g1', order: 0 } },
            { op: 'update', row: { game_id: 'g2', order: 1 } },
        ];
        expect(deltaRequiresFullRender(rows, noLookup)).toBe(false);
    });

    it('returns true for an order-only add (no game_id) — DOM fast-path cannot place it', () => {
        const rows = [{ op: 'add', row: { order: 5 } }];
        expect(deltaRequiresFullRender(rows, noLookup)).toBe(true);
    });

    it('returns true for an order-only update (no game_id)', () => {
        const rows = [{ op: 'update', row: { order: 5, status: 'running' } }];
        expect(deltaRequiresFullRender(rows, noLookup)).toBe(true);
    });

    it('returns true when removing a row that resolves to an order-only raw entry', () => {
        const raw: GamesScheduleEntry = { order: 5, status: 'pending' };
        expect(deltaRequiresFullRender([{ op: 'remove', id: '5' }], (id) => (id === '5' ? raw : undefined))).toBe(true);
    });

    it('returns false when removing a game_id row', () => {
        const raw: GamesScheduleEntry = { game_id: 'g1', order: 5 };
        expect(deltaRequiresFullRender([{ op: 'remove', id: 'g1' }], (id) => (id === 'g1' ? raw : undefined))).toBe(
            false,
        );
    });

    it('returns false when a remove target is already absent (nothing to reconcile)', () => {
        expect(deltaRequiresFullRender([{ op: 'remove', id: 'gone' }], noLookup)).toBe(false);
    });

    it('returns true when any row in a mixed batch is order-only', () => {
        const rows = [
            { op: 'update', row: { game_id: 'g1', order: 0 } },
            { op: 'add', row: { order: 9 } },
        ];
        expect(deltaRequiresFullRender(rows, noLookup)).toBe(true);
    });
});
