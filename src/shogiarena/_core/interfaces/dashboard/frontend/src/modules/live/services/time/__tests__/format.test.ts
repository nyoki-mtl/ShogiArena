import { describe, expect, it } from 'vitest';
import { formatByoyomi, formatCountUp, formatRemain } from '@/modules/live/services/time/format';

describe('live time format', () => {
    it('formats remain time in clock style with second ceiling', () => {
        expect(formatRemain(59_001)).toBe('1:00');
        expect(formatRemain(0)).toBe('0:00');
        expect(formatRemain(3_600_000)).toBe('1:00:00');
    });

    it('formats byoyomi as integer seconds', () => {
        expect(formatByoyomi(9_001)).toBe('10');
        expect(formatByoyomi(0)).toBe('0');
    });

    it('formats count-up time without fractional seconds', () => {
        expect(formatCountUp(90_500)).toBe('01:30');
        expect(formatCountUp(3_661_000)).toBe('1:01:01');
    });
});
