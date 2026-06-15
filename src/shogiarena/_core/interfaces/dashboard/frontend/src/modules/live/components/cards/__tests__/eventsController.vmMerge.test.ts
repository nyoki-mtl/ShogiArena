import { describe, expect, it } from 'vitest';

import { __testMergeWorkerVmMessage } from '@/modules/live/components/cards/events-controller';
import type { WorkerViewModelMessage } from '@/modules/live/services/updates/worker-bridge';

function vm(overrides: Partial<WorkerViewModelMessage>): WorkerViewModelMessage {
    return {
        workerIdx: 0,
        gid: 'g1',
        current_ply: 1,
        sfen: 'startpos',
        lastMove: '7g7f',
        ki2Move: '７六歩',
        evalCp: 12,
        ...overrides,
    };
}

describe('eventsController vm merge helper', () => {
    it('clears old snapshot state when gid changes', () => {
        const prev = vm({
            gid: 'g1',
            current_ply: 32,
            sfen: 'sfen-old',
            lastMove: '2g2f',
            ki2Move: '２六歩',
            evalCp: 88,
            clock: { active: 'black' },
            snapshot: { game_id: 'g1', current_ply: 32 },
            snapshotDelta: { current_ply: 32, eval: 88 },
        });
        const incoming = vm({
            gid: 'g2',
            current_ply: null,
            sfen: null,
            lastMove: null,
            ki2Move: null,
            evalCp: null,
        });

        const { merged, gidChanged } = __testMergeWorkerVmMessage(prev, incoming);

        expect(gidChanged).toBe(true);
        expect(merged.gid).toBe('g2');
        expect(merged.current_ply).toBeNull();
        expect(merged.sfen).toBeNull();
        expect(merged.lastMove).toBeNull();
        expect(merged.ki2Move).toBeNull();
        expect(merged.evalCp).toBeNull();
        expect(merged.snapshot).toBeUndefined();
        expect(merged.snapshotDelta).toBeUndefined();
        expect(merged.clock).toBeUndefined();
    });

    it('also treats transition to unassigned gid as a boundary', () => {
        const prev = vm({
            gid: 'g1',
            snapshot: { game_id: 'g1', current_ply: 10 },
            clock: { active: 'white' },
        });
        const incoming = vm({
            gid: null,
            current_ply: null,
            sfen: null,
            lastMove: null,
            ki2Move: null,
            evalCp: null,
        });

        const { merged, gidChanged } = __testMergeWorkerVmMessage(prev, incoming);

        expect(gidChanged).toBe(true);
        expect(merged.gid).toBeNull();
        expect(merged.snapshot).toBeUndefined();
        expect(merged.clock).toBeUndefined();
    });

    it('keeps previous snapshot when gid is unchanged and no new snapshot arrives', () => {
        const prevSnapshot = { game_id: 'g1', current_ply: 12 };
        const prev = vm({
            gid: 'g1',
            snapshot: prevSnapshot,
            current_ply: 12,
        });
        const incoming = vm({
            gid: 'g1',
            current_ply: 13,
            lastMove: '8c8d',
            ki2Move: '８四歩',
            evalCp: 25,
        });

        const { merged, gidChanged } = __testMergeWorkerVmMessage(prev, incoming);

        expect(gidChanged).toBe(false);
        expect(merged.current_ply).toBe(13);
        expect(merged.lastMove).toBe('8c8d');
        expect(merged.snapshot).toBe(prevSnapshot);
    });

    it('replaces snapshot and clock when incoming vm carries them', () => {
        const prev = vm({
            gid: 'g1',
            snapshot: { game_id: 'g1', current_ply: 5 },
            clock: { active: 'black' },
        });
        const incomingSnapshot = { game_id: 'g1', current_ply: 6 };
        const incomingClock = { active: 'white', black_remain_ms: 1000 };
        const incoming = vm({
            gid: 'g1',
            current_ply: 6,
            snapshot: incomingSnapshot,
            clock: incomingClock,
        });

        const { merged, gidChanged } = __testMergeWorkerVmMessage(prev, incoming);

        expect(gidChanged).toBe(false);
        expect(merged.snapshot).toBe(incomingSnapshot);
        expect(merged.clock).toBe(incomingClock);
    });
});
