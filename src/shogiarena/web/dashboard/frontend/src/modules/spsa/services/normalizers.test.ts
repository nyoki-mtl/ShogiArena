import { describe, expect, it } from 'vitest';
import { normalizeSpsaSummary, normalizeSpsaUpdateDetail } from './normalizers';

const baseSummary = {
    wins: 1,
    losses: 1,
    draws: 0,
    games: {
        total: 2,
        completed: 2,
    },
};

describe('normalizeSpsaSummary', () => {
    it('throws when legacy snake_case fields are present', () => {
        expect(() => normalizeSpsaSummary({ ...baseSummary, engine_meta: {} } as never)).toThrow(/engine_meta/);
        expect(() => normalizeSpsaSummary({ ...baseSummary, num_updates: 2 } as never)).toThrow(/num_updates/);
    });

    it('accepts a minimal modern payload', () => {
        const result = normalizeSpsaSummary(baseSummary as never);
        expect(result.wins).toBe(1);
        expect(result.updatesCompleted).toBe(2);
    });

    it('rejects detail meta camelCase includes', () => {
        const detailPayload = {
            update_idx: 1,
            engines: { baseline: null, tuned: null },
            wdl: { wins: 0, losses: 0, draws: 0 },
            variant_id: null,
            params: {},
            gradients: {},
            deltas: {},
            s_plus: null,
            s_minus: null,
            step: null,
            games: [],
            games_count: 0,
            meta: {
                availableIncludes: ['params'],
                loadedIncludes: ['params'],
            },
        } as never;
        expect(() => normalizeSpsaUpdateDetail(detailPayload)).toThrow(/availableIncludes/);
    });
});
