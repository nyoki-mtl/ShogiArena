import { describe, expect, it } from 'vitest';
import { normalizeSummaryPayload } from './summary';

describe('normalizeSummaryPayload', () => {
    it('normalizes a valid payload', () => {
        const normalized = normalizeSummaryPayload({
            gamesCompleted: 12,
            gamesScheduled: 30,
            defaultTimeControl: 'byoyomi 10000',
            engineTimeControls: { EngineA: 'byoyomi 20000' },
            liveView: {
                version: 1,
                mode: 'tournament',
                progress: {
                    kind: 'games',
                    completed: 12,
                    total: 30,
                    unitLabel: 'games',
                },
            },
        });

        expect(normalized.gamesCompleted).toBe(12);
        expect(normalized.defaultTimeControl).toBe('byoyomi 10000');
    });

    it('throws when payload is missing', () => {
        expect(() => normalizeSummaryPayload(null)).toThrow('summary payload is missing');
    });

    it('throws when engineTimeControls is not an object', () => {
        expect(() =>
            normalizeSummaryPayload({
                engineTimeControls: 'invalid',
            } as never),
        ).toThrow('summary payload engineTimeControls must be an object');
    });

    it('throws when numeric field is invalid', () => {
        expect(() =>
            normalizeSummaryPayload({
                gamesCompleted: '12',
            } as never),
        ).toThrow('summary.gamesCompleted: expected finite number');
    });
});
