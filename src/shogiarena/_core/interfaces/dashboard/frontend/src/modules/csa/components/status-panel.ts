import { type CsaAlert, type CsaState, classifyCsaRunState } from '@/modules/csa/types';
import { formatTimestamp } from '@/modules/games/viewmodel-format';

/**
 * The one CSA-specific component: a table of bridge runs.
 *
 * Built to the Games tab's visual idiom, but focused on run-level questions:
 * whether the bridge is alive, its current game, cumulative count and score,
 * and when it last changed.
 *
 * Two rules decide what is here. Every column answers one of the questions an
 * operator actually has — is it alive, what is it doing, how much has it played,
 * and when did it last change. A healthy run is silent: no chips, no tick,
 * nothing but its state and current game. What is wrong shows up as colour on
 * the row and a single chip; the rest waits in the detail row.
 *
 * Clocks and ponder are not here. The board card reconstructs the same clocks
 * from the same `,T` echoes, and a ponder hit rate is analysis rather than
 * monitoring.
 */

/** Below this many milliseconds the move deadline is close enough to warn about. */
export const DEADLINE_CAUTION_MS = 10_000;

export type DeadlineSeverity = 'normal' | 'caution' | 'overdue';

export function classifyDeadline(deadlineTs: number | null, nowMs: number): DeadlineSeverity {
    if (deadlineTs === null) return 'normal';
    const remaining = deadlineTs - nowMs;
    if (remaining < 0) return 'overdue';
    if (remaining <= DEADLINE_CAUTION_MS) return 'caution';
    return 'normal';
}

export function formatClockMs(value: number | null): string {
    if (value === null || !Number.isFinite(value)) return '--:--';
    const totalSeconds = Math.floor(Math.max(0, value) / 1000);
    const hours = Math.floor(totalSeconds / 3600);
    const minutes = Math.floor((totalSeconds % 3600) / 60);
    const seconds = totalSeconds % 60;
    const paddedSeconds = String(seconds).padStart(2, '0');
    if (hours > 0) return `${hours}:${String(minutes).padStart(2, '0')}:${paddedSeconds}`;
    return `${minutes}:${paddedSeconds}`;
}

export function formatDurationMs(value: number | null): string {
    if (value === null || !Number.isFinite(value)) return '—';
    const totalSeconds = Math.floor(Math.max(0, value) / 1000);
    const hours = Math.floor(totalSeconds / 3600);
    const minutes = Math.floor((totalSeconds % 3600) / 60);
    const seconds = totalSeconds % 60;
    if (hours > 0) return `${hours}h ${String(minutes).padStart(2, '0')}m`;
    if (minutes > 0) return `${minutes}m`;
    return `${seconds}s`;
}

/** Errors stay pinned above warnings; within a level the newest record wins. */
export function orderAlerts(alerts: readonly CsaAlert[]): CsaAlert[] {
    return [...alerts].sort((left, right) => {
        const leftRank = left.level === 'error' ? 0 : 1;
        const rightRank = right.level === 'error' ? 0 : 1;
        if (leftRank !== rightRank) return leftRank - rightRank;
        return right.seq - left.seq;
    });
}

function escapeHtml(value: string): string {
    return value
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

export type FaultLevel = 'error' | 'warn';

export interface CsaFault {
    readonly level: FaultLevel;
    /** What goes on the chip. Short enough to read without stopping. */
    readonly label: string;
    /** What goes in the detail row. */
    readonly detail: string | null;
}

/** Only the things that are wrong. A clean log produces an empty list. */
function logHealthIssues(state: CsaState): string[] {
    const health = state.log_health;
    const parts: string[] = [];
    if (health.missing_seq) parts.push(`missing seq ${health.missing_seq}`);
    if (health.malformed) parts.push(`malformed payload ${health.malformed}`);
    if (health.invalid_lines) parts.push(`invalid lines ${health.invalid_lines}`);
    if (health.orphan_records) parts.push(`orphan records ${health.orphan_records}`);
    if (health.ply_gaps) parts.push(`ply gaps ${health.ply_gaps}`);
    for (const [name, count] of Object.entries(health.unknown_types)) {
        parts.push(`unknown ${name} x${count}`);
    }
    if (health.has_partial_line) parts.push('partial final line');
    if (health.restarts) parts.push(`log replaced ${health.restarts}x`);
    return parts;
}

/**
 * Everything wrong with this run, worst first.
 *
 * Faults never rename the state — a run that is playing with a dead engine is
 * still playing. They colour the row, put one chip beside the state, and list
 * themselves in full only when the reader opens the row.
 */
export function collectFaults(state: CsaState, nowMs: number, isLive = true): CsaFault[] {
    const faults: CsaFault[] = [];

    for (const alert of orderAlerts(state.alerts)) {
        if (alert.game_id != null && alert.game_id !== state.current_game_id) continue;
        faults.push({
            level: alert.level === 'error' ? 'error' : 'warn',
            label: alert.code,
            detail: alert.detail,
        });
    }

    // A move deadline only means anything while someone is still expected to
    // meet it. On a run that ended two days ago it reads 「期限超過 37h」, which
    // is not a fault report but a clock difference.
    const deadline = isLive ? (state.outstanding_search?.deadline_ts ?? null) : null;
    const severity = classifyDeadline(deadline, nowMs);
    if (deadline !== null && severity !== 'normal') {
        const remaining = deadline - nowMs;
        faults.push(
            severity === 'overdue'
                ? { level: 'error', label: `Overdue ${formatDurationMs(-remaining)}`, detail: null }
                : { level: 'warn', label: `Due ${(remaining / 1000).toFixed(1)}s`, detail: null },
        );
    }

    if (!state.replay.is_complete) {
        const failure = state.replay.failure;
        faults.push({
            level: 'error',
            label: 'Replay failed',
            detail: failure === null ? null : `ply ${failure.ply} (${failure.usi}) ${failure.reason}`,
        });
    }

    if (state.fallback_plies.length) {
        faults.push({ level: 'warn', label: 'PV fallback', detail: state.fallback_plies.join(', ') });
    }

    const persistence = state.persistence ?? latestGame(state)?.persistence ?? null;
    if (persistence?.state === 'retrying') {
        faults.push({ level: 'warn', label: 'DB retry', detail: persistence.detail });
    } else if (persistence?.state === 'failed') {
        faults.push({ level: 'error', label: 'DB write failed', detail: persistence.detail });
    }

    const health = logHealthIssues(state);
    if (health.length) {
        faults.push({ level: 'warn', label: 'Log integrity', detail: health.join(', ') });
    }

    return faults.sort((left, right) => (left.level === right.level ? 0 : left.level === 'error' ? -1 : 1));
}

type RowLevel = 'error' | 'warn' | 'ok';

function highestLevel(stateSeverity: FaultLevel | null, faults: readonly CsaFault[]): RowLevel {
    if (stateSeverity === 'error' || faults.some((fault) => fault.level === 'error')) return 'error';
    if (stateSeverity === 'warn' || faults.length) return 'warn';
    return 'ok';
}

function latestGame(state: CsaState): CsaState['games'][number] | null {
    return state.games.length ? state.games[state.games.length - 1] : null;
}

export interface CsaStatusRenderCache {
    settledRows: Map<string, { key: string; markup: string }>;
}

export function createCsaStatusRenderCache(): CsaStatusRenderCache {
    return {
        settledRows: new Map(),
    };
}

/** Absolute times, formatted by the Games tab's own formatter. */
function renderTimestampCell(ts: number | null): string {
    const stamp = formatTimestamp(ts === null ? null : new Date(ts).toISOString());
    if (stamp.label === null) return '<td class="csa-cell__updated games-time-empty">—</td>';
    return `<td class="csa-cell__updated" title="${escapeHtml(stamp.title ?? '')}">${escapeHtml(stamp.label)}</td>`;
}

export function renderCsaStatusPanels(
    states: readonly CsaState[],
    nowMs: number,
    expanded: ReadonlySet<string> = new Set(),
    settledLimit = 20,
    cache?: CsaStatusRenderCache,
): string {
    if (!states.length) {
        return `<div class="csa-empty">No event logs</div>`;
    }

    // Live questions first; history stays visible but recedes. A watched log
    // directory only ever grows, so the layout has to answer "which of these
    // needs me" without the reader scrolling.
    const ordered = [...states].sort((left, right) => {
        const leftRank = classifyCsaRunState(left, nowMs).stale ? 1 : 0;
        const rightRank = classifyCsaRunState(right, nowMs).stale ? 1 : 0;
        if (leftRank !== rightRank) return leftRank - rightRank;
        return right.run_id.localeCompare(left.run_id);
    });

    const active = ordered.filter((state) => !classifyCsaRunState(state, nowMs).stale);
    const settled = ordered.filter((state) => classifyCsaRunState(state, nowMs).stale);
    const visible = [...active, ...settled.slice(0, settledLimit)];
    const visibleIds = new Set(visible.map((state) => state.run_id));
    if (cache !== undefined) {
        for (const runId of cache.settledRows.keys()) {
            if (!visibleIds.has(runId)) cache.settledRows.delete(runId);
        }
    }
    const rows = visible
        .map((state) => {
            const view = classifyCsaRunState(state, nowMs);
            const isExpanded = expanded.has(state.run_id);
            if (!view.stale || cache === undefined) return renderRow(state, nowMs, isExpanded);
            const key = `${isExpanded ? '1' : '0'}\u0000${JSON.stringify(state)}`;
            const cached = cache.settledRows.get(state.run_id);
            if (cached?.key === key) return cached.markup;
            const markup = renderRow(state, nowMs, isExpanded);
            cache.settledRows.set(state.run_id, { key, markup });
            return markup;
        })
        .join('');
    const remaining = Math.max(0, settled.length - settledLimit);
    const more =
        remaining > 0
            ? `<button type="button" class="csa-load-more" data-csa-more>Show more (${remaining})</button>`
            : '';
    // `table-simple variant-games-table` is how the spsa tab hosts this table.
    // Following that rather than inventing a table is the whole point: the app
    // has one table, and a second one that merely resembles it is worse than
    // either, because it invites the comparison and then loses it.
    // Wrapped so the table scrolls inside its own box rather than pushing the
    // page sideways. At 390px the actions column sat off-screen with no way to
    // reach it.
    return `<div class="csa-table-scroll"><table class="table-simple variant-games-table csa-table">
        <colgroup>
            <col class="csa-col__session">
            <col class="csa-col__state">
            <col class="csa-col__current">
            <col class="csa-col__games">
            <col class="csa-col__record">
            <col class="csa-col__updated">
            <col class="csa-col__actions">
        </colgroup>
        <thead>
            <tr>
                <th scope="col">Session</th>
                <th scope="col">State</th>
                <th scope="col">Current Game</th>
                <th scope="col">Games</th>
                <th scope="col">W-L-D</th>
                <th scope="col">Last Update</th>
                <th scope="col">Log</th>
            </tr>
        </thead>
        <tbody>${rows}</tbody>
    </table>${more}</div>`;
}

const COLUMN_COUNT = 7;

/**
 * One side of the board.
 *
 * Our own engine is marked with the Games tab's per-engine annotation slot — the
 * instance pill — and the opponent gets the same em-dash placeholder that tab
 * uses when there is no pill. The name itself is text: wiring it to
 * `games-engine-link` would open tournament matchup views that do not exist in
 * this profile, and a dead button in familiar styling is worse than no button.
 */
function renderRow(state: CsaState, nowMs: number, isExpanded: boolean): string {
    const view = classifyCsaRunState(state, nowMs);
    const faults = collectFaults(state, nowMs, !view.stale);
    const currentGame = state.current_game_id
        ? (state.games.find((game) => game.game_id === state.current_game_id) ?? null)
        : null;

    /*
     * A fault only *signals* while the run is still a live question.
     *
     * An `engine_dead` from Tuesday is a fact to look up, not a condition to
     * report: escalating the dot for it made the runs that mattered least the
     * loudest things on screen. Settled runs keep every fault in the detail row,
     * where history belongs, and show none of it in the row itself.
     */
    const signals = view.stale ? [] : faults;
    const level = highestLevel(view.severity, signals);
    const indicator = level === 'error' ? 'error' : level === 'warn' ? 'warn' : view.indicator;

    const worst = signals[0] ?? null;
    const extra = signals.length > 1 ? `<span class="csa-cell__muted"> +${signals.length - 1}</span>` : '';
    const chip =
        worst === null
            ? ''
            : ` <span class="csa-chip csa-chip--${worst.level}">${escapeHtml(worst.label)}</span>${extra}`;
    const stateNote =
        view.note === null ? '' : ` <span class="csa-chip csa-chip--warn">${escapeHtml(view.note)}</span>`;

    // No severity wash. A tint is an *area* signal, so it scales with row count
    // rather than importance — four settled runs drown the one that is live. The
    // Games tab never tints by severity either; its only row treatment is
    // receding what has settled, and one coloured dot in an otherwise monochrome
    // table is louder than four coloured rows.
    const classes = ['csa-row', view.stale ? 'csa-row--done' : 'csa-row--running', isExpanded ? 'csa-row--open' : '']
        .filter(Boolean)
        .join(' ');

    // A real button, because the row used to be the control: clickable `<tr>`
    // elements take no focus, answer no key, and announce nothing, so the log
    // health and the alert detail behind them were reachable by mouse only.
    const detailId = detailRowId(state.run_id);
    const toggle = `<button type="button" class="csa-toggle" data-csa-toggle="${escapeHtml(state.run_id)}"
        aria-expanded="${isExpanded}" aria-controls="${detailId}"
        aria-label="${escapeHtml(`Details for ${state.run_id}`)}"><span aria-hidden="true">${isExpanded ? '▾' : '▸'}</span></button>`;

    const stateCell = `<span class="csa-result-state" title="${escapeHtml(view.label)}"><span class="games-status-indicator csa-indicator" data-status-indicator="${indicator}" aria-hidden="true"></span><span>${escapeHtml(view.label)}</span>${stateNote}${chip}</span>`;
    const currentGameCell =
        currentGame === null
            ? '<span class="games-time-empty">—</span>'
            : `<button type="button" class="csa-game-link" data-csa-open-game="${escapeHtml(currentGame.game_id)}">${escapeHtml(currentGame.game_id)}</button>`;

    return `<tr class="${classes}" data-csa-run="${escapeHtml(state.run_id)}" data-state="${view.kind}" title="${escapeHtml(state.run_id)}">
        <td class="csa-cell__session"><div class="csa-game-cell">${toggle}<span class="csa-session-label">${escapeHtml(state.run_id)}</span></div></td>
        <td class="csa-cell__state">${stateCell}</td>
        <td class="csa-cell__current" title="${escapeHtml(currentGame?.game_id ?? '')}">${currentGameCell}</td>
        <td class="csa-cell__games numeric">${state.games.length}</td>
        <td class="csa-cell__record numeric">${state.score.wins}-${state.score.losses}-${state.score.draws}</td>
        ${renderTimestampCell(state.last_event_ts)}
        <td class="csa-cell__actions"><button type="button" class="games-settings-trigger" data-csa-wire="${escapeHtml(state.run_id)}">Log</button></td>
    </tr>${isExpanded ? renderDetailRow(state, view.label, faults, detailId) : ''}`;
}

/** A DOM id the toggle can point `aria-controls` at. */
export function detailRowId(runId: string): string {
    return `csa-detail-${runId.replace(/[^A-Za-z0-9_-]/g, '_')}`;
}

function definition(term: string, value: string): string {
    return `<div class="csa-detail__row"><dt>${escapeHtml(term)}</dt><dd>${value}</dd></div>`;
}

/**
 * The long form: every fault in full, plus the instruments that are not worth a
 * permanent column.
 *
 * Every line is a label and a value. A line that needs a sentence to explain its
 * value means the value is wrong.
 */
function renderDetailRow(state: CsaState, stateLabel: string, faults: readonly CsaFault[], detailId: string): string {
    const sections: string[] = [];

    const faultRows = faults
        .map(
            (fault) =>
                `<div class="csa-detail__row csa-detail__row--${fault.level}"><dt>${escapeHtml(fault.label)}</dt><dd>${
                    fault.detail === null ? '' : escapeHtml(fault.detail)
                }</dd></div>`,
        )
        .join('');
    if (faultRows) sections.push(`<dl class="csa-detail__group">${faultRows}</dl>`);

    const measures = [
        definition(
            'Status',
            `${escapeHtml(stateLabel)} <span class="csa-cell__muted">${escapeHtml(state.phase ?? '—')}</span>`,
        ),
        definition(
            'Engine',
            state.engine_name === null
                ? '—'
                : `${escapeHtml(state.engine_name)}${
                      state.engine_author === null
                          ? ''
                          : ` <span class="csa-cell__muted">${escapeHtml(state.engine_author)}</span>`
                  }`,
        ),
        // Not decoration: `Threads` and `USI_Hash` change what the engine plays,
        // so a run is not reproducible without them.
        definition(
            'Options',
            state.engine_options.length === 0
                ? '—'
                : escapeHtml(state.engine_options.map((option) => `${option.name}=${option.value}`).join(' ')),
        ),
        definition('Run', escapeHtml(state.run_id)),
        definition('Bridge', state.bridge_version === null ? '—' : escapeHtml(state.bridge_version)),
    ].join('');
    sections.push(`<dl class="csa-detail__group">${measures}</dl>`);

    return `<tr class="csa-detail" id="${detailId}"><td colspan="${COLUMN_COUNT}">${sections.join('')}</td></tr>`;
}
