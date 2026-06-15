import { describe, expect, it } from 'vitest';

import { __testApplyPatch } from '../live-merge-worker';
import type { WorkerSnapshotRecord } from '@/modules/live/types/updates';

const asSnapshot = (value: unknown): WorkerSnapshotRecord => value as WorkerSnapshotRecord;

describe('liveMergeWorker applyPatch', () => {
    it('merges plain fields', () => {
        const base = asSnapshot({ a: 1, b: 2 });
        const patch = { b: 3, c: 4 };
        const next = __testApplyPatch(base, patch);
        expect(next).toEqual({ a: 1, b: 3, c: 4 });
        expect(base).toEqual({ a: 1, b: 2 }); // immutability
    });

    it('applies set op with shallow path', () => {
        const base = asSnapshot({ a: 1 });
        const patch = { __apply__: { op: 'set', path: ['x'], value: 9 } };
        const next = __testApplyPatch(base, patch);
        expect(next).toEqual({ a: 1, x: 9 });
    });

    it('applies delete op with shallow path', () => {
        const base = asSnapshot({ keep: 1, drop: 2 });
        const patch = { __apply__: { op: 'delete', path: ['drop'] } };
        const next = __testApplyPatch(base, patch);
        expect(next).toEqual({ keep: 1 });
    });

    it('applies set op with two-level path by cloning child', () => {
        const base = asSnapshot({ a: { nested: 1 }, untouched: 9 });
        const patch = { __apply__: { op: 'set', path: ['a', 'nested'], value: 5 } };
        const next = __testApplyPatch(base, patch);
        expect(next).toEqual({ a: { nested: 5 }, untouched: 9 });
        expect(base).toEqual({ a: { nested: 1 }, untouched: 9 }); // immutability of child
    });

    it('applies delete op with two-level path', () => {
        const base = asSnapshot({ a: { nested: 1, keep: 2 } });
        const patch = { __apply__: { op: 'delete', path: ['a', 'nested'] } };
        const next = __testApplyPatch(base, patch);
        expect(next).toEqual({ a: { keep: 2 } });
        expect(base).toEqual({ a: { nested: 1, keep: 2 } });
    });

    it('applies set op with three-level path (deep recursion)', () => {
        const base = asSnapshot({ state: { clock: { black_ms: 1000 } }, untouched: 9 });
        const patch = { __apply__: { op: 'set', path: ['state', 'clock', 'black_ms'], value: 750 } };
        const next = __testApplyPatch(base, patch);
        expect(next).toEqual({ state: { clock: { black_ms: 750 } }, untouched: 9 });
        // source is untouched at every level
        expect(base).toEqual({ state: { clock: { black_ms: 1000 } }, untouched: 9 });
    });

    it('applies delete op with three-level path (deep recursion)', () => {
        const base = asSnapshot({ state: { clock: { black_ms: 1000, white_ms: 2000 } } });
        const patch = { __apply__: { op: 'delete', path: ['state', 'clock', 'black_ms'] } };
        const next = __testApplyPatch(base, patch);
        expect(next).toEqual({ state: { clock: { white_ms: 2000 } } });
    });

    it('creates intermediate objects for a deep path when missing', () => {
        const base = asSnapshot({ a: 1 });
        const patch = { __apply__: { op: 'set', path: ['x', 'y', 'z'], value: 7 } };
        const next = __testApplyPatch(base, patch);
        expect(next).toEqual({ a: 1, x: { y: { z: 7 } } });
    });

    it('ignores malformed op', () => {
        const base = asSnapshot({ a: 1 });
        const patch = { __apply__: { op: 'set', path: [] } };
        const next = __testApplyPatch(base, patch);
        expect(next).toEqual(base);
    });
});
