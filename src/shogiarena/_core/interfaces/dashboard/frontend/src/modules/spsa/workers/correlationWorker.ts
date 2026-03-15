import type { NormalizedSpsaParams, NormalizedSpsaUpdateEntry, SpsaCorrelationResponse } from '@/modules/spsa/types';
import { buildParameterInsights } from '@/modules/spsa/components/analysis/correlation.state';

type CorrelationWorkerRequest = {
    id: number;
    payload: SpsaCorrelationResponse;
    paramsData: NormalizedSpsaParams | null;
    updatesEntries: readonly NormalizedSpsaUpdateEntry[] | null;
    initialEntry?: NormalizedSpsaUpdateEntry | null;
};

type CorrelationWorkerResponse = {
    type: 'ready' | 'result';
    id: number;
    insights?: ReturnType<typeof buildParameterInsights>;
    error?: string;
};

type WorkerContext = {
    postMessage: (message: CorrelationWorkerResponse) => void;
    onmessage: ((event: MessageEvent<CorrelationWorkerRequest>) => void) | null;
};

const ctx = self as unknown as WorkerContext;

ctx.postMessage({ type: 'ready', id: -1 });

ctx.onmessage = (event: MessageEvent<CorrelationWorkerRequest>) => {
    const { id, payload, paramsData, updatesEntries, initialEntry } = event.data;
    try {
        const insights = buildParameterInsights(payload, paramsData, updatesEntries, {
            initialEntry: initialEntry ?? null,
        });
        const response: CorrelationWorkerResponse = { type: 'result', id, insights };
        ctx.postMessage(response);
    } catch (error) {
        const response: CorrelationWorkerResponse = {
            type: 'result',
            id,
            error: error instanceof Error ? error.message : String(error),
        };
        ctx.postMessage(response);
    }
};
