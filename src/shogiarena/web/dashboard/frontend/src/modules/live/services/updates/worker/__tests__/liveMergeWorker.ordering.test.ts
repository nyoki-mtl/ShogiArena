import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { __testHandleEnvelope, __testResetState } from '../liveMergeWorker';

describe('liveMergeWorker ordering (jitter buffer)', () => {
    beforeEach(() => {
        __testResetState();
        (globalThis as unknown as { postMessage?: unknown }).postMessage = vi.fn();
    });
    afterEach(() => {
        __testResetState();
        (globalThis as unknown as { postMessage?: unknown }).postMessage = undefined;
    });

    const postMessageMock = () => (globalThis as unknown as { postMessage: ReturnType<typeof vi.fn> }).postMessage;

    const assignWorker = (workerIdx: number, gid: string, seq = 1) => {
        __testHandleEnvelope({
            topic: 'live.assignment.snapshot',
            seq,
            payload: { assignments: { [String(workerIdx)]: gid }, gids: [gid], updatedAt: 0 },
        });
    };

    const sendDiff = (gid: string, seq: number, ply: number) => {
        __testHandleEnvelope({
            topic: `live.game.${gid}.moves.diff`,
            seq,
            payload: { gid, kind: 'move', patch: { currentPly: ply, move: `m${ply}` } },
        });
    };

    it('requests a snapshot on seq gaps and does not apply future move diffs', () => {
        assignWorker(0, 'g1', 1);
        postMessageMock().mockClear();

        // First processed diff becomes the baseline. Use ply=1 so we can apply it without a snapshot.
        sendDiff('g1', 10, 1);
        postMessageMock().mockClear();

        // Gap: expected=11, receive 12 -> request snapshot and ignore move diff
        sendDiff('g1', 12, 3);
        expect(postMessageMock().mock.calls.filter((c) => c[0]?.type === 'vm')).toHaveLength(0);
        const snapshotRequests = postMessageMock()
            .mock.calls.map((c) => c[0])
            .filter((m) => m && m.type === 'request_snapshot' && m.topic === 'live.game.g1.moves.diff');
        expect(snapshotRequests.length).toBe(1);
        expect(snapshotRequests[0]).toMatchObject({ fromSeq: 10 });

        postMessageMock().mockClear();

        // Fill the gap: process 11
        sendDiff('g1', 11, 2);

        const vmCalls = postMessageMock().mock.calls.filter((c) => c[0]?.type === 'vm');
        expect(vmCalls.length).toBeGreaterThan(0);
        expect(vmCalls[0][0].payload.currentPly).toBe(2);
    });

    it('does not reset diff seq baseline after a snapshot', () => {
        assignWorker(0, 'g1', 1);
        postMessageMock().mockClear();

        sendDiff('g1', 10, 1);
        postMessageMock().mockClear();

        __testHandleEnvelope({
            topic: 'live.game.g1.snapshot',
            seq: 50,
            payload: { gid: 'g1', snapshot: { game_id: 'g1', currentPly: 1, moves: ['m1'], sfen: 'startpos b' } },
        });
        postMessageMock().mockClear();

        // Receiving a snapshot must NOT "re-baseline" the diff stream.
        // If the next diff is far in the future, we should still request a snapshot rather than applying it.
        sendDiff('g1', 101, 2);
        expect(postMessageMock().mock.calls.some((c) => c[0]?.type === 'request_snapshot')).toBe(true);
        expect(postMessageMock().mock.calls.filter((c) => c[0]?.type === 'vm')).toHaveLength(0);
    });
});
