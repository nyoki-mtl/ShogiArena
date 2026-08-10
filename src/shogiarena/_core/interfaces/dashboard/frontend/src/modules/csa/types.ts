/**
 * The CSA-only state that rides along with a worker snapshot as `meta.csa`.
 *
 * Every field here answers "has something gone wrong", which is the reason to
 * watch a CSA game at all. The arena's own view model has no slot for any of it.
 */

/**
 * Phases in which a bridge is still working.
 *
 * Every phase except an observed `bridge_stop` belongs to a live run. Whether a
 * board exists is decided separately by `current_game_id`.
 */
export const CSA_RUNNING_PHASES: ReadonlySet<string> = new Set([
    'warming',
    'connecting',
    'idle',
    'waiting_pairing',
    'agreed',
    'playing',
    'closing',
]);

export function isCsaRunningPhase(phase: unknown): boolean {
    return typeof phase === 'string' && CSA_RUNNING_PHASES.has(phase);
}

/**
 * Silence long enough to call a heartbeat-writing bridge dead.
 *
 * The writer's interval is fixed at 60s and deliberately not configurable, so a
 * reader may depend on it. Two and a half intervals absorbs a slow disk or a
 * late poll without absorbing a death.
 */
const UNRESPONSIVE_AFTER_MS = 150_000;

/**
 * Silence long enough to be worth reporting for a bridge that writes no
 * heartbeats. All that can honestly be said of such a run is how long it has
 * been quiet — an old bridge waiting for a pairing writes nothing either.
 */
const NO_UPDATE_AFTER_MS = 180_000;

/**
 * Silence past which a run is history rather than a question.
 *
 * Bounded structurally, not by taste: floodgate pairs on a 30-minute cycle, so
 * no single legitimate quiet stretch — one long think, one opponent think, one
 * whole pairing wait — plausibly runs past it. A run from two days ago misses by
 * two orders of magnitude.
 *
 * The same constant does two jobs, which is the point of naming it once. It
 * stops a corpse from earning a board (see `deservesLiveBoard`), and it stops
 * the same corpse from shouting in the table forever.
 */
const PRESUMED_DEAD_MS = 30 * 60_000;

/**
 * The lifecycle of a run, as four labels.
 *
 * Phase and liveness are separate facts in the log, but an operator reads one
 * state, not two. Liveness wins the label — a bridge that was killed while
 * playing must not go on claiming 対局中 — and the phase it died in survives in
 * the detail row. Faults are the third axis and never reach this list: they
 * move the dot, they do not rename the state.
 *
 * Everything before the first move is 対局待ち. `warming` and `connecting` last
 * seconds and `agreed` lasts seconds; splitting them bought three labels for one
 * fact, and the dwell time carries what mattered — 「対局待ち 27m」 still convicts
 * a missed :30 pairing.
 */
export type CsaRunStateKind = 'waiting' | 'playing' | 'finished' | 'abnormal';

export interface CsaRunStateView {
    readonly kind: CsaRunStateKind;
    readonly label: string;
    /** The Games tab's indicator vocabulary, plus the two severities it lacks. */
    readonly indicator: 'pending' | 'running' | 'completed' | 'cancelled' | 'warn' | 'error';
    /** Set when the state itself is the problem, not merely the context of one. */
    readonly severity: 'error' | 'warn' | null;
    /** Time spent in this state, or the length of the silence for the silent ones. */
    readonly elapsedMs: number | null;
    /**
     * This run is history, not a question the operator still has.
     *
     * A table with standing coloured rows teaches the operator to ignore the
     * colour. An incident is loud; the history of it is quiet. The label is the
     * same in both phases — receding changes how hard it knocks, never what it
     * says.
     */
    readonly stale: boolean;
    /** The evidence behind the label, for the cell's tooltip. Never shown inline. */
    readonly note: string | null;
}

/** The facts a run's state is decided from. */
export type CsaLivenessFacts = Pick<
    CsaState,
    'phase' | 'phase_since_ts' | 'stopped' | 'emits_liveness' | 'last_event_ts' | 'current_game_id'
>;

function minutesAgo(silenceMs: number): string {
    const minutes = Math.floor(silenceMs / 60_000);
    if (minutes < 60) return `${Math.max(1, minutes)}m ago`;
    const hours = Math.floor(minutes / 60);
    if (hours < 24) return `${hours}h ago`;
    return `${Math.floor(hours / 24)}d ago`;
}

export function classifyCsaRunState(state: CsaLivenessFacts, nowMs: number): CsaRunStateView {
    const dwell = state.phase_since_ts === null ? null : nowMs - state.phase_since_ts;
    if (state.stopped) {
        return {
            kind: 'finished',
            label: 'Finished',
            indicator: 'completed',
            severity: null,
            elapsedMs: null,
            stale: true,
            note: null,
        };
    }

    const silence = state.last_event_ts === null ? null : nowMs - state.last_event_ts;
    const playing = state.phase === 'playing';
    const phaseLabel =
        state.phase === 'idle'
            ? 'Waiting for next game'
            : state.phase === 'closing'
              ? 'Shutting down'
              : playing
                ? 'Playing'
                : 'Waiting for pairing';

    /*
     * 異常終了 — it ended, and not by the protocol's front door.
     *
     * "Never claim an end you did not observe" forbids fabricating observations,
     * and a broken promise is not a fabrication. A bridge that writes heartbeats
     * committed to a record every 60s; the absence of one past the threshold
     * means the process that would have written it is not running. That is a
     * diagnosis, not a guess.
     *
     * A bridge that never made the promise gets the label only past the
     * 30-minute bound, where no legitimate silence exists either — and by then
     * the incident is already history, which is why it arrives receded.
     */
    if (silence !== null) {
        const dead = state.emits_liveness ? silence > UNRESPONSIVE_AFTER_MS : silence > PRESUMED_DEAD_MS;
        if (dead) {
            const note = state.emits_liveness
                ? `Last event ${minutesAgo(silence)}`
                : `Last event ${minutesAgo(silence)} / no game-end record`;
            const aged = silence > PRESUMED_DEAD_MS;
            return {
                kind: 'abnormal',
                label: 'Terminated',
                indicator: aged ? 'cancelled' : 'error',
                // Loud for the first half hour, then quiet. The toast already
                // interrupted at the moment of death; a row that stays red
                // forever only teaches the operator to stop seeing red.
                severity: aged ? null : 'error',
                elapsedMs: silence,
                stale: aged,
                note,
            };
        }

        /*
         * The legacy transient: a bridge with no heartbeat, quiet longer than a
         * heartbeat bridge would be but not yet long enough to convict.
         *
         * All that can honestly be said is how long it has been quiet, so the
         * label stays the phase and the uncertainty rides on the dot and the
         * tooltip. Reachable only from logs written before the bridge learned to
         * write heartbeats; when those age out the branch goes silent forever.
         */
        if (!state.emits_liveness && silence > NO_UPDATE_AFTER_MS) {
            return {
                kind: playing ? 'playing' : 'waiting',
                label: phaseLabel,
                indicator: 'pending',
                severity: 'warn',
                elapsedMs: dwell,
                stale: false,
                note: `No updates ${minutesAgo(silence)}`,
            };
        }
    }

    return {
        kind: playing ? 'playing' : 'waiting',
        label: phaseLabel,
        indicator: playing ? 'running' : 'pending',
        severity: null,
        elapsedMs: dwell,
        stale: false,
        note: null,
    };
}

/**
 * Should this run be given a board in Live View?
 *
 * Creation and retirement are deliberately asymmetric, and the asymmetry is the
 * whole point. **Retirement is a promise about something already on screen;
 * creation is an inference about liveness.** The cardinal rule — never hide a
 * game that might be running — protects the operator from the page *withdrawing*
 * a truth it was showing, so silence must never retire a board. But a run whose
 * log went quiet two days ago was never shown: declining to infer that it is
 * live is not concealment, it is refusing to fabricate.
 *
 * Without this, a log directory full of externally killed runs — each stuck in
 * `playing` forever, because a killed bridge writes no terminal record — opens
 * Live View with a wall of boards from days ago, which is exactly the failure
 * this profile's redesign exists to kill.
 *
 * Evaluated on every summary, so a run that comes back to life earns its board
 * on the next record it writes. Sharing `classifyCsaRunState` is deliberate:
 * a run is denied a board precisely when the monitoring table has stopped
 * treating it as a live question, so the two views cannot drift.
 */
export function deservesLiveBoard(state: CsaLivenessFacts, nowMs: number): boolean {
    const view = classifyCsaRunState(state, nowMs);
    return view.kind !== 'finished' && view.kind !== 'abnormal';
}

export type CsaAlertLevel = 'error' | 'warn' | string;

export interface CsaAlert {
    readonly seq: number;
    readonly level: CsaAlertLevel;
    readonly code: string;
    readonly detail: string | null;
    readonly ts: number | null;
    readonly game_id?: string | null;
}

export interface CsaPersistenceStatus {
    readonly state: 'pending' | 'persisted' | 'retrying' | 'failed';
    readonly attempts: number;
    readonly detail: string | null;
}

export interface CsaOutstandingSearch {
    readonly kind: string;
    readonly ply: number;
    readonly deadline_ts: number | null;
    readonly predicted_usi: string | null;
}

export interface CsaLedgerClocks {
    readonly black_ms: number | null;
    readonly white_ms: number | null;
    readonly as_of_ts: number | null;
}

export interface CsaLogHealth {
    readonly missing_seq: number;
    readonly malformed: number;
    readonly invalid_lines: number;
    readonly orphan_records: number;
    readonly ply_gaps: number;
    readonly unknown_types: Record<string, number>;
    readonly has_partial_line: boolean;
    readonly restarts: number;
}

export interface CsaGameEntry {
    readonly game_id: string;
    readonly result: string | null;
    /**
     * The same outcome in the arena's absolute vocabulary (`BLACK_WIN`, …).
     *
     * `result` states who won from our side; the arena states which colour won.
     * The producer already owns that mapping, so the monitoring table renders
     * with the arena's own result badge instead of re-deriving it.
     */
    readonly game_result: string | null;
    readonly current_ply: number;
    readonly black_name: string;
    readonly white_name: string;
    readonly my_color: 'black' | 'white';
    readonly terminal: readonly string[];
    readonly started_ts: number | null;
    readonly ended_ts: number | null;
    readonly persistence?: CsaPersistenceStatus | null;
}

export interface CsaReplayFailure {
    readonly ply: number;
    readonly usi: string;
    readonly reason: string;
}

export interface CsaReplayStatus {
    readonly is_complete: boolean;
    readonly failure: CsaReplayFailure | null;
}

export interface CsaScore {
    readonly wins: number;
    readonly losses: number;
    readonly draws: number;
}

export interface CsaState {
    readonly run_id: string;
    readonly worker_idx: number;
    readonly stream_generation: number;
    readonly current_game_id: string | null;
    readonly persistence: CsaPersistenceStatus | null;
    readonly bridge_version: string | null;
    // What the engine said it was during the USI handshake, and the options it
    // was actually given. `bridge_version` is the bridge's own and says nothing
    // about what played the game; without these a run is not reproducible.
    readonly engine_name: string | null;
    readonly engine_author: string | null;
    readonly engine_options: readonly { readonly name: string; readonly value: string }[];
    readonly phase: string | null;
    readonly phase_since_ts: number | null;
    // Liveness as three facts rather than one verdict. `stopped` means the
    // bridge said it was done; `emits_liveness` means this bridge writes
    // heartbeats, so silence is evidence of death rather than of a long think.
    readonly stopped: boolean;
    readonly emits_liveness: boolean;
    readonly last_event_ts: number | null;
    // Null while the bridge is logged in but not yet paired: there is no game,
    // hence no side of the board and nobody to name as the opponent.
    readonly my_color: 'black' | 'white' | null;
    readonly opponent_name: string | null;
    readonly side_to_move: 'black' | 'white' | null;
    readonly alerts: readonly CsaAlert[];
    readonly outstanding_search: CsaOutstandingSearch | null;
    readonly deadline_ts: number | null;
    readonly last_move_origin: string | null;
    readonly fallback_plies: readonly number[];
    readonly ledger: CsaLedgerClocks;
    readonly score: CsaScore;
    readonly games: readonly CsaGameEntry[];
    readonly replay: CsaReplayStatus;
    readonly log_health: CsaLogHealth;
}
