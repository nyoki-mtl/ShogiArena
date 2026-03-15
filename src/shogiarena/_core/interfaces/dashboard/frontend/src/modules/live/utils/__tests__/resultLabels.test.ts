import { describe, expect, it } from 'vitest';
import { resultCodeToKifJP } from '@/modules/live/utils';

describe('resultCodeToKifJP', () => {
    it('maps max plies and impasse to distinct labels', () => {
        expect(resultCodeToKifJP({ game_result: 'DRAW_BY_MAX_PLIES' })).toBe('最大手数');
        expect(resultCodeToKifJP({ game_result: 'DRAW_BY_IMPASSE' })).toBe('持将棋');
    });

    it('prefers game_result labels when available', () => {
        expect(resultCodeToKifJP({ game_result: 'BLACK_WIN' })).toBe('投了');
        expect(resultCodeToKifJP({ game_result: 'ERROR' })).toBe('エラー');
    });

    it('does not fall back to numeric result codes', () => {
        expect(resultCodeToKifJP()).toBe('結果不明');
        expect(resultCodeToKifJP({})).toBe('結果不明');
    });
});
