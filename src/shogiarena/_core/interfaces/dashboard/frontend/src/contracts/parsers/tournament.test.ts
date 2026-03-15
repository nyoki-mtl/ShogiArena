import { describe, expect, it } from 'vitest';
import { parseTournamentSummary } from './tournament';

describe('tournament parser', () => {
    it('rejects non-object tournament summary payload', () => {
        expect(() => parseTournamentSummary('invalid')).toThrow();
    });

    it('accepts a modern tournament summary payload', () => {
        const payload = {
            engines: ['A', 'B'],
            engineStats: {
                A: { wins: 1, draws: 0, losses: 1 },
                B: { wins: 1, draws: 1, losses: 0 },
            },
            games: { completed: 2, total: 4, cancelled: 0 },
            engineInstances: {},
        };
        const parsed = parseTournamentSummary(payload);
        expect(parsed.engines).toEqual(['A', 'B']);
        expect(parsed.games?.completed).toBe(2);
    });
});
