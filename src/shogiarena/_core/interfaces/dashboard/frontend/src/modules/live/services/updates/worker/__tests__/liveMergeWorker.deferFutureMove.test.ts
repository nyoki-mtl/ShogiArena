import { afterEach, describe, expect, it, vi } from 'vitest';

import { __testHandleEnvelope, __testResetState } from '../live-merge-worker';

describe('liveMergeWorker move sequencing', () => {
    afterEach(() => {
        __testResetState();
        (globalThis as unknown as { postMessage?: unknown }).postMessage = undefined;
    });

    it('requests snapshot and defers a move diff that jumps beyond contiguous moves', () => {
        __testResetState();
        const postMessage = vi.fn();
        (globalThis as unknown as { postMessage?: unknown }).postMessage = postMessage;

        // Assign worker 0 to g1 (this emits an initial request_snapshot, then we clear it).
        __testHandleEnvelope({ topic: 'live.assignment.snapshot', seq: 1, payload: { assignments: { '0': 'g1' } } });
        postMessage.mockClear();

        // Provide a snapshot so pending catch-up is cleared.
        __testHandleEnvelope({
            topic: 'live.game.g1.snapshot',
            seq: 2,
            payload: {
                gid: 'g1',
                snapshot: {
                    game_id: 'g1',
                    initial_sfen: 'startpos',
                    moves: ['7g7f'],
                    current_ply: 1,
                },
            },
        });
        postMessage.mockClear();

        // Now send a move diff claiming to be for a far future ply.
        __testHandleEnvelope({
            topic: 'live.game.g1.moves.diff',
            seq: 3,
            payload: { gid: 'g1', kind: 'move', patch: { current_ply: 10, move: '3c3d' } },
        });

        const calls = postMessage.mock.calls.map(([msg]) => msg);
        expect(calls).toEqual(
            expect.arrayContaining([
                expect.objectContaining({
                    type: 'request_snapshot',
                    topic: 'live.game.g1.moves.diff',
                }),
            ]),
        );
    });
});
