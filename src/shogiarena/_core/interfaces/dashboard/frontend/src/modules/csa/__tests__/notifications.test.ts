import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { installCsaModule } from '@/modules/csa/index';
import type { CsaState } from '@/modules/csa/types';
import type { ArenaDashboardWindow } from '@/types/globals';

/**
 * What is allowed to interrupt the operator.
 *
 * A notice is the only thing in this profile that reaches someone who is *not*
 * looking at the monitoring tab, so its bar is higher than the table's. The
 * failure this pins down was observed on a real log directory: thirteen runs,
 * an empty Live View, and a red 「着手期限を超過しました」 for runs that had been
 * dead for a day and a half.
 */

const NOW = 1_800_000_100_000;

function makeState(overrides: Partial<CsaState> = {}): CsaState {
    return {
        run_id: '1800000000',
        worker_idx: 0,
        stream_generation: 0,
        current_game_id: 'game-1',
        persistence: null,
        bridge_version: '0.1.0',
        engine_name: null,
        engine_author: null,
        engine_options: [],
        phase: 'playing',
        phase_since_ts: NOW - 60_000,
        stopped: false,
        emits_liveness: true,
        last_event_ts: NOW - 5_000,
        my_color: 'black',
        opponent_name: 'opponent',
        side_to_move: 'black',
        alerts: [],
        outstanding_search: { kind: 'go', ply: 42, deadline_ts: NOW - 10_000, predicted_usi: null },
        deadline_ts: NOW - 10_000,
        last_move_origin: 'engine',
        fallback_plies: [],
        ledger: { black_ms: null, white_ms: null, as_of_ts: null },
        score: { wins: 0, losses: 0, draws: 0 },
        games: [
            {
                game_id: 'game-1',
                result: null,
                game_result: null,
                current_ply: 42,
                black_name: 'rss-csa-test',
                white_name: 'opponent',
                my_color: 'black',
                terminal: [],
                started_ts: NOW - 600_000,
                ended_ts: null,
            },
        ],
        replay: { is_complete: true, failure: null },
        log_health: {
            missing_seq: 0,
            malformed: 0,
            invalid_lines: 0,
            orphan_records: 0,
            ply_gaps: 0,
            unknown_types: {},
            has_partial_line: false,
            restarts: 0,
        },
        ...overrides,
    };
}

function mount(states: readonly CsaState[]) {
    document.body.innerHTML = '<div id="csaStatusPanel"></div><button id="tabCsa"></button>';
    const notify = vi.fn();
    const openGame = vi.fn(async () => {});
    const owner = {
        document,
        DashboardShowNotice: notify,
        DashboardNavigation: { openGame },
        DashboardCore: {
            state: { workerSnapshots: new Map(states.map((s, i) => [i, { meta: { csa: s } }])), csaRuns: [] },
        },
    } as unknown as ArenaDashboardWindow;
    const api = installCsaModule(owner);
    const snapshots = (owner.DashboardCore as unknown as { state: { workerSnapshots: Map<number, unknown> } }).state
        .workerSnapshots;
    return { notify, openGame, api, snapshots };
}

beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(NOW);
});

afterEach(() => {
    vi.useRealTimers();
    document.body.innerHTML = '';
});

describe('overdue-deadline notices', () => {
    it('interrupts for a game that is actually being played', () => {
        // Blown *after* the page opened, which is what makes it news.
        const { notify, api } = mount([makeState({ deadline_ts: NOW + 5_000 })]);
        try {
            vi.setSystemTime(NOW + 10_000);
            vi.advanceTimersByTime(500);
            expect(notify).toHaveBeenCalledWith(expect.stringContaining('move deadline'), 'error');
        } finally {
            api.stop();
        }
    });

    it('says nothing about a run that ended days ago', () => {
        // `outstanding_search` is frozen at whatever was in flight when the log
        // stopped, so on a dead run every comparison against `now` reports it as
        // blown — by a margin that only grows.
        const dead = makeState({
            phase: 'playing',
            last_event_ts: NOW - 36 * 3600_000,
            deadline_ts: NOW - 36 * 3600_000,
            outstanding_search: { kind: 'go', ply: 8, deadline_ts: NOW - 36 * 3600_000, predicted_usi: null },
        });
        const { notify, api } = mount([dead]);
        try {
            expect(notify).not.toHaveBeenCalledWith(expect.stringContaining('move deadline'), 'error');
        } finally {
            api.stop();
        }
    });

    it('says nothing about a run that finished normally', () => {
        const { notify, api } = mount([makeState({ phase: 'idle' })]);
        try {
            expect(notify).not.toHaveBeenCalledWith(expect.stringContaining('move deadline'), 'error');
        } finally {
            api.stop();
        }
    });

    it('does not interrupt for a deadline blown before the page opened', () => {
        // Same rule the alerts already follow: still in the panel, no longer news.
        const { notify, api } = mount([
            makeState({
                deadline_ts: NOW - 1,
                outstanding_search: { kind: 'go', ply: 42, deadline_ts: NOW - 1, predicted_usi: null },
            }),
        ]);
        try {
            expect(notify).not.toHaveBeenCalledWith(expect.stringContaining('move deadline'), 'error');
        } finally {
            api.stop();
        }
    });

    it('announces once, not every tick', () => {
        const { notify, api } = mount([makeState({ deadline_ts: NOW + 5_000 })]);
        try {
            vi.setSystemTime(NOW + 10_000);
            vi.advanceTimersByTime(2_000);
            const overdue = notify.mock.calls.filter(([message]) => String(message).includes('move deadline'));
            expect(overdue).toHaveLength(1);
        } finally {
            api.stop();
        }
    });

    it('does not let one game silence the same ply in the next game', () => {
        // `run_id + ply` was not unique: a run plays several games, so ply 42 of
        // the second reused the key from ply 42 of the first and was swallowed.
        const first = makeState({ deadline_ts: NOW + 5_000 });
        const { notify, api, snapshots } = mount([first]);
        try {
            vi.setSystemTime(NOW + 10_000);
            vi.advanceTimersByTime(500);

            const second = makeState({
                deadline_ts: NOW + 20_000,
                outstanding_search: { kind: 'go', ply: 42, deadline_ts: NOW + 20_000, predicted_usi: null },
                games: [{ ...first.games[0], game_id: 'game-2' }],
            });
            snapshots.set(0, { meta: { csa: second } });
            vi.setSystemTime(NOW + 25_000);
            vi.advanceTimersByTime(500);

            const overdue = notify.mock.calls.filter(([message]) => String(message).includes('move deadline'));
            expect(overdue).toHaveLength(2);
        } finally {
            api.stop();
        }
    });
});

describe('Game navigation', () => {
    it('keeps a current game on the live worker source', () => {
        const { openGame, api } = mount([makeState({ deadline_ts: null, outstanding_search: null })]);
        try {
            (document.querySelector('[data-csa-open-game]') as HTMLButtonElement).click();
            expect(openGame).toHaveBeenCalledWith('game-1', {
                source: 'csa-monitor',
                preferArchived: false,
            });
        } finally {
            api.stop();
        }
    });

    it('leaves finished-game navigation to the Games tab', () => {
        const { openGame, api } = mount([
            makeState({
                current_game_id: null,
                phase: 'idle',
                deadline_ts: null,
                outstanding_search: null,
                games: [{ ...makeState().games[0], result: 'win', game_result: 'BLACK_WIN', ended_ts: NOW }],
            }),
        ]);
        try {
            expect(document.querySelector('[data-csa-open-game]')).toBeNull();
            expect(openGame).not.toHaveBeenCalled();
        } finally {
            api.stop();
        }
    });
});

describe('alert scope', () => {
    it('does not announce a previous game alert while the next game is active', () => {
        const { notify, api } = mount([
            makeState({
                current_game_id: 'game-2',
                deadline_ts: null,
                outstanding_search: null,
                alerts: [
                    {
                        seq: 7,
                        level: 'error',
                        code: 'suspect_resign',
                        detail: 'game 1 only',
                        ts: NOW + 1_000,
                        game_id: 'game-1',
                    },
                ],
                games: [
                    { ...makeState().games[0], game_id: 'game-1', ended_ts: NOW - 30_000 },
                    { ...makeState().games[0], game_id: 'game-2' },
                ],
            }),
        ]);
        try {
            vi.setSystemTime(NOW + 2_000);
            vi.advanceTimersByTime(500);

            expect(notify).not.toHaveBeenCalledWith(expect.stringContaining('suspect_resign'), 'error');
            expect(document.getElementById('tabCsa')?.classList.contains('dashboard-tab--attention')).toBe(false);
        } finally {
            api.stop();
        }
    });

    it('announces a run-level alert regardless of the current game', () => {
        const { notify, api, snapshots } = mount([makeState({ alerts: [] })]);
        try {
            snapshots.set(0, {
                meta: {
                    csa: makeState({
                        alerts: [
                            {
                                seq: 8,
                                level: 'error',
                                code: 'bridge_fault',
                                detail: null,
                                ts: NOW + 1_000,
                                game_id: null,
                            },
                        ],
                    }),
                },
            });
            vi.setSystemTime(NOW + 2_000);
            vi.advanceTimersByTime(500);

            expect(notify).toHaveBeenCalledWith(expect.stringContaining('bridge_fault'), 'error');
        } finally {
            api.stop();
        }
    });
});

/**
 * Swapping `innerHTML` destroys every element beneath it, including the focused
 * one — and the tick that does it arrives every 250ms. A reader tabbing to a
 * row's actions had the button pulled out from under them before they could
 * press it.
 */
describe('repaint while the reader is inside the table', () => {
    function focusFirstToggle(): HTMLElement {
        const button = document.querySelector<HTMLElement>('[data-csa-toggle]');
        if (!button) throw new Error('expected a disclosure button');
        button.focus();
        return button;
    }

    it('keeps the focused element alive across ticks', () => {
        const { api, snapshots } = mount([makeState({ deadline_ts: null, outstanding_search: null })]);
        try {
            const button = focusFirstToggle();
            snapshots.set(0, {
                meta: { csa: makeState({ deadline_ts: null, outstanding_search: null, phase: 'agreed' }) },
            });
            vi.advanceTimersByTime(1_000);

            expect(document.activeElement).toBe(button);
            expect(button.isConnected).toBe(true);
        } finally {
            api.stop();
        }
    });

    it('applies the newest state once focus leaves', () => {
        const { api, snapshots } = mount([makeState({ deadline_ts: null, outstanding_search: null })]);
        try {
            focusFirstToggle();
            snapshots.set(0, {
                meta: {
                    csa: makeState({
                        deadline_ts: null,
                        outstanding_search: null,
                        phase: 'idle',
                        stopped: true,
                        current_game_id: null,
                    }),
                },
            });
            vi.advanceTimersByTime(500);
            expect(document.querySelector('[data-state="finished"]')).toBeNull();

            (document.activeElement as HTMLElement).blur();
            vi.advanceTimersByTime(500);

            // Whatever was held back lands whole — the reader gets the current
            // table, not the one from the moment they tabbed in.
            expect(document.querySelector('[data-state="finished"]')).not.toBeNull();
        } finally {
            api.stop();
        }
    });

    it('still interrupts while focus is held', () => {
        // A notice is not a repaint. A fault that fires while the reader happens
        // to be tabbing through the table still has to reach them.
        const { api, notify, snapshots } = mount([makeState({ deadline_ts: null, outstanding_search: null })]);
        try {
            focusFirstToggle();
            snapshots.set(0, {
                meta: {
                    csa: makeState({
                        deadline_ts: null,
                        outstanding_search: null,
                        alerts: [{ seq: 1, level: 'error', code: 'engine_dead', detail: null, ts: NOW + 1_000 }],
                    }),
                },
            });
            vi.setSystemTime(NOW + 2_000);
            vi.advanceTimersByTime(500);

            expect(notify).toHaveBeenCalledWith(expect.stringContaining('engine_dead'), 'error');
            expect(document.getElementById('tabCsa')?.classList.contains('dashboard-tab--attention')).toBe(true);
        } finally {
            api.stop();
        }
    });
});
