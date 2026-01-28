import { describe, expect, it } from 'vitest';

import { mergeWorkerSnapshot } from '../snapshotMerge';
import { createEmptyWorkerSnapshot } from '@/modules/live/utils';
import type { WorkerSnapshotRecord, WorkerSnapshotUpdate } from '@/modules/live/types/updates';

const asRecord = (value: unknown): WorkerSnapshotRecord => value as WorkerSnapshotRecord;

describe('mergeWorkerSnapshot', () => {
    it('appends move/eval/search stats to snapshot', () => {
        const base = asRecord(createEmptyWorkerSnapshot());
        const update: WorkerSnapshotUpdate = {
            game_id: 'g1',
            currentPly: 1,
            move: '7g7f',
            eval: 42,
            depth: 12,
            nodes: 999,
            time_ms: 180,
            wall_time_ms: 250,
        };

        const merged = mergeWorkerSnapshot(base, update);

        expect(merged.game_id).toBe('g1');
        expect(merged.currentPly).toBe(1);
        expect(merged.moves).toEqual(['7g7f']);
        expect(merged.eval_black[0]).toBe(42);
        expect(merged.eval_white[0]).toBe(-42);
        expect(merged.depth_values[0]).toBe(12);
        expect(merged.nodes_values[0]).toBe(999);
        expect(merged.move_times_ms[0]).toBe(180);
        expect(merged.wall_times_ms[0]).toBe(250);
    });

    it('resets snapshot when game_id changes', () => {
        const base = asRecord(createEmptyWorkerSnapshot());
        base.game_id = 'g-old';
        base.moves = ['7g7f', '3c3d'];
        base.currentPly = 2;

        const update: WorkerSnapshotUpdate = {
            game_id: 'g-new',
            currentPly: 1,
            move: '2g2f',
            eval: -30,
        };

        const merged = mergeWorkerSnapshot(base, update);

        expect(merged.game_id).toBe('g-new');
        expect(merged.moves).toEqual(['2g2f']);
        expect(merged.currentPly).toBe(1);
        expect(merged.eval_black[0]).toBe(-30);
        expect(merged.eval_white[0]).toBe(30);
    });

    it('merges clock/time control fields', () => {
        const base = asRecord(createEmptyWorkerSnapshot());
        const update: WorkerSnapshotUpdate = {
            game_id: 'g1',
            currentPly: 1,
            clock: {
                active: 'black',
                black_remain_ms: 120000,
                white_remain_ms: 119000,
                time_control_black: '2m+30s',
            },
            time_control_white: '2m+30s',
        };

        const merged = mergeWorkerSnapshot(base, update);

        expect(merged.clock).toMatchObject({
            active: 'black',
            black_remain_ms: 120000,
            white_remain_ms: 119000,
            time_control_black: '2m+30s',
        });
        expect(merged.time_control_white).toBe('2m+30s');
    });

    it('accepts ply regression (Undo/"待った") and truncates future history', () => {
        const base = asRecord(createEmptyWorkerSnapshot());
        base.game_id = 'g1';
        base.currentPly = 2;
        base.moves = ['7g7f', '3c3d'];
        base.ki2_moves = ['７六歩', '３四歩'];

        const rewind: WorkerSnapshotUpdate = {
            game_id: 'g1',
            currentPly: 1,
        };
        const afterRewind = mergeWorkerSnapshot(base, rewind);
        expect(afterRewind.currentPly).toBe(1);
        expect(afterRewind.moves).toEqual(['7g7f']);
        expect(afterRewind.ki2_moves).toEqual(['７六歩']);

        const revised: WorkerSnapshotUpdate = {
            game_id: 'g1',
            currentPly: 1,
            move: '2g2f',
            ki2_move: '２六歩',
        };
        const afterRevision = mergeWorkerSnapshot(afterRewind, revised);
        expect(afterRevision.currentPly).toBe(1);
        expect(afterRevision.moves).toEqual(['2g2f']);
        expect(afterRevision.ki2_moves).toEqual(['２六歩']);
    });
});
