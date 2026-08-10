import { describe, expect, it } from 'vitest';

import { renderCsaStatusPanels } from '@/modules/csa/components/status-panel';
import { stateFromSummaryRun } from '@/modules/csa/index';

/**
 * A bridge that is logged in but not yet paired owns no game, so it publishes no
 * worker snapshot. Without a summary-derived fallback the dashboard shows
 * nothing at all for it — and on floodgate that is most of every hour, so an
 * operator who just started the bridge would read "nothing running" as a fault.
 */
describe('pending run drawn from the summary', () => {
    const entry = {
        run_id: '1785921938',
        worker_idx: 3,
        phase: 'waiting_pairing',
        phase_since_ts: 1_785_921_939_000,
        wins: 1,
        losses: 0,
        draws: 0,
        games: 1,
        alerts: 1,
        bridge_version: '0.1.0',
        alert_entries: [{ seq: 12, level: 'warn', code: 'engine_restarted', detail: 'restarting the engine', ts: 1 }],
    };

    it('keeps what the summary actually knows', () => {
        const state = stateFromSummaryRun(entry);
        expect(state).not.toBeNull();
        expect(state?.run_id).toBe('1785921938');
        expect(state?.worker_idx).toBe(3);
        expect(state?.phase).toBe('waiting_pairing');
        expect(state?.phase_since_ts).toBe(1_785_921_939_000);
        expect(state?.score).toEqual({ wins: 1, losses: 0, draws: 0 });
        expect(state?.alerts).toHaveLength(1);
    });

    it('invents no side of the board and no opponent', () => {
        const state = stateFromSummaryRun(entry);
        expect(state?.my_color).toBeNull();
        expect(state?.opponent_name).toBeNull();
        expect(state?.games).toEqual([]);
        expect(state?.ledger).toEqual({ black_ms: null, white_ms: null, as_of_ts: null });
        expect(state?.deadline_ts).toBeNull();
    });

    it('rejects an entry that does not identify a run', () => {
        expect(stateFromSummaryRun({ worker_idx: 1 })).toBeNull();
        expect(stateFromSummaryRun({ run_id: 'r' })).toBeNull();
    });

    it('renders as waiting rather than claiming a 後手 that does not exist', () => {
        const state = stateFromSummaryRun({ ...entry, last_event_ts: Date.now() });
        const markup = renderCsaStatusPanels(state === null ? [] : [state], Date.now());
        expect(markup).toContain('Waiting for pairing');
        expect(markup).not.toContain('Opponent');
    });

    /**
     * Liveness is the whole reason a killed bridge can be told from a long think,
     * and the summary is the only description of a run that owns no game — so
     * dropping these three fields here would make the distinction unreachable
     * during exactly the stretch it matters most.
     */
    it('carries the liveness facts the bridge published', () => {
        const state = stateFromSummaryRun({
            ...entry,
            stopped: true,
            emits_liveness: true,
            last_event_ts: 1_785_921_940_000,
        });
        expect(state?.stopped).toBe(true);
        expect(state?.emits_liveness).toBe(true);
        expect(state?.last_event_ts).toBe(1_785_921_940_000);
    });

    it('reads a run that says nothing about liveness as one that writes none', () => {
        const state = stateFromSummaryRun(entry);
        expect(state?.stopped).toBe(false);
        expect(state?.emits_liveness).toBe(false);
        expect(state?.last_event_ts).toBeNull();
    });
});
