import { describe, expect, it } from 'vitest';
import { normalizeSpsaSummary, normalizeSpsaUpdates, normalizeSpsaUpdateDetail } from './normalizers';

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
    it('accepts a minimal modern payload', () => {
        const result = normalizeSpsaSummary(baseSummary as never);
        expect(result.wins).toBe(1);
        expect(result.updatesCompleted).toBe(2);
    });

    it('normalizes numeric fields to zero on missing values', () => {
        const result = normalizeSpsaSummary({ wins: 0, losses: 0, draws: 0, games: {} } as never);
        expect(result.wins).toBe(0);
        expect(result.losses).toBe(0);
        expect(result.numUpdates).toBe(0);
    });
});

describe('normalizeSpsaUpdates', () => {
    it('normalizes canonical update entries', () => {
        const entries = [
            {
                update_idx: 1,
                gradients: { p1: 0.1 },
                deltas: { p1: 0.5 },
                delta_norm: 0.3,
                is_pending: false,
            },
            {
                update_idx: 0,
                gradients: {},
                deltas: {},
                delta_norm: 0.1,
                is_pending: true,
            },
        ];
        const result = normalizeSpsaUpdates(entries);
        expect(result).toHaveLength(2);
        // Sorted by updateIdx descending
        expect(result[0].updateIdx).toBe(1);
        expect(result[1].updateIdx).toBe(0);
        expect(result[0].isPending).toBe(false);
        expect(result[1].isPending).toBe(true);
    });

    it('reads is_ltc_rejected from canonical field', () => {
        const entries = [{ update_idx: 0, is_ltc_rejected: true }];
        const result = normalizeSpsaUpdates(entries);
        expect(result[0].ltcRejected).toBe(true);
    });

    it('defaults is_pending to false when absent', () => {
        const entries = [{ update_idx: 0 }];
        const result = normalizeSpsaUpdates(entries);
        expect(result[0].isPending).toBe(false);
    });

    it('defaults is_ltc_rejected to false when absent', () => {
        const entries = [{ update_idx: 0 }];
        const result = normalizeSpsaUpdates(entries);
        expect(result[0].ltcRejected).toBe(false);
    });
});

describe('normalizeSpsaUpdateDetail', () => {
    it('normalizes a canonical detail payload', () => {
        const detailPayload = {
            update_idx: 1,
            engines: { baseline: 'a', tuned: 'b' },
            wdl: { wins: 3, losses: 1, draws: 2 },
            variant_id: 'v1',
            params: {},
            gradients: {},
            deltas: {},
            s_plus: null,
            s_minus: null,
            step: null,
            games: [],
            games_count: 0,
            meta: {
                available_includes: ['variant_games'],
                loaded_includes: ['variant_games'],
            },
        } as never;
        const result = normalizeSpsaUpdateDetail(detailPayload);
        expect(result.updateIdx).toBe(1);
        expect(result.wdl.wins).toBe(3);
        expect(result.loadedIncludes).toEqual(['variant_games']);
    });

    it('reads is_pending from canonical field', () => {
        const detailPayload = {
            update_idx: 1,
            engines: { baseline: 'a', tuned: 'b' },
            wdl: { wins: 0, losses: 0, draws: 0 },
            params: {},
            games: [],
            games_count: 0,
            is_pending: true,
        } as never;
        const result = normalizeSpsaUpdateDetail(detailPayload);
        expect(result.isPending).toBe(true);
    });
});
