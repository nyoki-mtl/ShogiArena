import { describe, expect, it } from 'vitest';

import { normalizeSummaryEventPayload } from '@/modules/live/components/summary/contract-guard';

import golden from './fixtures/summary-golden.json';

/**
 * Cross-language contract check for the summary the page boots on.
 *
 * The fixture is emitted by `build_summary` (pinned in
 * `tests/unit/test_csa_live_publisher.py`) and read here unmodified, then run
 * through the *real* boot guard rather than a description of it.
 *
 * This exists because the guard once rejected the CSA summary for a missing
 * `live_view` while every Python assertion about that same summary passed, and
 * the rejection was fatal: the whole dashboard rendered blank. Agreeing on one
 * artifact is what makes that drift a failing test instead of an empty page.
 */
describe('CSA summary -> dashboard boot guard', () => {
    it('is accepted by the guard the page boots with', () => {
        expect(() => normalizeSummaryEventPayload(golden)).not.toThrow();
    });

    it('carries a progress block with completed and total', () => {
        const normalized = normalizeSummaryEventPayload(golden) as unknown as {
            live_view?: { progress?: Record<string, unknown> };
        };
        const progress = normalized.live_view?.progress;
        expect(progress).toBeTruthy();
        expect(typeof progress?.completed).toBe('number');
        expect(typeof progress?.total).toBe('number');
    });

    it('names a mode the frontend knows, so it is not silently degraded', () => {
        // 'csa' had to be added to LiveViewMode: an unknown mode falls back to
        // 'unknown', which happened to still render only by way of the progress
        // kind. That is the same producer/consumer gap this file guards.
        const normalized = normalizeSummaryEventPayload(golden) as unknown as {
            live_view?: { mode?: string };
        };
        expect(normalized.live_view?.mode).toBe('csa');
    });

    it('preserves both games, their own colors, and the absence of a current board', () => {
        const runs = golden.csa_runs;
        expect(runs).toHaveLength(1);
        expect(runs[0]?.current_game_id).toBeNull();
        expect(runs[0]?.stream_generation).toBe(0);
        expect(runs[0]?.game_entries).toMatchObject([{ my_color: 'black' }, { my_color: 'white' }]);
        const alerts = runs[0]?.alert_entries ?? [];
        expect(alerts.some((alert) => alert.game_id === null)).toBe(true);
        expect(alerts.some((alert) => typeof alert.game_id === 'string')).toBe(true);
    });
});
