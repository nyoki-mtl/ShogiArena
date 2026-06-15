import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { __testGetMoveSeq, __testGetRecoveryState, __testHandleEnvelope, __testResetState } from '../live-merge-worker';

describe('liveMergeWorker recovery state machine', () => {
    beforeEach(() => {
        __testResetState();
        (globalThis as unknown as { postMessage?: unknown }).postMessage = vi.fn();
    });

    afterEach(() => {
        __testResetState();
        (globalThis as unknown as { postMessage?: unknown }).postMessage = undefined;
    });

    const postMessageMock = () => (globalThis as unknown as { postMessage: ReturnType<typeof vi.fn> }).postMessage;

    const assignWorker = (workerIdx: number, gid: string, rev = 1) => {
        __testHandleEnvelope({
            topic: 'live.assignment.snapshot',
            seq: 1,
            payload: { assignments: { [String(workerIdx)]: gid }, gids: [gid], assignment_rev: rev, updated_at: 0 },
        });
    };

    const applySnapshot = (gid: string, seq: number, ply: number, moves: string[], moveSeq?: number) => {
        __testHandleEnvelope({
            topic: `live.game.${gid}.snapshot`,
            seq,
            payload: {
                gid,
                snapshot: {
                    game_id: gid,
                    initial_sfen: 'startpos',
                    current_ply: ply,
                    moves,
                    assignment_rev: 1,
                    game_epoch: 1,
                    ...(typeof moveSeq === 'number' ? { move_seq: moveSeq } : {}),
                },
            },
        });
    };

    it('transitions to SNAPSHOT_REQUESTED on move_seq gap', () => {
        assignWorker(0, 'g1');
        applySnapshot('g1', 2, 1, ['7g7f'], 1);
        postMessageMock().mockClear();

        __testHandleEnvelope({
            topic: 'live.game.g1.moves.diff',
            seq: 3,
            payload: {
                gid: 'g1',
                ply: 3,
                usi: '2g2f',
                move_seq: 3,
                assignment_rev: 1,
                game_epoch: 1,
            },
        });

        const snapshotRequests = postMessageMock()
            .mock.calls.map(([msg]) => msg)
            .filter((msg) => msg && (msg as { type?: unknown }).type === 'request_snapshot');
        expect(snapshotRequests.length).toBe(1);
        expect(snapshotRequests[0]).toMatchObject({
            topic: 'live.game.g1.moves.diff',
            reason_code: 102,
        });
        expect(__testGetRecoveryState('g1')).toBe('SNAPSHOT_REQUESTED');
        expect(__testGetMoveSeq('g1')).toBe(1);
    });

    it('returns to NORMAL after authoritative snapshot is applied', () => {
        assignWorker(0, 'g1');
        applySnapshot('g1', 2, 1, ['7g7f'], 1);

        __testHandleEnvelope({
            topic: 'live.game.g1.moves.diff',
            seq: 3,
            payload: {
                gid: 'g1',
                ply: 3,
                usi: '2g2f',
                move_seq: 3,
                assignment_rev: 1,
                game_epoch: 1,
            },
        });
        expect(__testGetRecoveryState('g1')).toBe('SNAPSHOT_REQUESTED');

        applySnapshot('g1', 4, 3, ['7g7f', '3c3d', '2g2f'], 3);

        expect(__testGetRecoveryState('g1')).toBe('NORMAL');
        expect(__testGetMoveSeq('g1')).toBe(3);
    });

    it('drops duplicate move_seq as stale move update', () => {
        assignWorker(0, 'g1');
        applySnapshot('g1', 2, 1, ['7g7f'], 1);
        postMessageMock().mockClear();

        __testHandleEnvelope({
            topic: 'live.game.g1.moves.diff',
            seq: 3,
            payload: {
                gid: 'g1',
                ply: 1,
                usi: '7g7f',
                move_seq: 1,
                assignment_rev: 1,
                game_epoch: 1,
            },
        });

        const vmCalls = postMessageMock().mock.calls.filter((call) => (call[0] as { type?: unknown }).type === 'vm');
        const snapshotRequests = postMessageMock()
            .mock.calls.map(([msg]) => msg)
            .filter((msg) => msg && (msg as { type?: unknown }).type === 'request_snapshot');
        expect(vmCalls).toHaveLength(0);
        expect(snapshotRequests).toHaveLength(0);
        expect(__testGetMoveSeq('g1')).toBe(1);
    });
});
