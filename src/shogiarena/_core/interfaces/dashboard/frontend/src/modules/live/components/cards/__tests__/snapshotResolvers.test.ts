import { describe, expect, it, vi } from 'vitest';
import { resolveSnapshotForWorkerCard } from '@/modules/live/components/cards/snapshot-resolvers';
import type { WorkerSnapshotRecord } from '@/modules/live/components/cards/types';

function createSnapshot(overrides: Partial<WorkerSnapshotRecord> = {}): WorkerSnapshotRecord {
    return {
        game_id: 'game-1',
        initial_sfen: 'startpos',
        sfen: 'startpos moves 7g7f',
        black_name: null,
        white_name: null,
        moves: ['7g7f'],
        ki2_moves: [],
        eval_black: [],
        eval_white: [],
        nodes_values: [],
        depth_values: [],
        seldepth_values: [],
        move_times_ms: [],
        wall_times_ms: [],
        latency_deltas_ms: [],
        latency_alerts: [],
        current_ply: 1,
        ...overrides,
    };
}

describe('resolveSnapshotForWorkerCard', () => {
    it('fetches a bootstrap snapshot when no cached snapshot exists', async () => {
        const fetched = createSnapshot();
        const fetchAndCacheWorkerSnapshot = vi.fn(async () => fetched);

        const result = await resolveSnapshotForWorkerCard(
            3,
            {
                getCachedWorkerSnapshot: () => null,
                fetchAndCacheWorkerSnapshot,
            },
            { forceBootstrap: false },
        );

        expect(fetchAndCacheWorkerSnapshot).toHaveBeenCalledOnce();
        expect(result).toEqual(fetched);
    });

    it('does not fetch when a cached snapshot exists but is waiting for WS repair', async () => {
        const cached = createSnapshot({ initial_sfen: '' });
        const fetchAndCacheWorkerSnapshot = vi.fn(async () => createSnapshot({ game_id: 'game-2' }));

        const result = await resolveSnapshotForWorkerCard(
            5,
            {
                getCachedWorkerSnapshot: () => cached,
                fetchAndCacheWorkerSnapshot,
            },
            { forceBootstrap: false },
        );

        expect(fetchAndCacheWorkerSnapshot).not.toHaveBeenCalled();
        expect(result).toEqual(cached);
    });
});
