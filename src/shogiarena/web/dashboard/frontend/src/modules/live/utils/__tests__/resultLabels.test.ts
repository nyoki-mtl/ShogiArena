import { describe, expect, it } from 'vitest';
import { resultCodeToKifJP } from '@/modules/live/utils';

describe('resultCodeToKifJP', () => {
    it('maps max plies and impasse to distinct labels', () => {
        expect(resultCodeToKifJP(6)).toBe('最大手数');
        expect(resultCodeToKifJP(10)).toBe('持将棋');
    });

    it('keeps legacy code-6 hint fallback when available', () => {
        expect(resultCodeToKifJP(6, { end_reason: 'max_plies' })).toBe('最大手数');
        expect(resultCodeToKifJP(6, { end_reason: 'jishogi' })).toBe('持将棋');
    });

    it('keeps existing labels for non-ambiguous results', () => {
        expect(resultCodeToKifJP(2)).toBe('千日手');
        expect(resultCodeToKifJP(16)).toBe('時間切れ負け');
    });
});
