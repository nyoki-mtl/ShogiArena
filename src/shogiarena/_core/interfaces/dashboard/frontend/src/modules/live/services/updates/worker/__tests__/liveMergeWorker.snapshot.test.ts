import { describe, expect, it } from 'vitest';

import { __testShouldIgnoreIncomingSnapshot } from '../live-merge-worker';
import type { WorkerSnapshotRecord } from '@/modules/live/types/updates';

const snap = (partial: Partial<WorkerSnapshotRecord>): WorkerSnapshotRecord => ({
    game_id: null,
    initial_sfen: 'startpos',
    sfen: null,
    black_name: null,
    white_name: null,
    moves: [],
    ki2_moves: [],
    eval_black: [],
    eval_white: [],
    nodes_values: [],
    depth_values: [],
    seldepth_values: [],
    move_times_ms: [],
    wall_times_ms: [],
    engine_wall_times_ms: [],
    latency_deltas_ms: [],
    latency_alerts: [],
    current_ply: 0,
    ...partial,
});

describe('liveMergeWorker snapshot acceptance', () => {
    it('ignores stale snapshot when it is a strict prefix of existing history', () => {
        const existing = snap({ game_id: 'g1', current_ply: 3, moves: ['7g7f', '3c3d', '2g2f'] });
        const incoming = snap({ game_id: 'g1', current_ply: 1, moves: ['7g7f'] });
        expect(__testShouldIgnoreIncomingSnapshot(existing, incoming)).toBe(true);
    });

    it('accepts snapshot when history diverges (possible correction)', () => {
        const existing = snap({ game_id: 'g1', current_ply: 3, moves: ['7g7f', '3c3d', '2g2f'] });
        const incoming = snap({ game_id: 'g1', current_ply: 1, moves: ['2g2f'] });
        expect(__testShouldIgnoreIncomingSnapshot(existing, incoming)).toBe(false);
    });

    it('accepts snapshot when gid differs', () => {
        const existing = snap({ game_id: 'g-old', current_ply: 50 });
        const incoming = snap({ game_id: 'g-new', current_ply: 10 });
        expect(__testShouldIgnoreIncomingSnapshot(existing, incoming)).toBe(false);
    });

    it('accepts snapshot when incoming current_ply is newer', () => {
        const existing = snap({ game_id: 'g1', current_ply: 10 });
        const incoming = snap({ game_id: 'g1', current_ply: 50, moves: Array.from({ length: 50 }, () => '7g7f') });
        expect(__testShouldIgnoreIncomingSnapshot(existing, incoming)).toBe(false);
    });
});
