import { afterEach, describe, expect, it, vi } from 'vitest';

import { __testHandleEnvelope, __testResetState } from '../liveMergeWorker';

describe('liveMergeWorker hole detection', () => {
    afterEach(() => {
        __testResetState();
        (globalThis as unknown as { postMessage?: unknown }).postMessage = undefined;
    });

    it('requests snapshot when current_ply runs ahead of contiguous moves', () => {
        __testResetState();
        const postMessage = vi.fn();
        (globalThis as unknown as { postMessage?: unknown }).postMessage = postMessage;

        __testHandleEnvelope({
            topic: 'live.assignment.snapshot',
            seq: 1,
            payload: { assignments: { '0': 'g1' } },
        });

        postMessage.mockClear();
        __testHandleEnvelope({
            topic: 'live.game.g1.snapshot',
            seq: 2,
            payload: {
                gid: 'g1',
                snapshot: {
                    game_id: 'g1',
                    initial_sfen: 'startpos',
                    moves: [],
                    current_ply: 0,
                },
            },
        });

        postMessage.mockClear();
        __testHandleEnvelope({
            topic: 'live.game.g1.moves.diff',
            seq: 3,
            payload: { gid: 'g1', kind: 'move', patch: { current_ply: 10, move: '7g7f' } },
        });

        expect(postMessage).toHaveBeenCalledWith(
            expect.objectContaining({
                type: 'request_snapshot',
                topic: 'live.game.g1.moves.diff',
            }),
        );
    });
});
