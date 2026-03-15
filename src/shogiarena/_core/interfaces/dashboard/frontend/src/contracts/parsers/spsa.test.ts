import { describe, expect, it } from 'vitest';
import {
    parseSpsaParamsResponse,
    parseSpsaSummaryResponse,
    parseSpsaUpdateDetailResponse,
    parseSpsaUpdateEntries,
} from './spsa';

describe('spsa parsers', () => {
    describe('parseSpsaSummaryResponse', () => {
        it('accepts a canonical summary payload', () => {
            const payload = { wins: 10, losses: 5, draws: 3 };
            const result = parseSpsaSummaryResponse(payload);
            expect(result.wins).toBe(10);
            expect(result.losses).toBe(5);
            expect(result.draws).toBe(3);
        });

        it('rejects non-object summary payload', () => {
            expect(() => parseSpsaSummaryResponse(123)).toThrow();
        });

        it('rejects payload missing required wins field', () => {
            expect(() => parseSpsaSummaryResponse({ losses: 0, draws: 0 })).toThrow();
        });

        it('rejects payload with non-numeric wins', () => {
            expect(() => parseSpsaSummaryResponse({ wins: 'ten', losses: 0, draws: 0 })).toThrow();
        });

        it('passes through unknown canonical-compatible keys', () => {
            const payload = { wins: 1, losses: 0, draws: 0, some_new_field: 'value' };
            const result = parseSpsaSummaryResponse(payload);
            expect(result.wins).toBe(1);
        });
    });

    describe('parseSpsaUpdateEntries', () => {
        it('accepts canonical update entries', () => {
            const entries = [{ update_idx: 0 }, { update_idx: 1 }];
            const result = parseSpsaUpdateEntries(entries);
            expect(result).toHaveLength(2);
            expect(result[0].update_idx).toBe(0);
        });

        it('rejects non-array updates payload', () => {
            expect(() => parseSpsaUpdateEntries('invalid')).toThrow();
        });

        it('rejects entry missing update_idx', () => {
            expect(() => parseSpsaUpdateEntries([{ variant_id: 'v1' }])).toThrow();
        });

        it('accepts entry with canonical is_pending', () => {
            const entries = [{ update_idx: 0, is_pending: false }];
            const result = parseSpsaUpdateEntries(entries);
            expect(result[0].update_idx).toBe(0);
        });

        it('accepts entry with canonical gradients and deltas', () => {
            const entries = [{ update_idx: 0, gradients: { p: 0.1 }, deltas: { p: 0.5 } }];
            const result = parseSpsaUpdateEntries(entries);
            expect(result[0].update_idx).toBe(0);
        });
    });

    describe('parseSpsaUpdateDetailResponse', () => {
        const validDetail = {
            update_idx: 1,
            engines: { baseline: 'A', tuned: 'B' },
            wdl: { wins: 1, losses: 0, draws: 0 },
            variant_id: 'v1',
            params: {},
            gradients: {},
            deltas: {},
            s_plus: 0,
            s_minus: 0,
            step: 0,
            games: [],
            games_count: 1,
        };

        it('accepts valid detail payload', () => {
            expect(parseSpsaUpdateDetailResponse(validDetail)).toMatchObject({ update_idx: 1 });
        });

        it('rejects detail missing update_idx', () => {
            const { update_idx: _, ...rest } = validDetail;
            expect(() => parseSpsaUpdateDetailResponse(rest)).toThrow();
        });

        it('rejects detail missing engines', () => {
            const { engines: _, ...rest } = validDetail;
            expect(() => parseSpsaUpdateDetailResponse(rest)).toThrow();
        });

        it('rejects detail missing wdl', () => {
            const { wdl: _, ...rest } = validDetail;
            expect(() => parseSpsaUpdateDetailResponse(rest)).toThrow();
        });

        it('rejects detail missing games', () => {
            const { games: _, ...rest } = validDetail;
            expect(() => parseSpsaUpdateDetailResponse(rest)).toThrow();
        });

        it('rejects detail missing games_count', () => {
            const { games_count: _, ...rest } = validDetail;
            expect(() => parseSpsaUpdateDetailResponse(rest)).toThrow();
        });
    });

    describe('parseSpsaParamsResponse', () => {
        it('accepts valid params payload', () => {
            const payload = {
                params: [],
                variant_id: 'v1',
                num_params: 1,
                num_used: 1,
                num_clamped: 0,
                clamped_ratio: 0,
                initial_params: {},
                diffs: {},
            };
            expect(parseSpsaParamsResponse(payload)).toMatchObject({ num_params: 1 });
        });

        it('rejects params missing required num_params', () => {
            expect(() =>
                parseSpsaParamsResponse({
                    params: [],
                    num_used: 0,
                    num_clamped: 0,
                    clamped_ratio: null,
                }),
            ).toThrow();
        });

        it('rejects params missing required params array', () => {
            expect(() =>
                parseSpsaParamsResponse({
                    num_params: 0,
                    num_used: 0,
                    num_clamped: 0,
                    clamped_ratio: null,
                }),
            ).toThrow();
        });
    });
});
