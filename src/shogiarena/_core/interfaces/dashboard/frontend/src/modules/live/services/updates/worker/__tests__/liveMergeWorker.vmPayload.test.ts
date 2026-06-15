import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { __testHandleEnvelope, __testResetState } from '../live-merge-worker';

describe('liveMergeWorker vm payload contract', () => {
    beforeEach(() => {
        __testResetState();
        (globalThis as unknown as { postMessage?: unknown }).postMessage = vi.fn();
    });

    afterEach(() => {
        __testResetState();
        (globalThis as unknown as { postMessage?: unknown }).postMessage = undefined;
    });

    const postMessageMock = () => (globalThis as unknown as { postMessage: ReturnType<typeof vi.fn> }).postMessage;

    const extractVmPayloads = () =>
        postMessageMock()
            .mock.calls.map(([msg]) => msg)
            .filter((msg) => msg && (msg as { type?: unknown }).type === 'vm')
            .map((msg) => (msg as { payload?: unknown }).payload as Record<string, unknown>);

    const assignWorker = () => {
        __testHandleEnvelope({
            topic: 'live.assignment.snapshot',
            seq: 1,
            payload: { assignments: { '0': 'g1' }, gids: ['g1'], updated_at: 0 },
        });
    };

    it('emits move updates with authoritative snapshot', () => {
        assignWorker();
        postMessageMock().mockClear();

        __testHandleEnvelope({
            topic: 'live.game.g1.moves.diff',
            seq: 2,
            payload: { gid: 'g1', kind: 'move', patch: { current_ply: 1, move: '7g7f' } },
        });

        const payloads = extractVmPayloads();
        expect(payloads).toHaveLength(1);
        expect(payloads[0].snapshot).toMatchObject({
            game_id: 'g1',
            current_ply: 1,
            moves: ['7g7f'],
        });
        expect(payloads[0].snapshotDelta).toBeUndefined();
    });

    it('includes vm.clock only for clock stream updates', () => {
        assignWorker();
        postMessageMock().mockClear();

        __testHandleEnvelope({
            topic: 'live.game.g1.clock.diff',
            seq: 2,
            payload: {
                gid: 'g1',
                assignment_rev: 1,
                type: 'clock_start',
                clock: {
                    active: 'black',
                    black_remain_ms: 300000,
                    white_remain_ms: 300000,
                    started_at_ms: 1234,
                },
            },
        });

        const payloads = extractVmPayloads();
        expect(payloads).toHaveLength(1);
        expect(payloads[0].clock).toMatchObject({
            active: 'black',
            black_remain_ms: 300000,
            white_remain_ms: 300000,
            started_at_ms: 1234,
        });
    });

    it('keeps analysis updates delta-first (no full snapshot)', () => {
        assignWorker();
        postMessageMock().mockClear();

        __testHandleEnvelope({
            topic: 'live.game.g1.analysis.diff',
            seq: 2,
            payload: {
                gid: 'g1',
                kind: 'analysis',
                patch: { current_ply: 1, eval: 42, depth: 12 },
            },
        });

        const payloads = extractVmPayloads();
        expect(payloads).toHaveLength(1);
        expect(payloads[0].snapshot).toBeUndefined();
        expect(payloads[0].snapshotDelta).toMatchObject({
            current_ply: 1,
            eval: 42,
            depth: 12,
        });
    });

    it('includes full snapshot on snapshot topic', () => {
        assignWorker();
        postMessageMock().mockClear();

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

        const payloads = extractVmPayloads();
        expect(payloads).toHaveLength(1);
        expect(payloads[0].snapshot).toMatchObject({
            game_id: 'g1',
            current_ply: 1,
            moves: ['7g7f'],
        });
    });
});
