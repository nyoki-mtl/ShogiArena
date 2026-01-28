// Live module internal types

import type { WorkerSnapshot } from '@/types/live';

export type LiveCardId = string | number;

export type LiveCardSource = 'empty' | `worker-latest:${number}` | `db-game:${string}` | (string & {});

export interface LiveCardState {
    id: LiveCardId;
    source: LiveCardSource;
    viewPly?: number;
    autoSync?: boolean;
    lastGameId?: string | null;
    initial_sfen?: string;
    /** Indicates the card is waiting for a fresh snapshot (e.g., after tab resume). */
    isSyncing?: boolean;
    /** Timestamp (ms) when syncing started; used for timeout warnings. */
    syncingStartedAt?: number;
    [key: string]: unknown;
}

export interface WorkerRuntimeState {
    currentGameId?: string | null;
    lastGameId?: string | null;
    prevGameId?: string | null;
    viewPly?: number;
    clockActive?: 'black' | 'white' | null;
    blackRemainMs?: number;
    whiteRemainMs?: number;
    startedAtMs?: number;
    timeControlBlack?: string | null;
    timeControlWhite?: string | null;
    byoyomiMsBlack?: number;
    byoyomiMsWhite?: number;
    blackFrozenByoText?: string | null;
    whiteFrozenByoText?: string | null;
    tcBlackHasTimePool?: boolean;
    tcWhiteHasTimePool?: boolean;
    tcBlackHasSearchLimit?: boolean;
    tcWhiteHasSearchLimit?: boolean;
    clockDisplayMode?: 'time' | 'fixed' | 'search' | 'unknown';
    pendingClockBySide?: {
        black?: {
            remainMs?: number;
            byoyomiMs?: number;
            timeControl?: string | null;
            receivedAtMs: number;
            source: 'clock_start' | 'clock_increment' | 'snapshot';
        };
        white?: {
            remainMs?: number;
            byoyomiMs?: number;
            timeControl?: string | null;
            receivedAtMs: number;
            source: 'clock_start' | 'clock_increment' | 'snapshot';
        };
    };
    lastSeenPly?: number;
    lastSideToMove?: 'black' | 'white' | null;
    lastAppliedPly?: number;
    lastAppliedAtMs?: number;
    lastCatchupRequestAt?: number;
}

export interface LiveBoardAdapter {
    mount(target: Element): void;
    setPositionFromSFEN?(sfen: string): void;
    setMoves?(moves: readonly string[]): void;
    goTo?(ply: number): void;
    resize?(): void;
    highlightSquares?(squares: Iterable<number>): void;
    destroy?(): void;
    currentPly?: number;
    [key: string]: unknown;
}

export type LiveClockIncrementSide = 'black' | 'white' | null;

export interface LiveClockState {
    blackRemainMs: number;
    whiteRemainMs: number;
    incSide: LiveClockIncrementSide;
    incMs: number;
    byoyomiBlack: number;
    byoyomiWhite: number;
}

export interface WorkerSnapshotSummary {
    data: WorkerSnapshot;
    maxPly: number;
    liveDisplayPly: number;
}
