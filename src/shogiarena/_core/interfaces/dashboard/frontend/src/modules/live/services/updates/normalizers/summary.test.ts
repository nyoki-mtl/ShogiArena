import { describe, expect, it } from 'vitest';
import { normalizeSummaryPayload } from './summary';

describe('normalizeSummaryPayload', () => {
    it('normalizes a valid payload', () => {
        const normalized = normalizeSummaryPayload({
            games_completed: 12,
            games_scheduled: 30,
            default_time_control: 'byoyomi 10000',
            engine_time_controls: { EngineA: 'byoyomi 20000' },
            live_view: {
                version: 1,
                mode: 'tournament',
                progress: {
                    kind: 'games',
                    completed: 12,
                    total: 30,
                    unit_label: 'games',
                },
            },
        });

        expect(normalized.games_completed).toBe(12);
        expect(normalized.default_time_control).toBe('byoyomi 10000');
    });

    it('throws when payload is missing', () => {
        expect(() => normalizeSummaryPayload(null)).toThrow('summary payload is missing');
    });

    it('throws when engineTimeControls is not an object', () => {
        expect(() =>
            normalizeSummaryPayload({
                engine_time_controls: 'invalid',
            } as never),
        ).toThrow('summary payload engine_time_controls must be an object');
    });

    it('throws when numeric field is invalid', () => {
        expect(() =>
            normalizeSummaryPayload({
                games_completed: '12',
            } as never),
        ).toThrow('summary.games_completed: expected finite number');
    });
});
