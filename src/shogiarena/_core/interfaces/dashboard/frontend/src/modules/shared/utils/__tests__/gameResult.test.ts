import { describe, expect, it } from 'vitest';

import {
    gameResultAbbreviation,
    gameResultCategory,
    gameResultDetailKey,
    gameResultLabel,
    gameResultScores,
    gameResultWinner,
} from '@/modules/shared/utils/gameResult';

describe('gameResult helpers', () => {
    it('maps canonical names to labels', () => {
        expect(gameResultLabel('BLACK_WIN')).toBe('投了');
        expect(gameResultLabel('DRAW_BY_IMPASSE')).toBe('持将棋');
    });

    it('derives category, winner, and scores from game_result', () => {
        expect(gameResultCategory('WHITE_WIN_BY_TIMEOUT')).toBe(1);
        expect(gameResultWinner('WHITE_WIN_BY_TIMEOUT')).toBe('white');
        expect(gameResultScores('DRAW_BY_REPETITION')).toEqual({ black: 0.5, white: 0.5 });
    });

    it('treats paused and invalid values as non-scoring', () => {
        expect(gameResultCategory('PAUSED')).toBe(3);
        expect(gameResultWinner('PAUSED')).toBe('error');
        expect(gameResultScores('PAUSED')).toEqual({ black: null, white: null });
        expect(gameResultLabel('UNKNOWN_RESULT')).toBeNull();
    });

    it('derives detail keys and abbreviations for non-canonical legacy display fields', () => {
        expect(gameResultDetailKey('DRAW_BY_MAX_PLIES')).toBe('max plies');
        expect(gameResultAbbreviation('DRAW_BY_MAX_PLIES')).toBe('M');
        expect(gameResultDetailKey('BLACK_WIN_BY_TIMEOUT')).toBe('timeout');
        expect(gameResultAbbreviation('BLACK_WIN_BY_TIMEOUT')).toBe('T');
        expect(gameResultDetailKey('BLACK_WIN')).toBeNull();
        expect(gameResultAbbreviation('BLACK_WIN')).toBeNull();
    });
});
