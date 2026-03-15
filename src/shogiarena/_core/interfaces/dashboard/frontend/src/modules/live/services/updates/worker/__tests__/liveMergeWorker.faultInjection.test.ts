import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { __testGetMoveSeq, __testGetRecoveryState, __testHandleEnvelope, __testResetState } from '../liveMergeWorker';

type Envelope = {
    topic: string;
    seq: number;
    payload: Record<string, unknown>;
};

const MOVE_BY_PLY: Record<number, string> = {
    1: '7g7f',
    2: '3c3d',
    3: '2g2f',
    4: '8c8d',
    5: '2f2e',
    6: '8d8e',
};

function movesUntil(ply: number): string[] {
    const out: string[] = [];
    for (let i = 1; i <= ply; i += 1) {
        out.push(MOVE_BY_PLY[i] ?? `m${i}`);
    }
    return out;
}

function moveDiffEnvelope(seq: number, ply: number, moveSeq: number): Envelope {
    return {
        topic: 'live.game.g1.moves.diff',
        seq,
        payload: {
            gid: 'g1',
            ply,
            usi: MOVE_BY_PLY[ply] ?? `m${ply}`,
            move_seq: moveSeq,
            assignment_rev: 1,
            game_epoch: 1,
        },
    };
}

function analysisDiffEnvelope(seq: number, ply: number, evalCp: number): Envelope {
    return {
        topic: 'live.game.g1.analysis.diff',
        seq,
        payload: {
            gid: 'g1',
            kind: 'analysis',
            patch: {
                current_ply: ply,
                eval: evalCp,
            },
            assignment_rev: 1,
            game_epoch: 1,
        },
    };
}

function snapshotEnvelope(seq: number, ply: number, moveSeq = ply): Envelope {
    return {
        topic: 'live.game.g1.snapshot',
        seq,
        payload: {
            gid: 'g1',
            snapshot: {
                game_id: 'g1',
                initial_sfen: 'startpos',
                current_ply: ply,
                moves: movesUntil(ply),
                assignment_rev: 1,
                game_epoch: 1,
                move_seq: moveSeq,
            },
        },
    };
}

describe('liveMergeWorker fault injection', () => {
    beforeEach(() => {
        __testResetState();
        (globalThis as unknown as { postMessage?: unknown }).postMessage = vi.fn();
    });

    afterEach(() => {
        __testResetState();
        (globalThis as unknown as { postMessage?: unknown }).postMessage = undefined;
    });

    const postMessageMock = () => (globalThis as unknown as { postMessage: ReturnType<typeof vi.fn> }).postMessage;

    const snapshotRequests = () =>
        postMessageMock()
            .mock.calls.map(([msg]) => msg as Record<string, unknown>)
            .filter((msg) => msg.type === 'request_snapshot');

    const lastVmPayload = () =>
        postMessageMock()
            .mock.calls.map(([msg]) => msg as Record<string, unknown>)
            .filter((msg) => msg.type === 'vm')
            .map((msg) => msg.payload as Record<string, unknown>)
            .at(-1);

    const bootAssignedGame = () => {
        __testHandleEnvelope({
            topic: 'live.assignment.snapshot',
            seq: 1,
            payload: {
                assignments: { '0': 'g1' },
                gids: ['g1'],
                assignment_rev: 1,
                updatedAt: 0,
            },
        });
        __testHandleEnvelope(snapshotEnvelope(2, 1, 1));
        postMessageMock().mockClear();
    };

    const runFlow = (flow: Envelope[]) => {
        __testResetState();
        postMessageMock().mockClear();
        bootAssignedGame();
        for (const env of flow) {
            __testHandleEnvelope(env);
        }
        return {
            signature: {
                current_ply: (lastVmPayload()?.current_ply as number | null | undefined) ?? null,
                lastMove: (lastVmPayload()?.lastMove as string | null | undefined) ?? null,
                moveSeq: __testGetMoveSeq('g1'),
                recovery: __testGetRecoveryState('g1'),
            },
            snapshotRequestCount: snapshotRequests().length,
        };
    };

    it('drop: missing move diff triggers recovery and snapshot rehydration', () => {
        bootAssignedGame();
        __testHandleEnvelope(moveDiffEnvelope(3, 2, 2));

        postMessageMock().mockClear();
        __testHandleEnvelope(moveDiffEnvelope(5, 4, 4));

        const requests = snapshotRequests();
        expect(requests.length).toBe(1);
        expect(requests[0]).toMatchObject({
            topic: 'live.game.g1.moves.diff',
            reason_code: 102,
        });
        expect(__testGetRecoveryState('g1')).toBe('SNAPSHOT_REQUESTED');

        __testHandleEnvelope(snapshotEnvelope(6, 4, 4));
        expect(__testGetRecoveryState('g1')).toBe('NORMAL');
        expect(__testGetMoveSeq('g1')).toBe(4);
        expect(lastVmPayload()).toMatchObject({ current_ply: 4, lastMove: MOVE_BY_PLY[4] });
    });

    it('reorder: out-of-order moves recover after replay + authoritative snapshot', () => {
        bootAssignedGame();
        __testHandleEnvelope(moveDiffEnvelope(3, 2, 2));

        __testHandleEnvelope(moveDiffEnvelope(5, 4, 4));
        __testHandleEnvelope(moveDiffEnvelope(4, 3, 3));
        __testHandleEnvelope(moveDiffEnvelope(5, 4, 4));

        expect(snapshotRequests().some((msg) => msg.reason_code === 102)).toBe(true);
        expect(__testGetMoveSeq('g1')).toBe(4);
        expect(__testGetRecoveryState('g1')).toBe('SNAPSHOT_REQUESTED');

        __testHandleEnvelope(snapshotEnvelope(6, 4, 4));
        expect(__testGetRecoveryState('g1')).toBe('NORMAL');
    });

    it('delay burst: non-strict analysis bursts do not bypass pending recovery', () => {
        bootAssignedGame();
        __testHandleEnvelope(moveDiffEnvelope(3, 2, 2));
        __testHandleEnvelope(moveDiffEnvelope(5, 4, 4));

        postMessageMock().mockClear();
        __testHandleEnvelope(analysisDiffEnvelope(100, 2, 120));
        __testHandleEnvelope(analysisDiffEnvelope(101, 2, 130));

        expect(__testGetRecoveryState('g1')).toBe('SNAPSHOT_REQUESTED');
        expect(__testGetMoveSeq('g1')).toBe(2);
        expect(lastVmPayload()).toBeUndefined();

        __testHandleEnvelope(moveDiffEnvelope(4, 3, 3));
        __testHandleEnvelope(moveDiffEnvelope(5, 4, 4));
        __testHandleEnvelope(snapshotEnvelope(6, 4, 4));

        expect(__testGetRecoveryState('g1')).toBe('NORMAL');
        expect(__testGetMoveSeq('g1')).toBe(4);
    });

    it('duplicate: stale ws seq and duplicate move_seq are both ignored', () => {
        bootAssignedGame();
        __testHandleEnvelope(moveDiffEnvelope(3, 2, 2));

        postMessageMock().mockClear();
        __testHandleEnvelope(moveDiffEnvelope(3, 2, 2)); // stale ws seq duplicate
        __testHandleEnvelope(moveDiffEnvelope(4, 2, 2)); // fresh ws seq but stale move_seq

        expect(snapshotRequests()).toHaveLength(0);
        expect(lastVmPayload()).toBeUndefined();

        __testHandleEnvelope(moveDiffEnvelope(5, 3, 3));
        expect(__testGetMoveSeq('g1')).toBe(3);
        expect(lastVmPayload()).toMatchObject({ current_ply: 3, lastMove: MOVE_BY_PLY[3] });
    });

    it('deterministic replay: injected jitter flow converges to canonical final signature', () => {
        const canonical = runFlow([
            moveDiffEnvelope(3, 2, 2),
            moveDiffEnvelope(4, 3, 3),
            moveDiffEnvelope(5, 4, 4),
            moveDiffEnvelope(6, 5, 5),
            snapshotEnvelope(7, 5, 5),
        ]);

        const injected = runFlow([
            moveDiffEnvelope(3, 2, 2),
            moveDiffEnvelope(5, 4, 4), // out-of-order arrival
            analysisDiffEnvelope(100, 2, 150), // burst while recovery pending
            moveDiffEnvelope(4, 3, 3), // delayed missing move
            moveDiffEnvelope(5, 4, 4), // replay after gap close
            moveDiffEnvelope(6, 5, 5),
            moveDiffEnvelope(7, 5, 5), // duplicate move_seq
            snapshotEnvelope(8, 5, 5),
        ]);

        expect(canonical.signature).toEqual(injected.signature);
        expect(injected.snapshotRequestCount).toBeGreaterThanOrEqual(1);
    });
});
