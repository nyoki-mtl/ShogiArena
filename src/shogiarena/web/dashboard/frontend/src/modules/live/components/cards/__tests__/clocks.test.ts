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
});
