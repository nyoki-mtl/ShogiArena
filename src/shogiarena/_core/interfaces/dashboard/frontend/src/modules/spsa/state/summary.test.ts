import { describe, expect, it } from 'vitest';
import { summaryStore } from '@/store';
import { normalizeSpsaSummary } from '../services/normalizers';
import { resolveSummary } from './summary';

/**
 * backend は SPSA summary のエンジンメタを `engines_meta`（**配列**）で返す。
 * frontend の正規化は `engine_meta`（オブジェクト）を読むため、
 * `NormalizedSpsaSummary.engineMeta` は常に空になる。
 *
 * `applySpsaSummary` はストアの SPSA データを丸ごと置換するので、
 * ここで raw を渡さないと、起動時に `engines_meta` から入っていた
 * エンジン詳細が最初のリフレッシュで消える。
 */
const RAW_SUMMARY = {
    mode: 'spsa',
    wins: 10,
    losses: 8,
    draws: 1,
    engines: ['YaneuraOu search parameters'],
    engines_meta: [
        {
            name: 'YaneuraOu search parameters',
            engine_path: 'C:/engines/YaneuraOu.exe',
            merged_options: { Threads: 1, USI_Hash: 16 },
        },
    ],
    engine_time_controls: {},
    engine_instances: {},
};

describe('SPSA summary -> unified store', () => {
    it('keeps engine metadata that only exists in the raw payload', () => {
        summaryStore.applySpsaSummary(RAW_SUMMARY as never);
        const bootstrapped = summaryStore.getState().spsa?.engineMeta ?? {};
        expect(Object.keys(bootstrapped)).toContain('YaneuraOu search parameters');

        // 通常のリフレッシュ経路。ここで raw を落とすとエンジンメタが消える。
        resolveSummary(normalizeSpsaSummary(RAW_SUMMARY));

        const afterRefresh = summaryStore.getState().spsa?.engineMeta ?? {};
        expect(Object.keys(afterRefresh)).toContain('YaneuraOu search parameters');
        expect(afterRefresh['YaneuraOu search parameters']).toMatchObject({
            engine_path: 'C:/engines/YaneuraOu.exe',
        });
    });
});
