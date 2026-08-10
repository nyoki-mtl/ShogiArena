import { describe, expect, it } from 'vitest';

import {
    classifyDeadline,
    collectFaults,
    createCsaStatusRenderCache,
    formatClockMs,
    orderAlerts,
    renderCsaStatusPanels,
} from '@/modules/csa/components/status-panel';
import { type CsaAlert, type CsaState, classifyCsaRunState, deservesLiveBoard } from '@/modules/csa/types';

const NOW = 1_800_000_100_000;

function makeState(overrides: Partial<CsaState> = {}): CsaState {
    return {
        run_id: '1800000000',
        worker_idx: 0,
        stream_generation: 0,
        current_game_id: 'wdoor+floodgate-300-10F+a+b+1',
        persistence: null,
        bridge_version: '0.1.0',
        engine_name: 'YaneuraOu NNUE 8.30',
        engine_author: 'Motohiro Isozaki',
        engine_options: [{ name: 'Threads', value: '4' }],
        phase: 'playing',
        phase_since_ts: NOW - 60_000,
        stopped: false,
        emits_liveness: true,
        last_event_ts: NOW - 5_000,
        my_color: 'black',
        opponent_name: 'fixture-opponent-a',
        side_to_move: 'black',
        alerts: [],
        outstanding_search: null,
        deadline_ts: null,
        last_move_origin: 'engine',
        fallback_plies: [],
        ledger: { black_ms: 300_000, white_ms: 280_000, as_of_ts: NOW - 5_000 },
        score: { wins: 1, losses: 0, draws: 0 },
        games: [
            {
                game_id: 'wdoor+floodgate-300-10F+a+b+1',
                result: null,
                game_result: null,
                current_ply: 42,
                black_name: 'rss-csa-test',
                white_name: 'fixture-opponent-a',
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

describe('clock formatting', () => {
    it('reads as a clock, with hours only when there are hours', () => {
        expect(formatClockMs(63_000)).toBe('1:03');
        expect(formatClockMs(3_723_000)).toBe('1:02:03');
    });

    it('shows dashes rather than a fake zero when the ledger is unknown', () => {
        expect(formatClockMs(null)).toBe('--:--');
    });

    it('never renders negative time', () => {
        expect(formatClockMs(-5_000)).toBe('0:00');
    });
});

describe('deadline severity', () => {
    it('is normal with time to spare', () => {
        expect(classifyDeadline(NOW + 60_000, NOW)).toBe('normal');
    });

    it('warns inside the last ten seconds', () => {
        expect(classifyDeadline(NOW + 4_000, NOW)).toBe('caution');
    });

    it('flags an exceeded deadline', () => {
        expect(classifyDeadline(NOW - 1, NOW)).toBe('overdue');
    });

    it('is normal when no search is outstanding', () => {
        expect(classifyDeadline(null, NOW)).toBe('normal');
    });
});

describe('alert ordering', () => {
    const alerts: CsaAlert[] = [
        { seq: 10, level: 'warn', code: 'engine_restarted', detail: null, ts: null },
        { seq: 4, level: 'error', code: 'engine_dead', detail: 'engine process ended', ts: null },
        { seq: 20, level: 'warn', code: 'engine_slow', detail: null, ts: null },
    ];

    it('pins errors above warnings', () => {
        expect(orderAlerts(alerts).map((alert) => alert.code)).toEqual([
            'engine_dead',
            'engine_slow',
            'engine_restarted',
        ]);
    });

    it('does not mutate the input', () => {
        const copy = [...alerts];
        orderAlerts(alerts);
        expect(alerts).toEqual(copy);
    });
});

/**
 * State is one closed list, not three columns that each say part of it.
 *
 * Phase and liveness are separate facts in the log; the reader gets one answer.
 * Liveness wins the label because a bridge that was killed must not go on
 * claiming to be playing — but the phase it died in survives in the detail row.
 */
describe('run state', () => {
    it('calls everything before the first move 対局待ち, with how long it has waited', () => {
        // Three labels for one fact bought nothing: `warming`, `connecting` and
        // `agreed` each last seconds, and the dwell time carries what mattered.
        expect(classifyCsaRunState(makeState({ phase: 'connecting' }), NOW).label).toBe('Waiting for pairing');
        expect(classifyCsaRunState(makeState({ phase: 'waiting_pairing' }), NOW).label).toBe('Waiting for pairing');
        expect(classifyCsaRunState(makeState({ phase: 'agreed' }), NOW).label).toBe('Waiting for pairing');
        const playing = classifyCsaRunState(makeState(), NOW);
        expect(playing.label).toBe('Playing');
        expect(playing.elapsedMs).toBe(60_000);
        expect(playing.indicator).toBe('running');
    });

    it('keeps idle and closing as live run phases with a waiting board', () => {
        const idle = makeState({ phase: 'idle', current_game_id: null });
        expect(classifyCsaRunState(idle, NOW).label).toBe('Waiting for next game');
        expect(classifyCsaRunState(idle, NOW).kind).toBe('waiting');
        expect(deservesLiveBoard(idle, NOW)).toBe(true);

        const closing = makeState({ phase: 'closing', current_game_id: null });
        expect(classifyCsaRunState(closing, NOW).label).toBe('Shutting down');
        expect(classifyCsaRunState(closing, NOW).kind).toBe('waiting');
        expect(deservesLiveBoard(closing, NOW)).toBe(true);
    });

    it('calls a bridge that said it was done finished, whatever phase it stopped in', () => {
        const view = classifyCsaRunState(makeState({ stopped: true }), NOW);
        expect(view.kind).toBe('finished');
        expect(view.label).toBe('Finished');
    });

    it('calls a heartbeat-writing bridge abnormally ended once it goes quiet', () => {
        // A broken promise is an observation, not a guess: the process that
        // would have written the heartbeat is not writing it.
        const view = classifyCsaRunState(makeState({ last_event_ts: NOW - 200_000 }), NOW);
        expect(view.kind).toBe('abnormal');
        expect(view.label).toBe('Terminated');
        expect(view.severity).toBe('error');
        expect(view.elapsedMs).toBe(200_000);
        expect(view.note).toContain('Last event');
    });

    it('keeps the phase label when a promise-less bridge merely goes quiet', () => {
        // Nothing was promised, so nothing is broken and there is no verdict to
        // give — only the observation, which rides on the dot and the tooltip.
        const view = classifyCsaRunState(makeState({ emits_liveness: false, last_event_ts: NOW - 300_000 }), NOW);
        expect(view.label).toBe('Playing');
        expect(view.severity).toBe('warn');
        expect(view.note).toContain('No updates');
    });

    it('leaves a bridge in a long think alone', () => {
        // 100s of silence is well inside a heartbeat-capable bridge's promise.
        expect(classifyCsaRunState(makeState({ last_event_ts: NOW - 100_000 }), NOW).kind).toBe('playing');
    });

    /**
     * A table with standing amber rows teaches the operator to ignore amber.
     * An uncertainty two days old has stopped being a question — but the page
     * never observed an end, so it must not be promoted to 終了 either.
     */
    it('convicts a promise-less bridge only past the bound where no silence is legitimate', () => {
        const aged = classifyCsaRunState(makeState({ emits_liveness: false, last_event_ts: NOW - 40 * 60_000 }), NOW);
        expect(aged.kind).toBe('abnormal');
        expect(aged.label).toBe('Terminated');
        expect(aged.note).toContain('no game-end record');
        // By the time it can be asserted, it is already history.
        expect(aged.stale).toBe(true);
        expect(aged.severity).toBeNull();
    });

    it('lets an incident stop shouting without changing what it says', () => {
        // Loud for the first half hour; the toast already interrupted at the
        // moment of death, and a permanently red row trains blindness.
        const fresh = classifyCsaRunState(makeState({ last_event_ts: NOW - 200_000 }), NOW);
        const old = classifyCsaRunState(makeState({ last_event_ts: NOW - 40 * 60_000 }), NOW);
        expect(fresh.severity).toBe('error');
        expect(fresh.stale).toBe(false);
        expect(old.severity).toBeNull();
        expect(old.stale).toBe(true);
        expect(old.label).toBe(fresh.label);
    });
});

/**
 * Creation and retirement are deliberately asymmetric. Retirement is a promise
 * about something already on screen; creation is an inference about liveness.
 */
describe('board eligibility', () => {
    it('gives a live run a board', () => {
        expect(deservesLiveBoard(makeState(), NOW)).toBe(true);
    });

    it('refuses one to a run whose log went quiet days ago', () => {
        expect(deservesLiveBoard(makeState({ emits_liveness: false, last_event_ts: NOW - 48 * 3600_000 }), NOW)).toBe(
            false,
        );
    });

    it('refuses one to a heartbeat bridge that stopped writing', () => {
        expect(deservesLiveBoard(makeState({ last_event_ts: NOW - 200_000 }), NOW)).toBe(false);
    });

    it('still gives one to a pre-heartbeat bridge in a four-minute think', () => {
        // 180s is the heartbeat threshold and means nothing here: a bridge that
        // never promised a record can be legitimately quiet for a long search.
        expect(deservesLiveBoard(makeState({ emits_liveness: false, last_event_ts: NOW - 240_000 }), NOW)).toBe(true);
    });
});

/** Faults colour the row and fill the detail. They never rename the state. */
describe('faults', () => {
    it('finds nothing wrong with a healthy run', () => {
        expect(collectFaults(makeState(), NOW)).toEqual([]);
    });

    it('puts errors first so the chip shows the worst one', () => {
        const faults = collectFaults(
            makeState({
                fallback_plies: [12],
                alerts: [{ seq: 9, level: 'error', code: 'engine_dead', detail: 'process ended', ts: NOW }],
            }),
            NOW,
        );
        expect(faults[0]?.label).toBe('engine_dead');
        expect(faults).toHaveLength(2);
    });

    it('says nothing about a deadline that is comfortably far off', () => {
        const faults = collectFaults(
            makeState({
                outstanding_search: { kind: 'go', ply: 42, deadline_ts: NOW + 200_000, predicted_usi: null },
            }),
            NOW,
        );
        expect(faults).toEqual([]);
    });

    it('signals only run alerts and alerts for the current game', () => {
        const state = makeState({
            current_game_id: 'current',
            alerts: [
                { seq: 1, level: 'error', code: 'old_game', detail: null, ts: NOW, game_id: 'old' },
                { seq: 2, level: 'error', code: 'current_game', detail: null, ts: NOW, game_id: 'current' },
                { seq: 3, level: 'warn', code: 'run_alert', detail: null, ts: NOW, game_id: null },
            ],
        });
        expect(collectFaults(state, NOW).map((fault) => fault.label)).toEqual(['current_game', 'run_alert']);
        expect(renderCsaStatusPanels([state], NOW, new Set([state.run_id]))).not.toContain('old_game');
    });
});

/**
 * The table's contract: a healthy run is quiet, a troubled one is loud.
 *
 * These assert the *absence* of noise as much as the presence of signal, because
 * the failure this layout replaced was not a missing value — it was every value
 * shown at once, which buried the one that mattered.
 */
describe('monitoring table', () => {
    it('says so plainly when there is nothing to watch', () => {
        expect(renderCsaStatusPanels([], NOW)).toContain('No event logs');
    });

    it('bounds settled history and offers a deliberate next page', () => {
        const settled = Array.from({ length: 1_000 }, (_, index) =>
            makeState({
                run_id: String(1_800_000_000 + index),
                worker_idx: index,
                stopped: true,
                current_game_id: null,
            }),
        );
        const markup = renderCsaStatusPanels(settled, NOW);
        expect(markup.match(/data-csa-run=/g)).toHaveLength(20);
        expect(markup).toContain('Show more (980)');
    });

    it('reuses settled rows across refresh ticks', () => {
        const settled = Array.from({ length: 100 }, (_, index) =>
            makeState({
                run_id: String(1_800_000_000 + index),
                worker_idx: index,
                stopped: true,
                current_game_id: null,
            }),
        );
        const cache = createCsaStatusRenderCache();

        const first = renderCsaStatusPanels(settled, NOW, new Set(), 20, cache);
        const rows = new Map(cache.settledRows);
        const second = renderCsaStatusPanels(settled, NOW + 250, new Set(), 20, cache);

        expect(second).toBe(first);
        expect(cache.settledRows).toEqual(rows);
        expect(cache.settledRows.size).toBe(20);
    });

    it('gives a healthy run its state and current game, and nothing else', () => {
        const markup = renderCsaStatusPanels([makeState()], NOW);
        expect(markup).toContain('data-state="playing"');
        expect(markup).toContain('data-status-indicator="running"');
        expect(markup).toContain('csa-row--running');
        expect(markup).toContain('class="csa-game-link"');
        expect(markup).toContain('>Log</button>');
        expect(markup).not.toContain('games-order-label');
        expect(markup).not.toContain('csa-chip');
    });

    it('shows the score owned by the run', () => {
        const markup = renderCsaStatusPanels([makeState()], NOW);
        expect(markup).toContain('<th scope="col">W-L-D</th>');
        expect(markup).toContain('csa-cell__record numeric">1-0-0');
    });

    it('shows a legacy silence warning without relying on a tooltip', () => {
        const markup = renderCsaStatusPanels([makeState({ emits_liveness: false, last_event_ts: NOW - 300_000 })], NOW);
        expect(markup).toContain('No updates 5m ago');
        expect(markup).toContain('csa-chip--warn');
    });

    it('does not duplicate player identities from Games', () => {
        const markup = renderCsaStatusPanels([makeState()], NOW);
        expect(markup).not.toContain('<th scope="col">Black</th>');
        expect(markup).not.toContain('<th scope="col">White</th>');
        expect(markup).not.toContain('csa-ownership-pill');
    });

    it('leaves Current Game empty before a pairing', () => {
        const markup = renderCsaStatusPanels(
            [makeState({ phase: 'waiting_pairing', opponent_name: null, my_color: null, games: [] })],
            NOW,
        );
        expect(markup).toContain('csa-cell__current" title=""><span class="games-time-empty">—</span>');
    });

    it('shows run identity and score but omits clocks and ponder', () => {
        const state = makeState();
        const markup = renderCsaStatusPanels([state], NOW);
        const expandedMarkup = renderCsaStatusPanels([state], NOW, new Set([state.run_id]));
        expect(markup).toContain('csa-cell__session');
        expect(markup).not.toContain('5:00');
        expect(expandedMarkup).not.toContain('ponder');
        expect(markup).toContain('1-0-0');
    });

    it('puts waiting state in the explicit run State column', () => {
        const waiting = makeState({ phase: 'waiting_pairing', opponent_name: null, my_color: null, games: [] });
        const markup = renderCsaStatusPanels([waiting], NOW);
        expect(markup).toContain('class="csa-result-state"');
        expect(markup).toContain('Waiting for pairing');
        expect(markup).toContain('<th scope="col">State</th>');
        expect(markup).toContain('csa-cell__state');
    });

    it('reports a killed bridge as unresponsive rather than as a game in progress', () => {
        const markup = renderCsaStatusPanels([makeState({ last_event_ts: NOW - 600_000 })], NOW);
        expect(markup).toContain('Terminated');
        expect(markup).not.toContain('Playing');
        expect(markup).toContain('data-status-indicator="error"');
    });

    it('flags an error alert with one chip on a run that is still live', () => {
        const markup = renderCsaStatusPanels(
            [
                makeState({
                    alerts: [{ seq: 9, level: 'error', code: 'engine_dead', detail: 'process ended', ts: NOW }],
                }),
            ],
            NOW,
        );
        expect(markup).toContain('engine_dead');
        expect(markup).toContain('data-status-indicator="error"');
        // The state itself is unchanged: it is still playing, with a dead engine.
        expect(markup).toContain('Playing');
        expect(markup.match(/class="csa-chip /g)).toHaveLength(1);
    });

    /**
     * The row tint was an *area* signal, so it scaled with row count rather than
     * importance: four runs that died on Tuesday out-shouted the one playing now.
     * Severity lives in the dot and one chip, on a table that is otherwise
     * monochrome.
     */
    it('never washes a row in severity colour', () => {
        const markup = renderCsaStatusPanels(
            [
                makeState({ alerts: [{ seq: 9, level: 'error', code: 'engine_dead', detail: null, ts: NOW }] }),
                makeState({ run_id: '1800000001', worker_idx: 1, fallback_plies: [3] }),
            ],
            NOW,
        );
        expect(markup).not.toContain('csa-row--error');
        expect(markup).not.toContain('csa-row--warn');
    });

    it('lets a settled run stop signalling faults it collected while alive', () => {
        // An engine_dead from Tuesday is a fact to look up, not a condition to
        // report. It stays in the detail row; it leaves the dot and the chip.
        const settled = makeState({
            phase: 'idle',
            stopped: true,
            current_game_id: null,
            alerts: [{ seq: 9, level: 'error', code: 'engine_dead', detail: 'process ended', ts: NOW - 200_000_000 }],
        });
        const markup = renderCsaStatusPanels([settled], NOW);
        expect(markup).toContain('data-status-indicator="completed"');
        expect(markup).not.toContain('class="csa-chip ');
        // Still fully enumerated once the reader asks.
        expect(renderCsaStatusPanels([settled], NOW, new Set([settled.run_id]))).toContain('engine_dead');
    });

    it('counts the faults it does not show rather than listing them all', () => {
        const markup = renderCsaStatusPanels(
            [
                makeState({
                    fallback_plies: [12, 78],
                    alerts: [{ seq: 9, level: 'error', code: 'engine_dead', detail: null, ts: NOW }],
                }),
            ],
            NOW,
        );
        expect(markup).toContain('engine_dead');
        expect(markup).toContain('+1');
    });

    it('lists every fault in full once the row is expanded', () => {
        const state = makeState({
            fallback_plies: [12, 78],
            replay: { is_complete: false, failure: { ply: 5, usi: '9i9h', reason: 'illegal move' } },
        });
        const markup = renderCsaStatusPanels([state], NOW, new Set([state.run_id]));
        expect(markup).toContain('12, 78');
        expect(markup).toContain('ply 5 (9i9h) illegal move');
    });

    it('keeps reproducibility details in the detail row', () => {
        const state = makeState();
        const markup = renderCsaStatusPanels([state], NOW, new Set([state.run_id]));
        expect(markup).toContain('Threads=4');
        expect(markup).toContain(state.run_id);
    });

    it('does not duplicate a completed game result from Games', () => {
        const markup = renderCsaStatusPanels(
            [
                makeState({
                    phase: 'idle',
                    games: [
                        {
                            game_id: 'g1',
                            result: 'win',
                            game_result: 'BLACK_WIN',
                            current_ply: 82,
                            black_name: 'rss-csa-test',
                            white_name: 'haifrz-4',
                            my_color: 'black',
                            terminal: ['%TORYO'],
                            started_ts: NOW - 900_000,
                            ended_ts: NOW - 300_000,
                        },
                    ],
                }),
            ],
            NOW,
        );
        expect(markup).not.toContain('games-result-badge');
        expect(markup).not.toContain('%TORYO');
        expect(markup).toContain('csa-cell__games numeric">1');
    });

    it('shows the run last-update time instead of game start and finish', () => {
        const state = makeState();
        const markup = renderCsaStatusPanels([state], NOW);
        expect(markup).toContain('csa-cell__updated');
        expect(markup).not.toContain('games-started-cell');
        expect(markup).not.toContain('games-ended-cell');
    });

    it('opens the board from the Game cell and keeps Log as the row action', () => {
        const state = makeState();
        const markup = renderCsaStatusPanels([state], NOW);
        expect(markup).toContain('<th scope="col">Current Game</th>');
        expect(markup).toContain('class="csa-cell__current"');
        expect(markup).toContain('data-csa-open-game=');
        expect(markup).toContain('data-csa-wire=');
    });

    it('leaves an unpaired run’s opponent cell inert', () => {
        const markup = renderCsaStatusPanels(
            [makeState({ phase: 'waiting_pairing', opponent_name: null, my_color: null, games: [] })],
            NOW,
        );
        expect(markup).not.toContain('data-csa-open-game=');
    });

    it('does not offer the latest ended game as the current game', () => {
        const markup = renderCsaStatusPanels([makeState({ current_game_id: null, phase: 'idle' })], NOW);
        expect(markup).not.toContain('data-csa-open-game=');
    });

    it('puts running bridges above finished ones', () => {
        const markup = renderCsaStatusPanels(
            [
                makeState({ run_id: '1800000000', phase: 'closing' }),
                makeState({ run_id: '1800001000', worker_idx: 1, phase: 'playing' }),
            ],
            NOW,
        );
        expect(markup.indexOf('1800001000')).toBeLessThan(markup.indexOf('1800000000'));
    });

    /**
     * The row used to be the control. A clickable `<tr>` takes no focus, answers
     * no key and announces nothing, so the log health and alert detail behind it
     * were reachable by mouse only.
     */
    it('expands through a real button that reports its own state', () => {
        const state = makeState();
        const closed = renderCsaStatusPanels([state], NOW);
        expect(closed).toContain('data-csa-toggle=');
        expect(closed).toContain('aria-expanded="false"');
        expect(closed).toContain('aria-controls="csa-detail-1800000000"');

        const open = renderCsaStatusPanels([state], NOW, new Set([state.run_id]));
        expect(open).toContain('aria-expanded="true"');
        // The control points at a row that actually exists.
        expect(open).toContain('id="csa-detail-1800000000"');
    });

    it('scrolls inside its own box rather than pushing the page sideways', () => {
        const markup = renderCsaStatusPanels([makeState()], NOW);
        expect(markup).toContain('csa-table-scroll');
        expect(markup).toContain('<colgroup>');
        expect(markup).toContain('csa-col__session');
    });

    it('escapes current game ids that arrive from a remote server', () => {
        const hostile = '<script>x</script>';
        const state = makeState({ current_game_id: hostile });
        const markup = renderCsaStatusPanels([{ ...state, games: [{ ...state.games[0], game_id: hostile }] }], NOW);
        expect(markup).not.toContain('<script>');
        expect(markup).toContain('&lt;script&gt;');
    });
});
