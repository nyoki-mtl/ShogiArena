import type {
    NormalizedSpsaParams,
    NormalizedSpsaUpdateEntry,
    SpsaCorrelationResponse,
    SpsaConvergenceResponse,
    SpsaLtcResultsResponse,
} from '@/modules/spsa/types';
import type { ParameterInsight } from '@/modules/spsa/components/analysis/parameterShared';
import { buildParameterInsights } from '@/modules/spsa/components/analysis/correlation.state';
import {
    buildDeltaMetaLabel,
    computeConvergenceDomainSize,
    normalizeDeltaSeries,
    normalizeDirectionalSeries,
    normalizeLtcResults,
    resolveLtcYAxis,
} from '@/modules/spsa/components/analysis/convergence.compute';

export type ConvergenceComputePayload = {
    domainSize: number;
    windowSize: number | null;
    deltaSeries: number[];
    directionalSeries: number[];
    deltaMetaLabel: string;
    ltcProcessed?: ReturnType<typeof normalizeLtcResults>;
    ltcYAxis?: ReturnType<typeof resolveLtcYAxis>;
};

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
    insights?: ParameterInsight[];
    error?: string;
};

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

export type AnalysisPipeline = {
    computeCorrelation: (
        payload: SpsaCorrelationResponse,
        paramsData: NormalizedSpsaParams | null,
        updatesEntries: readonly NormalizedSpsaUpdateEntry[] | null,
        initialEntry?: NormalizedSpsaUpdateEntry | null,
    ) => Promise<ParameterInsight[]>;
    computeConvergence: (
        convergence: SpsaConvergenceResponse,
        ltcSnapshot: SpsaLtcResultsResponse | null,
    ) => Promise<ConvergenceComputePayload>;
};

export function createAnalysisPipeline(): AnalysisPipeline {
    let correlationWorker: Worker | null = null;
    let convergenceWorker: Worker | null = null;
    let correlationWorkerDisabled = false;
    let convergenceWorkerDisabled = false;
    let correlationWorkerReady: Promise<Worker | null> | null = null;
    let convergenceWorkerReady: Promise<Worker | null> | null = null;
    let correlationSeq = 0;
    let convergenceSeq = 0;
    const WORKER_TIMEOUT_MS = 5_000;
    const WORKER_READY_TIMEOUT_MS = 2_000;
    const correlationRequests = new Map<
        number,
        { resolve: (value: ParameterInsight[]) => void; reject: (reason?: unknown) => void; timeoutId: number }
    >();
    const convergenceRequests = new Map<
        number,
        { resolve: (value: ConvergenceComputePayload) => void; reject: (reason?: unknown) => void; timeoutId: number }
    >();

    const ensureCorrelationWorkerReady = (): Promise<Worker | null> => {
        if (correlationWorkerDisabled) {
            return Promise.resolve(null);
        }
        if (correlationWorkerReady) {
            return correlationWorkerReady;
        }
        if (correlationWorker) {
            return Promise.resolve(correlationWorker);
        }
        if (typeof Worker === 'undefined') {
            return Promise.resolve(null);
        }
        try {
            const worker = new Worker(new URL('../workers/correlationWorker.ts', import.meta.url), { type: 'module' });
            let resolveReady: ((value: Worker | null) => void) | null = null;
            let rejectReady: ((reason?: unknown) => void) | null = null;
            correlationWorkerReady = new Promise((resolve, reject) => {
                resolveReady = resolve;
                rejectReady = reject;
            });
            const readyTimer = window.setTimeout(() => {
                correlationWorkerDisabled = true;
                worker.terminate();
                correlationWorker = null;
                correlationWorkerReady = null;
                resolveReady?.(null);
            }, WORKER_READY_TIMEOUT_MS);
            worker.onmessage = (event: MessageEvent<CorrelationWorkerResponse>) => {
                if (event.data?.type === 'ready') {
                    window.clearTimeout(readyTimer);
                    resolveReady?.(worker);
                    return;
                }
                const { id, insights, error } = event.data;
                const pending = correlationRequests.get(id);
                if (!pending) return;
                window.clearTimeout(pending.timeoutId);
                correlationRequests.delete(id);
                if (error) {
                    pending.reject(new Error(error));
                    return;
                }
                pending.resolve(insights ?? []);
            };
            worker.onmessageerror = (event) => {
                console.warn('[SPSA] correlation analysis worker message error', event);
            };
            worker.onerror = (event) => {
                console.warn('[SPSA] correlation analysis worker failed', event);
                correlationRequests.forEach((pending, id) => {
                    window.clearTimeout(pending.timeoutId);
                    pending.reject(new Error('Correlation worker failed'));
                    correlationRequests.delete(id);
                });
                worker.terminate();
                correlationWorker = null;
                correlationWorkerDisabled = true;
                correlationWorkerReady = null;
                rejectReady?.(event);
            };
            correlationWorker = worker;
            return correlationWorkerReady;
        } catch (error) {
            console.warn('[SPSA] correlation analysis worker init failed', error);
            correlationWorker = null;
            correlationWorkerDisabled = true;
            correlationWorkerReady = null;
            return Promise.resolve(null);
        }
    };

    const ensureConvergenceWorkerReady = (): Promise<Worker | null> => {
        if (convergenceWorkerDisabled) {
            return Promise.resolve(null);
        }
        if (convergenceWorkerReady) {
            return convergenceWorkerReady;
        }
        if (convergenceWorker) {
            return Promise.resolve(convergenceWorker);
        }
        if (typeof Worker === 'undefined') {
            return Promise.resolve(null);
        }
        try {
            const worker = new Worker(new URL('../workers/convergenceWorker.ts', import.meta.url), { type: 'module' });
            let resolveReady: ((value: Worker | null) => void) | null = null;
            let rejectReady: ((reason?: unknown) => void) | null = null;
            convergenceWorkerReady = new Promise((resolve, reject) => {
                resolveReady = resolve;
                rejectReady = reject;
            });
            const readyTimer = window.setTimeout(() => {
                convergenceWorkerDisabled = true;
                worker.terminate();
                convergenceWorker = null;
                convergenceWorkerReady = null;
                resolveReady?.(null);
            }, WORKER_READY_TIMEOUT_MS);
            worker.onmessage = (event: MessageEvent<ConvergenceWorkerResponse>) => {
                if (event.data?.type === 'ready') {
                    window.clearTimeout(readyTimer);
                    resolveReady?.(worker);
                    return;
                }
                const { id, error, ...payload } = event.data;
                const pending = convergenceRequests.get(id);
                if (!pending) return;
                window.clearTimeout(pending.timeoutId);
                convergenceRequests.delete(id);
                if (error) {
                    pending.reject(new Error(error));
                    return;
                }
                pending.resolve(payload as ConvergenceComputePayload);
            };
            worker.onmessageerror = (event) => {
                console.warn('[SPSA] convergence analysis worker message error', event);
            };
            worker.onerror = (event) => {
                console.warn('[SPSA] convergence analysis worker failed', event);
                convergenceRequests.forEach((pending, id) => {
                    window.clearTimeout(pending.timeoutId);
                    pending.reject(new Error('Convergence worker failed'));
                    convergenceRequests.delete(id);
                });
                worker.terminate();
                convergenceWorker = null;
                convergenceWorkerDisabled = true;
                convergenceWorkerReady = null;
                rejectReady?.(event);
            };
            convergenceWorker = worker;
            return convergenceWorkerReady;
        } catch (error) {
            console.warn('[SPSA] convergence analysis worker init failed', error);
            convergenceWorker = null;
            convergenceWorkerDisabled = true;
            convergenceWorkerReady = null;
            return Promise.resolve(null);
        }
    };

    const computeCorrelation = async (
        payload: SpsaCorrelationResponse,
        paramsData: NormalizedSpsaParams | null,
        updatesEntries: readonly NormalizedSpsaUpdateEntry[] | null,
        initialEntry: NormalizedSpsaUpdateEntry | null = null,
    ): Promise<ParameterInsight[]> => {
        const worker = await ensureCorrelationWorkerReady();
        if (!worker) {
            return Promise.resolve(buildParameterInsights(payload, paramsData, updatesEntries, { initialEntry }));
        }
        correlationSeq += 1;
        const id = correlationSeq;
        const request: CorrelationWorkerRequest = {
            id,
            payload,
            paramsData,
            updatesEntries,
            initialEntry,
        };
        return new Promise((resolve, reject) => {
            const timeoutId = window.setTimeout(() => {
                correlationRequests.delete(id);
                if (correlationWorker) {
                    correlationWorker.terminate();
                }
                correlationWorker = null;
                correlationWorkerDisabled = true;
                reject(new Error('Correlation worker timeout'));
            }, WORKER_TIMEOUT_MS);
            correlationRequests.set(id, { resolve, reject, timeoutId });
            worker.postMessage(request);
        });
    };

    const computeConvergence = async (
        convergence: SpsaConvergenceResponse,
        ltcSnapshot: SpsaLtcResultsResponse | null,
    ): Promise<ConvergenceComputePayload> => {
        const worker = await ensureConvergenceWorkerReady();
        if (!worker) {
            const domainSize = computeConvergenceDomainSize(convergence);
            const deltaSeries = normalizeDeltaSeries(convergence.delta_norm_history ?? []);
            const directionalSeries = normalizeDirectionalSeries(convergence.delta_mean_vector_norm_history ?? []);
            const windowSize =
                typeof convergence.delta_mean_vector_window === 'number' ? convergence.delta_mean_vector_window : null;
            const deltaMetaLabel = buildDeltaMetaLabel(deltaSeries, directionalSeries, windowSize);
            let ltcProcessed: ReturnType<typeof normalizeLtcResults> | undefined;
            let ltcYAxis: ReturnType<typeof resolveLtcYAxis> | undefined;
            if (ltcSnapshot?.results) {
                ltcProcessed = normalizeLtcResults(ltcSnapshot.results);
                ltcYAxis = resolveLtcYAxis(ltcProcessed);
            }
            return Promise.resolve({
                domainSize,
                windowSize,
                deltaSeries,
                directionalSeries,
                deltaMetaLabel,
                ltcProcessed,
                ltcYAxis,
            });
        }
        convergenceSeq += 1;
        const id = convergenceSeq;
        const request: ConvergenceWorkerRequest = {
            id,
            convergence,
            ltcSnapshot,
        };
        return new Promise((resolve, reject) => {
            const timeoutId = window.setTimeout(() => {
                convergenceRequests.delete(id);
                if (convergenceWorker) {
                    convergenceWorker.terminate();
                }
                convergenceWorker = null;
                convergenceWorkerDisabled = true;
                reject(new Error('Convergence worker timeout'));
            }, WORKER_TIMEOUT_MS);
            convergenceRequests.set(id, { resolve, reject, timeoutId });
            worker.postMessage(request);
        });
    };

    return {
        computeCorrelation,
        computeConvergence,
    };
}

let cachedPipeline: AnalysisPipeline | null = null;

export function getAnalysisPipeline(): AnalysisPipeline {
    if (!cachedPipeline) {
        cachedPipeline = createAnalysisPipeline();
    }
    return cachedPipeline;
}
