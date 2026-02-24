import { describe, expect, it } from 'vitest';
import type { WorkerRuntimeState } from '@/modules/live/types';
import {
    maybeApplyImmediateClockStart,
    queueClockCorrections,
    resetWorkerRuntimeClockStateForNewGame,
    syncClockToTurnBoundary,
    updateTimeControlState,
} from '@/modules/live/utils/clockSync';

describe('clockSync', () => {
    it('uses started_at_ms from payload as the running clock anchor', () => {
        const ws: WorkerRuntimeState = {};
        const payload = {
            active: 'black',
            black_remain_ms: 300_000,
            white_remain_ms: 300_000,
            started_at_ms: 123_456,
            time_control_black: 't300000+i2000',
            time_control_white: 't300000+i2000',
        };

        queueClockCorrections(ws, payload, 'clock_start', 999_999);
        maybeApplyImmediateClockStart(ws, payload, 999_999);

        expect(ws.clockActive).toBe('black');
        expect(ws.blackRemainMs).toBe(300_000);
        expect(ws.startedAtMs).toBe(123_456);
    });

    it('accepts camelCase clock fields from merged payloads', () => {
        const ws: WorkerRuntimeState = {};
        queueClockCorrections(
            ws,
            {
                blackRemainMs: 111_000,
                whiteRemainMs: 222_000,
                byoyomiMsBlack: 30_000,
                timeControlBlack: 't300000+b30000',
            } as unknown as Parameters<typeof queueClockCorrections>[1],
            'snapshot',
            1000,
        );

        syncClockToTurnBoundary({
            ws,
            currentPly: 0,
            initialSfen: 'startpos',
            normalizeSFEN: (value) => value || 'startpos',
            nowMs: 1000,
        });

        expect(ws.blackRemainMs).toBe(111_000);
        expect(ws.byoyomiMsBlack).toBe(30_000);
        expect(ws.timeControlBlack).toBe('t300000+b30000');
        expect(ws.clockActive).toBe('black');
    });

    it('ignores stale clock snapshots older than current anchor', () => {
        const ws: WorkerRuntimeState = {
            clockActive: 'black',
            blackRemainMs: 250_000,
            startedAtMs: 5000,
        };
        const stalePayload = {
            active: 'black',
            black_remain_ms: 1,
            started_at_ms: 4000,
        };

        queueClockCorrections(ws, stalePayload, 'clock_start', 5001);
        maybeApplyImmediateClockStart(ws, stalePayload, 5001);

        expect(ws.startedAtMs).toBe(5000);
        expect(ws.blackRemainMs).toBe(250_000);
    });

    it('does not overwrite running remain by snapshot without time anchor', () => {
        const ws: WorkerRuntimeState = {
            clockActive: 'black',
            blackRemainMs: 124_000,
            startedAtMs: 5000,
            lastSeenPly: 12,
            lastSideToMove: 'black',
        };

        queueClockCorrections(
            ws,
            {
                active: 'black',
                black_remain_ms: 152_000,
            },
            'snapshot',
            6000,
        );

        syncClockToTurnBoundary({
            ws,
            currentPly: 13,
            initialSfen: 'startpos',
            normalizeSFEN: (value) => value || 'startpos',
            nowMs: 6000,
        });

        expect(ws.blackRemainMs).toBe(124_000);
        expect(ws.startedAtMs).toBe(5000);
    });

    it('applies both sides remain at turn boundary and activates side-to-move', () => {
        const ws: WorkerRuntimeState = {
            clockActive: 'black',
            startedAtMs: 10_000,
            blackRemainMs: 152_000,
            whiteRemainMs: 180_000,
            lastSeenPly: 20,
            lastSideToMove: 'black',
        };

        queueClockCorrections(
            ws,
            {
                black_remain_ms: 126_000,
                white_remain_ms: 180_000,
                occurred_at_ms: 12_000,
            },
            'clock_increment',
            12_100,
        );

        syncClockToTurnBoundary({
            ws,
            currentPly: 21,
            initialSfen: 'startpos',
            normalizeSFEN: (value) => value || 'startpos',
            nowMs: 12_100,
        });

        expect(ws.blackRemainMs).toBe(126_000);
        expect(ws.whiteRemainMs).toBe(180_000);
        expect(ws.clockActive).toBe('white');
        expect(ws.startedAtMs).toBe(12_000);
    });

    it('seeds initial remain from time control when clock snapshot is missing', () => {
        const ws: WorkerRuntimeState = {};
        updateTimeControlState(ws, 't300000+i2000', 't300000+i2000');

        expect(ws.blackRemainMs).toBe(300_000);
        expect(ws.whiteRemainMs).toBe(300_000);
    });

    it('re-seeds remain from time control after game-boundary reset', () => {
        const ws: WorkerRuntimeState = {
            blackRemainMs: 123_000,
            whiteRemainMs: 122_000,
            startedAtMs: 5000,
            clockActive: 'black',
            timeControlBlack: 't120000+i1000',
            timeControlWhite: 't120000+i1000',
            byoyomiMsBlack: 5000,
            byoyomiMsWhite: 5000,
            lastSeenPly: 42,
        };

        resetWorkerRuntimeClockStateForNewGame(ws);
        updateTimeControlState(ws, 't300000+i2000', 't300000+i2000');

        expect(ws.blackRemainMs).toBe(300_000);
        expect(ws.whiteRemainMs).toBe(300_000);
        expect(ws.clockActive).toBeNull();
        expect(ws.startedAtMs).toBeUndefined();
    });
});
