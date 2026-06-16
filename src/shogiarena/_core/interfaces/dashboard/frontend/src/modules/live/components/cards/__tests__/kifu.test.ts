import { describe, expect, it, vi } from 'vitest';
import { createKifuHandlers } from '@/modules/live/components/cards/kifu';
import type { WorkerSnapshotRecord } from '@/modules/live/components/cards/types';
import type { LiveCardState } from '@/modules/live/types';

function createSnapshot(overrides: Partial<WorkerSnapshotRecord> = {}): WorkerSnapshotRecord {
    return {
        game_id: 'game-1',
        initial_sfen: 'startpos',
        sfen: 'startpos moves 7g7f',
        black_name: null,
        white_name: null,
        moves: ['7g7f'],
        ki2_moves: ['☗７六歩'],
        eval_black: [null],
        eval_white: [null],
        nodes_values: [null],
        depth_values: [null],
        seldepth_values: [null],
        move_times_ms: [null],
        wall_times_ms: [null],
        engine_wall_times_ms: [null],
        latency_deltas_ms: [null],
        latency_alerts: [null],
        current_ply: 1,
        ...overrides,
    };
}

function createHandlers(snapshot: WorkerSnapshotRecord) {
    const liveCardState: LiveCardState = {
        id: 'card-1',
        source: 'worker-latest:0',
        viewPly: 1,
        autoSync: false,
    };
    return createKifuHandlers({
        state: {} as never,
        owner: window,
        getWorkerSnapshot: () => snapshot,
        normalizeSFEN: (sfen) => sfen ?? 'startpos',
        getStartingPlyNumber: () => 1,
        formatRemain: () => '',
        formatInc: () => '',
        formatCountUp: () => '',
        formatByoyomi: () => '',
        updateElement: vi.fn(),
        warnSoftFailure: (_context, error): never => {
            throw error instanceof Error ? error : new Error(String(error));
        },
        findCardById: () => liveCardState,
        getCardList: () => [liveCardState],
        parseLiveCardId: (raw) => raw,
        computeAutoViewPly: () => 1,
        syncWorkerViewFromCard: vi.fn(),
        goToMoveCard: vi.fn(),
        resolveCardData: vi.fn(async () => snapshot),
    });
}

describe('createKifuHandlers', () => {
    it('keeps a single current-prefix marker in formatted kifu lines', () => {
        const snapshot = createSnapshot();
        const handlers = createHandlers(snapshot);

        expect(handlers.formatKifuLineSimple(snapshot, 1)).toBe('001 ☗７六歩');
    });

    it('does not duplicate the current-prefix marker in the move summary', () => {
        const snapshot = createSnapshot();
        const handlers = createHandlers(snapshot);
        document.body.innerHTML = '<div id="moveSummary-card-1"></div>';

        handlers.updateMoveSummaryCard('card-1', snapshot);

        const html = document.getElementById('moveSummary-card-1')?.innerHTML ?? '';
        expect(html).toContain('☗');
        expect(html).not.toContain('☗☗');
    });
});
