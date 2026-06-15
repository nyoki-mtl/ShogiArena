import { describe, expect, it } from 'vitest';
import type { SpsaUpdateDetailResponse } from '@/modules/spsa/types';
import { parseDetailBatchEnvelope } from './detail-stream';

const sampleDetail: SpsaUpdateDetailResponse = {
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
};

describe('parseDetailBatchEnvelope', () => {
    it('accepts a valid detail_batch envelope', () => {
        const result = parseDetailBatchEnvelope({ type: 'detail_batch', details: [sampleDetail] });
        expect(result.details).toHaveLength(1);
        expect(result.details[0]).toEqual(sampleDetail);
    });

    it('rejects legacy single detail payloads', () => {
        expect(() => parseDetailBatchEnvelope({ detail: sampleDetail })).toThrow(/detail_batch/);
    });

    it('rejects envelopes without an array of details', () => {
        expect(() => parseDetailBatchEnvelope({ type: 'detail_batch', details: sampleDetail })).toThrow(
            /array 'details'/,
        );
    });

    it('rejects non-object payloads', () => {
        expect(() => parseDetailBatchEnvelope(null)).toThrow(/payload must be an object/);
        expect(() => parseDetailBatchEnvelope('oops')).toThrow(/payload must be an object/);
    });

    it('rejects unexpected envelope types', () => {
        expect(() => parseDetailBatchEnvelope({ type: 'detail', details: [sampleDetail] })).toThrow(/detail_batch/);
    });
});
