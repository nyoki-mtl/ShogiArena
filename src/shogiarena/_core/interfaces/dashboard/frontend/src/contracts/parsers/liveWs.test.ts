import { describe, expect, it } from 'vitest';
import { parseGamesPayload, parseLiveGamesEnvelope, parseLiveSummaryEnvelope, parseSummaryPayload } from './liveWs';

describe('liveWs parsers', () => {
    it('parses a summary envelope', () => {
        const envelope = parseLiveSummaryEnvelope({
            topic: 'live.summary.snapshot.tournament',
            seq: 7,
            payload: {
                games: { completed: 10, total: 20 },
                gamesCompleted: 10,
                gamesScheduled: 20,
                liveView: null,
            },
        });

        expect(envelope.topic).toBe('live.summary.snapshot.tournament');
        expect(envelope.seq).toBe(7);
        expect(envelope.payload.gamesCompleted).toBe(10);
    });

    it('parses a games envelope', () => {
        const envelope = parseLiveGamesEnvelope({
            topic: 'live.games.delta',
            seq: 12,
            payload: {
                kind: 'bulk',
                revision: 3,
                base_revision: null,
                rows: [{ game_id: 'g1' }],
                snapshotMeta: {},
            },
        });

        expect(envelope.topic).toBe('live.games.delta');
        expect(envelope.seq).toBe(12);
        expect(envelope.payload.kind).toBe('bulk');
        expect(envelope.payload.revision).toBe(3);
        expect(envelope.payload.rows?.[0]).toMatchObject({ game_id: 'g1' });
    });

    it('throws when payload is invalid', () => {
        expect(() =>
            parseSummaryPayload({
                // `topic` is required and must be string
                seq: 1,
                payload: {},
            } as never),
        ).toThrow();
    });

    it('throws when summary payload is missing games', () => {
        expect(() =>
            parseSummaryPayload({
                topic: 'live.summary.snapshot.tournament',
                seq: 2,
                payload: {},
            } as never),
        ).toThrow();
    });

    it('throws when games payload is missing required fields', () => {
        expect(() =>
            parseGamesPayload({
                topic: 'live.games.delta',
                seq: 3,
                payload: { rows: [] },
            } as never),
        ).toThrow();
    });
});
