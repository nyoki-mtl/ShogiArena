import type { SpsaConvergenceResponse, SpsaLtcResultsResponse } from '@/modules/spsa/types';
import {
    buildDeltaMetaLabel,
    computeConvergenceDomainSize,
    normalizeDeltaSeries,
    normalizeDirectionalSeries,
    normalizeLtcResults,
    resolveLtcYAxis,
} from '@/modules/spsa/components/analysis/convergence.compute';

type ConvergenceWorkerRequest = {
    id: number;
    convergence: SpsaConvergenceResponse;
    ltcSnapshot?: SpsaLtcResultsResponse | null;
};

type ConvergenceWorkerResponse = {
    type: 'ready' | 'result';
    id: number;
    domainSize: number;
    windowSize: number | null;
    deltaSeries: number[];
    directionalSeries: number[];
    deltaMetaLabel: string;
    ltcProcessed?: ReturnType<typeof normalizeLtcResults>;
    ltcYAxis?: ReturnType<typeof resolveLtcYAxis>;
    error?: string;
};

type WorkerContext = {
    postMessage: (message: ConvergenceWorkerResponse) => void;
    onmessage: ((event: MessageEvent<ConvergenceWorkerRequest>) => void) | null;
};

const ctx = self as unknown as WorkerContext;

ctx.postMessage({
    type: 'ready',
    id: -1,
    domainSize: 0,
    windowSize: null,
    deltaSeries: [],
    directionalSeries: [],
    deltaMetaLabel: '',
});

ctx.onmessage = (event: MessageEvent<ConvergenceWorkerRequest>) => {
    const { id, convergence, ltcSnapshot } = event.data;
    try {
        const deltaSeries = normalizeDeltaSeries(convergence.delta_norm_history ?? []);
        const directionalSeries = normalizeDirectionalSeries(convergence.delta_mean_vector_norm_history ?? []);
        const windowSize =
            typeof convergence.delta_mean_vector_window === 'number' ? convergence.delta_mean_vector_window : null;
        const domainSize = computeConvergenceDomainSize(convergence);
        const deltaMetaLabel = buildDeltaMetaLabel(deltaSeries, directionalSeries, windowSize);
        const response: ConvergenceWorkerResponse = {
            type: 'result',
            id,
            domainSize,
            windowSize,
            deltaSeries,
            directionalSeries,
            deltaMetaLabel,
        };
        if (ltcSnapshot?.results && Array.isArray(ltcSnapshot.results)) {
            const ltcProcessed = normalizeLtcResults(ltcSnapshot.results);
            const ltcYAxis = resolveLtcYAxis(ltcProcessed);
            response.ltcProcessed = ltcProcessed;
            response.ltcYAxis = ltcYAxis;
        }
        ctx.postMessage(response);
    } catch (error) {
        const response: ConvergenceWorkerResponse = {
            type: 'result',
            id,
            domainSize: 1,
            windowSize: null,
            deltaSeries: [],
            directionalSeries: [],
            deltaMetaLabel: '',
            error: error instanceof Error ? error.message : String(error),
        };
        ctx.postMessage(response);
    }
};
