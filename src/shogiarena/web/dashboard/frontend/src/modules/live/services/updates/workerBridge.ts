import type { LiveEnvelope } from './wsTypes';

type SnapshotRequestHandler = (topic: string, fromSeq?: number | null, reasonCode?: number | null) => void;
type VmHandler = (vm: WorkerViewModelMessage) => void;

export type WorkerViewModelMessage = {
    workerIdx: number;
    gid: string | null;
    currentPly: number | null;
    sfen: string | null;
    lastMove: string | null;
    ki2Move: string | null;
    evalCp: number | null;
    clock?: Record<string, unknown>;
    snapshot?: Record<string, unknown>;
    snapshotDelta?: Record<string, unknown>;
};

export interface MergeWorkerBridge {
    handleEnvelope: (envelope: LiveEnvelope) => void;
    reset: (reason?: string) => void;
    dispose: () => void;
    requestDiagnostics: () => void;
}

export function createMergeWorkerBridge(
    onRequestSnapshot: SnapshotRequestHandler,
    onVm?: VmHandler,
): MergeWorkerBridge {
    if (typeof Worker !== 'function') {
        throw new Error('Live updates require Web Worker support (Merge Worker is mandatory).');
    }
    let worker: Worker | null = null;
    try {
        worker = new Worker(new URL('./worker/liveMergeWorker.ts', import.meta.url), { type: 'module' });
    } catch (err) {
        throw new Error('Failed to start liveMergeWorker.', { cause: err instanceof Error ? err : undefined });
    }

    const handleEnvelope = (envelope: LiveEnvelope) => {
        worker?.postMessage(envelope);
    };
    const reset = (reason?: string) => {
        worker?.postMessage({ type: 'reset', reason });
    };
    const requestDiagnostics = () => {
        worker?.postMessage({ type: 'diagnostics' });
    };

    const onMessage = (event: MessageEvent) => {
        const data = event.data;
        if (!data || typeof data !== 'object') return;
        if (data.type === 'request_snapshot' && typeof data.topic === 'string') {
            const reasonCode = typeof data.reason_code === 'number' ? data.reason_code : null;
            onRequestSnapshot(data.topic, typeof data.fromSeq === 'number' ? data.fromSeq : null, reasonCode);
        }
        if (data.type === 'vm' && onVm) {
            onVm(data.payload);
        }
    };

    worker.addEventListener('message', onMessage);

    const dispose = () => {
        worker?.removeEventListener('message', onMessage);
        worker?.terminate();
        worker = null;
    };

    return { handleEnvelope, reset, dispose, requestDiagnostics };
}
