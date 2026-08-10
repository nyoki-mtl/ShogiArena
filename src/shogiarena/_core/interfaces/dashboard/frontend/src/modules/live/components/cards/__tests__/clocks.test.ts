import { describe, expect, it } from 'vitest';
import { computeByoyomiRemainMs, computeRunningClockDisplay } from '@/modules/live/components/cards/clocks';

describe('live card clock helpers', () => {
    it('keeps consuming only main time while main time remains', () => {
        const clock = computeRunningClockDisplay(5_000, 10_000, 4_000);
        expect(clock.mainRemainMs).toBe(1_000);
        expect(clock.byoyomiRemainMs).toBeNull();
    });

    it('consumes byoyomi only after main time is exhausted', () => {
        const clock = computeRunningClockDisplay(3_000, 10_000, 4_000);
        expect(clock.mainRemainMs).toBe(0);
        expect(clock.byoyomiRemainMs).toBe(9_000);
    });

    it('computes post-move byoyomi from elapsed and pre-move remain', () => {
        const remain = computeByoyomiRemainMs(10_000, 4_000, 3_000);
        expect(remain).toBe(9_000);
    });

    // CSA games carry a ledger value reconstructed from the server's `,T` echoes.
    // When the machine that wrote the log runs ahead of the viewer, the elapsed term
    // goes negative and an unclamped estimate grows without bound; a real display of
    // 2368:07 was produced this way. A running clock can never hold more time than
    // the authoritative value it started from.
    it('never displays more time than the authoritative remaining value', () => {
        const ledgerMs = 63_000;
        const elapsedFromAFutureTimestamp = -8_500_000_000;
        const clock = computeRunningClockDisplay(ledgerMs, 0, elapsedFromAFutureTimestamp);
        expect(clock.mainRemainMs).toBe(ledgerMs);
        expect(clock.mainRemainMs).toBeLessThanOrEqual(ledgerMs);
    });
});
