import { describe, expect, it, vi } from 'vitest';
import type { DashboardCoreState } from '@/types/dashboard';
import {
    clearCsaWaitingCardSync,
    createWorkerSnapshotsController,
    shouldShowCsaWaitingBoard,
} from './worker-snapshots';

function controller(state: DashboardCoreState) {
    return createWorkerSnapshotsController({
        state,
        warnSoftFailure: vi.fn(),
        showApiError: vi.fn(),
        notifyDashboardServerStopped: vi.fn(),
        getApiBase: () => '',
        requestJson: vi.fn(),
    });
}

describe('CSA worker card labels', () => {
    it('replaces a stale terminal worker snapshot with the waiting board while the run is alive', () => {
        const state = {
            csaRuns: [{ worker_idx: 0, current_game_id: null, stopped: false }],
        } as unknown as DashboardCoreState;

        expect(shouldShowCsaWaitingBoard(state, 0)).toBe(true);
        state.csaRuns = [{ worker_idx: 0, current_game_id: 'next-game', stopped: false }];
        expect(shouldShowCsaWaitingBoard(state, 0)).toBe(false);
        state.csaRuns = [{ worker_idx: 0, current_game_id: null, stopped: true }];
        expect(shouldShowCsaWaitingBoard(state, 0)).toBe(false);
    });

    it('uses the summary waiting state instead of a stale terminal snapshot id', () => {
        const state = {
            numWorkers: 1,
            workerSnapshots: new Map([[0, { game_id: 'finished-game' }]]),
            csaRuns: [{ worker_idx: 0, current_game_id: null, stopped: false }],
        } as unknown as DashboardCoreState;

        expect(controller(state).workerLatestLabel(0)).toBe('Worker #0: Waiting for pairing');
    });

    it('clears resume sync state while the CSA worker is waiting for pairing', () => {
        const state = {
            csaRuns: [{ worker_idx: 0, current_game_id: null, stopped: false }],
        } as unknown as DashboardCoreState;
        const cardState = { isSyncing: true, syncingStartedAt: 123 };

        expect(clearCsaWaitingCardSync(state, 0, cardState)).toBe(true);
        expect(cardState).toEqual({ isSyncing: false });

        state.csaRuns = [{ worker_idx: 0, current_game_id: 'game-1', stopped: false }];
        cardState.isSyncing = true;
        cardState.syncingStartedAt = 456;
        expect(clearCsaWaitingCardSync(state, 0, cardState)).toBe(false);
        expect(cardState).toEqual({ isSyncing: true, syncingStartedAt: 456 });
    });

    it('uses the summary current game id while a CSA game is active', () => {
        const state = {
            numWorkers: 1,
            workerSnapshots: new Map([[0, { game_id: 'previous-game' }]]),
            csaRuns: [{ worker_idx: 0, current_game_id: 'current-game', stopped: false }],
        } as unknown as DashboardCoreState;

        expect(controller(state).workerLatestLabel(0)).toBe('Worker #0: current-game');
    });

    it('labels a stopped CSA worker without exposing its internal game key', () => {
        const state = {
            numWorkers: 1,
            workerSnapshots: new Map([[0, { game_id: 'csa_internal-key' }]]),
            csaRuns: [{ worker_idx: 0, current_game_id: null, stopped: true }],
        } as unknown as DashboardCoreState;

        expect(controller(state).workerLatestLabel(0)).toBe('Worker #0: Finished');
    });
});
