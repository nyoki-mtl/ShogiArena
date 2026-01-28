import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { __testHandleEnvelope, __testHasSnapshot, __testResetState } from '../liveMergeWorker';

describe('liveMergeWorker gid retention', () => {
    beforeEach(() => {
        __testResetState();
        (globalThis as unknown as { postMessage?: unknown }).postMessage = vi.fn();
    });
    afterEach(() => {
        __testResetState();
        (globalThis as unknown as { postMessage?: unknown }).postMessage = undefined;
    });

    it('drops snapshots for gids no longer referenced by any worker', () => {
        __testHandleEnvelope({
            topic: 'live.assignment.snapshot',
            seq: 1,
            payload: {
                assignments: { '0': 'g1' },
                gids: ['g1'],
                updatedAt: 0,
            },
        });

        expect(__testHasSnapshot('g1')).toBe(true);

        __testHandleEnvelope({
            topic: 'live.assignment.snapshot',
            seq: 2,
            payload: {
                assignments: { '0': 'g2' },
                gids: ['g2'],
                updatedAt: 0,
            },
        });

        expect(__testHasSnapshot('g1')).toBe(false);
        expect(__testHasSnapshot('g2')).toBe(true);

        // Late/stale diffs for an unassigned gid should not recreate in-memory snapshots.
        __testHandleEnvelope({
            topic: 'live.game.g1.moves.diff',
            seq: 10,
            payload: { gid: 'g1', kind: 'move', patch: { currentPly: 1, move: '7g7f' } },
        });
        expect(__testHasSnapshot('g1')).toBe(false);
    });
});
