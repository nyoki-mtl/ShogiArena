import type { LiveCardId } from '@/modules/live/types';
import type { WorkerSnapshot } from '@/types/live';

export type WorkerSnapshotRecord = WorkerSnapshot & {
    currentDisplayPly?: number;
    time_limit?: string | number | null;
};

export interface KifuHandlers {
    resultCodeToKifJP: (code: unknown) => string;
    ensureKifuLayers: () => void;
    positionKifuPopover: (anchorEl: Element | null) => void;
    scrollKifuToCurrent: () => void;
    renderKifuLinesCard: (cardId: LiveCardId, data: WorkerSnapshotRecord) => string;
    renderKifuLines: (workerIdx: number) => string;
    updateKI2MovesCard: (cardId: LiveCardId, data: WorkerSnapshotRecord) => void;
    updateKI2Moves: (workerIdx: number) => void;
    getMovePrefix: (workerIdx: number, moveIdx1: number) => '▲' | '△';
    getMovePrefixFromData: (data: WorkerSnapshotRecord, moveIdx1: number) => '▲' | '△';
    formatKifuLineSimple: (data: WorkerSnapshotRecord, idx1: number, options?: { includeNumber?: boolean }) => string;
    formatTerminalLineSimple: (
        data: WorkerSnapshotRecord,
        lastPly: number,
        options?: { includeNumber?: boolean },
    ) => string;
    updateMoveSummaryCard: (cardId: LiveCardId, data: WorkerSnapshotRecord) => void;
    formatMoveSummaryWithStats: (
        labelNum: string,
        moveText: string,
        data: WorkerSnapshotRecord,
        ply: number,
        variant?: 'summary' | 'popover',
    ) => string;
    formatTerminalLineWithStats: (
        data: WorkerSnapshotRecord,
        lastPly: number,
        variant?: 'summary' | 'popover',
    ) => string;
    extractSearchStats: (
        data: WorkerSnapshotRecord,
        ply: number,
    ) => {
        nodes: number | null;
        depth: number | null;
        seldepth: number | null;
        time_ms: number | null;
    };
    formatNodes: (nodes: number | null | undefined) => string;
    formatDepth: (depth: number | null | undefined, seldepth: number | null | undefined) => string;
    formatMs: (ms: number | null | undefined) => string;
    isDimmedByHighlight: (data: WorkerSnapshotRecord) => boolean;
    updateEvalChartCard: (cardId: LiveCardId, data: WorkerSnapshotRecord) => void;
    updateMoveSummary: (workerIdx: number) => void;
    toggleKifu: (workerIdx: number) => void;
    toggleKifuCard: (cardId: LiveCardId) => void;
    closeKifuPopover: () => void;
}
