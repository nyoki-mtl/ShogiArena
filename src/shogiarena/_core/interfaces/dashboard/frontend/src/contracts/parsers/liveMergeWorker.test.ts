import { describe, expect, it } from 'vitest';
import { parseLiveMergeWorkerEnvelope } from './live-merge-worker';

describe('live merge worker parser', () => {
    it('parses a worker envelope', () => {
        const envelope = parseLiveMergeWorkerEnvelope({
            topic: 'live.assignment.snapshot',
            seq: 12,
            payload: { assignments: { '0': 'g1' } },
        });

        expect(envelope.topic).toBe('live.assignment.snapshot');
        expect(envelope.seq).toBe(12);
        expect(envelope.payload).toEqual({ assignments: { '0': 'g1' } });
    });

    it('rejects envelope without topic', () => {
        expect(() =>
            parseLiveMergeWorkerEnvelope({
                seq: 1,
                payload: { assignments: { '0': 'g1' } },
            } as never),
        ).toThrow();
    });

    it('rejects envelope without seq', () => {
        expect(() =>
            parseLiveMergeWorkerEnvelope({
                topic: 'live.game.g1.moves.diff',
                payload: { gid: 'g1', ply: 1 },
            } as never),
        ).toThrow();
    });

    it('rejects envelope without payload', () => {
        expect(() =>
            parseLiveMergeWorkerEnvelope({
                topic: 'live.game.g1.moves.diff',
                seq: 4,
            } as never),
        ).toThrow();
    });
});
