import { createCsaStatusRenderCache, renderCsaStatusPanels } from '@/modules/csa/components/status-panel';
import {
    type CsaPersistenceStatus,
    type CsaRunStateView,
    type CsaState,
    classifyCsaRunState,
} from '@/modules/csa/types';
import type { ArenaDashboardWindow } from '@/types/globals';

/**
 * CSA watch module.
 *
 * The board, kifu, clocks and evaluation chart are the arena's existing live
 * card, untouched: reusing them is the whole reason the watcher moved here. The
 * only thing this module adds is the panel that answers "is anything wrong".
 */

const PANEL_ELEMENT_ID = 'csaStatusPanel';
const REFRESH_INTERVAL_MS = 250;
const DEFAULT_SETTLED_LIMIT = 20;
const SETTLED_PAGE_SIZE = 50;

export interface CsaModuleApi {
    readonly refresh: () => void;
    readonly stop: () => void;
    readonly readStates: () => CsaState[];
}

function isRecord(value: unknown): value is Record<string, unknown> {
    return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function parsePersistence(value: unknown): CsaPersistenceStatus | null {
    if (!isRecord(value)) return null;
    const state = value.state;
    if (state !== 'pending' && state !== 'persisted' && state !== 'retrying' && state !== 'failed') return null;
    if (typeof value.attempts !== 'number') return null;
    if (value.detail !== null && typeof value.detail !== 'string') return null;
    return { state, attempts: value.attempts, detail: value.detail };
}

/** Quote a run id for use inside an attribute selector. */
function cssEscape(value: string): string {
    return value.replace(/["\\]/g, '\\$&');
}

/**
 * Pull `meta.csa` out of a worker snapshot.
 *
 * The block travels as free-form live-stream metadata, so it arrives with the
 * board data instead of on a stream of its own — one fewer moving part between
 * the fold and the panel.
 */
function isCsaState(value: unknown): value is CsaState {
    if (!isRecord(value)) return false;
    return (
        typeof value.run_id === 'string' &&
        typeof value.worker_idx === 'number' &&
        isRecord(value.ledger) &&
        isRecord(value.log_health) &&
        Array.isArray(value.alerts)
    );
}

export function extractCsaState(snapshot: unknown): CsaState | null {
    if (!isRecord(snapshot)) return null;
    const meta = snapshot.meta;
    if (!isRecord(meta)) return null;
    if (!isCsaState(meta.csa)) return null;
    // Liveness is read as "false unless stated". A snapshot that predates these
    // fields describes a bridge that writes no heartbeats, which is exactly what
    // their absence means — so the default is also the truth.
    const raw = meta.csa;
    return {
        ...meta.csa,
        stream_generation: typeof raw.stream_generation === 'number' ? raw.stream_generation : 0,
        current_game_id: typeof raw.current_game_id === 'string' ? raw.current_game_id : null,
        persistence: parsePersistence(raw.persistence),
        stopped: raw.stopped === true,
        emits_liveness: raw.emits_liveness === true,
        last_event_ts: typeof raw.last_event_ts === 'number' ? raw.last_event_ts : null,
    };
}

/**
 * Build a panel state for a run the summary knows about but no snapshot covers.
 *
 * That is the logged-in-but-not-yet-paired stretch, which is most of a floodgate
 * hour. There is no game, so most of the panel is genuinely empty: the point is
 * to show that the bridge is up, which phase it is in, how long it has been
 * there, and any alert it has already raised. Nothing else is invented.
 */
export function stateFromSummaryRun(entry: Record<string, unknown>): CsaState | null {
    const runId = entry.run_id;
    const workerIdx = entry.worker_idx;
    if (typeof runId !== 'string' || typeof workerIdx !== 'number') return null;
    const alerts = Array.isArray(entry.alert_entries) ? (entry.alert_entries as CsaState['alerts']) : [];
    const health = isRecord(entry.log_health) ? entry.log_health : {};
    return {
        run_id: runId,
        worker_idx: workerIdx,
        stream_generation: typeof entry.stream_generation === 'number' ? entry.stream_generation : 0,
        current_game_id: typeof entry.current_game_id === 'string' ? entry.current_game_id : null,
        persistence: parsePersistence(entry.persistence),
        bridge_version: typeof entry.bridge_version === 'string' ? entry.bridge_version : null,
        // The engine belongs to the run, not to a game, so a run still waiting
        // for a pairing can say what it is holding ready.
        engine_name: typeof entry.engine_name === 'string' ? entry.engine_name : null,
        engine_author: typeof entry.engine_author === 'string' ? entry.engine_author : null,
        engine_options: Array.isArray(entry.engine_options) ? (entry.engine_options as CsaState['engine_options']) : [],
        phase: typeof entry.phase === 'string' ? entry.phase : null,
        phase_since_ts: typeof entry.phase_since_ts === 'number' ? entry.phase_since_ts : null,
        stopped: entry.stopped === true,
        emits_liveness: entry.emits_liveness === true,
        last_event_ts: typeof entry.last_event_ts === 'number' ? entry.last_event_ts : null,
        my_color: null,
        opponent_name: null,
        side_to_move: null,
        alerts,
        outstanding_search: null,
        deadline_ts: null,
        last_move_origin: null,
        fallback_plies: [],
        ledger: { black_ms: null, white_ms: null, as_of_ts: null },
        score: {
            wins: typeof entry.wins === 'number' ? entry.wins : 0,
            losses: typeof entry.losses === 'number' ? entry.losses : 0,
            draws: typeof entry.draws === 'number' ? entry.draws : 0,
        },
        games: Array.isArray(entry.game_entries) ? (entry.game_entries as CsaState['games']) : [],
        replay: { is_complete: true, failure: null },
        log_health: {
            missing_seq: typeof health.missing_seq === 'number' ? health.missing_seq : 0,
            malformed: typeof health.malformed === 'number' ? health.malformed : 0,
            invalid_lines: typeof health.invalid_lines === 'number' ? health.invalid_lines : 0,
            orphan_records: typeof health.orphan_records === 'number' ? health.orphan_records : 0,
            ply_gaps: typeof health.ply_gaps === 'number' ? health.ply_gaps : 0,
            unknown_types: isRecord(health.unknown_types) ? (health.unknown_types as Record<string, number>) : {},
            has_partial_line: health.has_partial_line === true,
            restarts: typeof health.restarts === 'number' ? health.restarts : 0,
        },
    };
}

function collectStates(owner: ArenaDashboardWindow): CsaState[] {
    const coreState = owner.DashboardCore?.state;
    const snapshots = coreState?.workerSnapshots;
    const states: CsaState[] = [];
    const runs = Array.isArray(coreState?.csaRuns) ? coreState.csaRuns : [];
    const summaries = new Map<number, CsaState>();
    for (const entry of runs) {
        if (!isRecord(entry)) continue;
        const summary = stateFromSummaryRun(entry);
        if (summary !== null) summaries.set(summary.worker_idx, summary);
    }
    const covered = new Set<number>();
    if (snapshots instanceof Map) {
        const indices = Array.from(snapshots.keys()).sort((left, right) => Number(left) - Number(right));
        for (const index of indices) {
            const snapshot = extractCsaState(snapshots.get(index));
            if (snapshot !== null) {
                const summary = summaries.get(snapshot.worker_idx);
                if (summary === undefined) {
                    states.push(snapshot);
                } else {
                    const hasCurrentGame = summary.current_game_id !== null;
                    states.push({
                        ...snapshot,
                        ...summary,
                        my_color: hasCurrentGame ? snapshot.my_color : null,
                        opponent_name: hasCurrentGame ? snapshot.opponent_name : null,
                        side_to_move: hasCurrentGame ? snapshot.side_to_move : null,
                        outstanding_search: hasCurrentGame ? snapshot.outstanding_search : null,
                        deadline_ts: hasCurrentGame ? snapshot.deadline_ts : null,
                        replay: hasCurrentGame ? snapshot.replay : summary.replay,
                    });
                }
                covered.add(snapshot.worker_idx);
            }
        }
    }

    // Runs no snapshot covers are the unpaired ones. Drawing them from the
    // summary is what makes "bridge started, waiting for pairing" visible at
    // all; without it the dashboard looks as though nothing is running.
    for (const pending of summaries.values()) {
        if (!covered.has(pending.worker_idx)) states.push(pending);
    }

    states.sort((left, right) => left.worker_idx - right.worker_idx);
    return states;
}

/**
 * Is this run's move deadline a live problem, right now?
 *
 * A deadline only means something while someone is still expected to meet it.
 * `outstanding_search` is whatever search was in flight when the log stopped, so
 * on a run that ended days ago it is frozen in the past and every comparison
 * against `now` reports it as blown — by an ever-growing margin. That produced a
 * red 「着手期限を超過しました」 for runs that had been dead since Tuesday, on a
 * page whose Live View was empty.
 *
 * The gate is the state the table already computes: only a run that is still a
 * live question can have a live deadline.
 */
function hasBlownDeadline(state: CsaState, nowMs: number, view: CsaRunStateView): boolean {
    if (view.kind !== 'playing' || view.stale) return false;
    return state.deadline_ts !== null && nowMs > state.deadline_ts;
}

/** Is this run in a state an operator has to see right now? */
function isUrgent(state: CsaState, nowMs: number): boolean {
    const view = classifyCsaRunState(state, nowMs);
    // Historical alerts belong to history. A settled run keeps them in its
    // detail row and stops flagging the tab, for the same reason it stopped
    // escalating its dot.
    if (
        !view.stale &&
        state.alerts.some(
            (alert) => alert.level === 'error' && (alert.game_id == null || alert.game_id === state.current_game_id),
        )
    )
        return true;
    if (hasBlownDeadline(state, nowMs, view)) return true;
    // A bridge that promised a heartbeat and stopped writing is gone, and the
    // board it left behind will go on looking like a game in progress. Only
    // while the incident is fresh: an interruption about a run that died an hour
    // ago is noise, and the run stays in the table either way.
    return view.kind === 'abnormal' && !view.stale;
}

export function installCsaModule(owner: ArenaDashboardWindow): CsaModuleApi {
    const documentRef = owner.document ?? (typeof document !== 'undefined' ? document : null);
    const panel = documentRef?.getElementById(PANEL_ELEMENT_ID) ?? null;
    if (panel) {
        panel.removeAttribute('hidden');
    }

    let lastMarkup = '';
    // The newest markup, withheld because the reader has focus inside the table.
    let pendingMarkup: string | null = null;
    let timerId: ReturnType<typeof setInterval> | null = null;
    // Which rows the reader has opened. Held here rather than in the DOM because
    // the table is re-rendered wholesale whenever its text changes.
    const expandedRuns = new Set<string>();
    let settledLimit = DEFAULT_SETTLED_LIMIT;
    const renderCache = createCsaStatusRenderCache();
    // Announce each fault once. Without this the 250ms tick would fire the same
    // notice continuously and train the operator to ignore it.
    const announced = new Set<string>();
    // Alerts already in the log when the page opened are history, not news. They
    // stay in the panel and still flag the tab; interrupting with a stack of them
    // on load is the clutter this layout exists to remove.
    //
    // This is a timestamp cutoff rather than a "skip the first pass" flag,
    // because worker snapshots arrive over the socket after install — an old
    // alert can first become visible several ticks in and would otherwise look
    // brand new.
    const openedAt = Date.now();

    /**
     * Faults have to reach someone looking at the board, not just someone who
     * happens to have this tab open — a panel behind a tab is not an alert.
     * Only the urgent two interrupt; the rest of the monitoring stays here.
     */
    function announceUrgent(states: CsaState[], nowMs: number): void {
        const notify = owner.DashboardShowNotice;
        for (const state of states) {
            const view = classifyCsaRunState(state, nowMs);
            for (const alert of state.alerts) {
                if (alert.level !== 'error') continue;
                // A run keeps every game's alerts for audit, but only run-level
                // alerts and the current game's alerts describe what is wrong
                // now. Re-announcing game 1 while game 2 is being played turns
                // history into a false live incident.
                if (alert.game_id != null && alert.game_id !== state.current_game_id) continue;
                const key = `${state.run_id}:alert:${alert.seq}`;
                if (announced.has(key)) continue;
                announced.add(key);
                if (alert.ts !== null && alert.ts < openedAt) continue;
                notify?.(`CSA ${state.run_id}: ${alert.code}${alert.detail ? ` — ${alert.detail}` : ''}`, 'error');
            }
            if (!hasBlownDeadline(state, nowMs, view)) continue;
            // Identity is the deadline itself, not the run and ply.
            //
            // `run_id + ply` is not unique: a run plays several games, and ply 42
            // of the second game reuses the key from ply 42 of the first — so a
            // real overdue move in a later game was suppressed by an unrelated
            // one hours earlier. The game and the deadline timestamp together
            // name exactly one search.
            const game = state.games.length ? state.games[state.games.length - 1].game_id : state.run_id;
            const key = `${game}:overdue:${state.outstanding_search?.ply ?? 'x'}:${state.deadline_ts}`;
            if (announced.has(key)) continue;
            announced.add(key);
            // Blown before the page opened is history, exactly as it is for
            // alerts: the panel still shows it, but it does not interrupt.
            if (state.deadline_ts !== null && state.deadline_ts < openedAt) continue;
            notify?.(`CSA ${state.run_id}: move deadline exceeded`, 'error');
        }
    }

    /**
     * Is the reader's keyboard focus somewhere inside the table right now?
     *
     * Swapping `innerHTML` destroys every element under it, including the one
     * with focus — so a reader tabbing to a row's actions had the button pulled
     * out from under them on the next tick, which arrives every 250ms. Nothing
     * about the panel is worth that.
     */
    function holdsFocus(): boolean {
        const active = documentRef?.activeElement ?? null;
        return active !== null && panel !== null && panel.contains(active);
    }

    function refresh(): void {
        if (!panel) return;
        const now = Date.now();
        const states = collectStates(owner);

        // Deliberately before the focus check: an interruption is not a repaint.
        // A fault that fires while the reader happens to be tabbing through the
        // table still has to reach them, and the tab flag is a class toggle on an
        // element the table does not own, so neither is affected by deferral.
        announceUrgent(states, now);
        const tabButton = documentRef?.getElementById('tabCsa') ?? null;
        tabButton?.classList.toggle(
            'dashboard-tab--attention',
            states.some((state) => isUrgent(state, now)),
        );

        const markup = renderCsaStatusPanels(states, now, expandedRuns, settledLimit, renderCache);
        // Only touch the DOM when the text actually changed, so the table does
        // not fight the board for the main thread. Now that the countdown and
        // the ticking estimates are gone, a settled table rarely changes at all.
        if (markup === lastMarkup) return;
        if (holdsFocus()) {
            // Hold the newest markup rather than dropping it: whenever focus
            // leaves, the reader gets the current table, not the one from the
            // moment they tabbed in.
            pendingMarkup = markup;
            return;
        }
        pendingMarkup = null;
        lastMarkup = markup;
        panel.innerHTML = markup;
    }

    /** Apply whatever was held back while the reader was inside the table. */
    function flushPending(): void {
        if (pendingMarkup === null || !panel || holdsFocus()) return;
        lastMarkup = pendingMarkup;
        pendingMarkup = null;
        panel.innerHTML = lastMarkup;
    }

    /**
     * Every action on a row is a real button, and nothing else is clickable.
     *
     * The row itself used to toggle the detail. A clickable `<tr>` takes no
     * focus, answers no key and announces nothing, so the log health and alert
     * detail behind it were mouse-only — and every cell was a hair-trigger for a
     * reader who meant to select text.
     */
    function onPanelClick(event: Event): void {
        const target = event.target as HTMLElement | null;

        const more = target?.closest?.('[data-csa-more]') as HTMLElement | null;
        if (more) {
            settledLimit += SETTLED_PAGE_SIZE;
            lastMarkup = '';
            refresh();
            return;
        }

        const openGame = target?.closest?.('[data-csa-open-game]') as HTMLElement | null;
        if (openGame) {
            const gameId = openGame.dataset.csaOpenGame;
            if (gameId) {
                void owner.DashboardNavigation?.openGame?.(gameId, {
                    source: 'csa-monitor',
                    preferArchived: openGame.dataset.csaArchived === 'true',
                });
            }
            return;
        }

        const wire = target?.closest?.('[data-csa-wire]') as HTMLElement | null;
        if (wire) {
            const runId = wire.dataset.csaWire;
            // The browser is already an adequate viewer for a plain-text
            // protocol log, and a bespoke log viewer is the "original UI" habit
            // this panel was just rebuked for.
            if (runId) owner.open?.(`/api/csa/runs/${encodeURIComponent(runId)}/wire`, '_blank', 'noopener');
            return;
        }

        const toggle = target?.closest?.('[data-csa-toggle]') as HTMLElement | null;
        const runId = toggle?.dataset?.csaToggle;
        if (!runId) return;
        if (expandedRuns.has(runId)) {
            expandedRuns.delete(runId);
        } else {
            expandedRuns.add(runId);
        }
        // The reader asked for this one, so it applies even though their focus is
        // on the button that asked. Re-render, then put focus back where it was:
        // the markup is replaced wholesale, so the button they pressed is a new
        // element.
        lastMarkup = '';
        pendingMarkup = null;
        if (panel) {
            const markup = renderCsaStatusPanels(
                collectStates(owner),
                Date.now(),
                expandedRuns,
                settledLimit,
                renderCache,
            );
            lastMarkup = markup;
            panel.innerHTML = markup;
            const restored = panel.querySelector<HTMLElement>(`[data-csa-toggle="${cssEscape(runId)}"]`);
            restored?.focus();
        }
    }

    // `focusout` fires before focus lands, so the check has to happen after the
    // browser has moved it — otherwise `holdsFocus` still sees the old element
    // and the pending markup waits another tick for no reason.
    function onPanelFocusOut(): void {
        setTimeout(flushPending, 0);
    }

    function stop(): void {
        if (timerId !== null) {
            clearInterval(timerId);
            timerId = null;
        }
        panel?.removeEventListener('click', onPanelClick);
        panel?.removeEventListener('focusout', onPanelFocusOut);
    }

    panel?.addEventListener('click', onPanelClick);
    panel?.addEventListener('focusout', onPanelFocusOut);
    refresh();
    if (typeof setInterval === 'function') {
        timerId = setInterval(refresh, REFRESH_INTERVAL_MS);
    }

    return {
        refresh,
        stop,
        readStates: () => collectStates(owner),
    };
}
