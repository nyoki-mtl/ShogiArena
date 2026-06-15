import type { SpsaConvergenceResponse, SpsaLtcResultsResponse } from '@/modules/spsa/types';
import { renderMobility } from '../mobility';
import { getDashboardSpsaApi, resolveNumeric } from './shared';
import {
    clearConvergenceContent,
    ensureConvergenceLayout,
    renderConvergenceError as renderConvergenceErrorLayout,
    renderConvergenceLoading as renderConvergenceLoadingLayout,
    resetConvergenceCharts,
} from './convergence-layout';
import { renderDeltaNormHistoryChartNumeric } from './convergence-delta-chart';
import { renderLtcEloChart, renderLtcEloChartProcessed } from './convergence-ltc-chart';
import {
    buildDeltaMetaLabel,
    computeConvergenceDomainSize,
    normalizeDeltaSeries,
    normalizeDirectionalSeries,
    normalizeLtcResults,
    resolveLtcYAxis,
} from './convergence-compute';

let lastConvergenceDomainSize: number | null = null;
let convergenceComputeSeq = 0;
let convergenceWorker: Worker | null = null;
let convergenceWorkerDisabled = false;
let convergenceWorkerSeq = 0;
let convergenceWorkerReady: Promise<Worker | null> | null = null;
const CONVERGENCE_WORKER_TIMEOUT_MS = 5_000;
const convergenceWorkerRequests = new Map<
    number,
    {
        resolve: (value: ConvergenceWorkerPayload) => void;
        reject: (reason?: unknown) => void;
        timeoutId: number;
    }
>();

type ConvergenceWorkerPayload = {
    domainSize: number;
    windowSize: number | null;
    deltaSeries: number[];
    directionalSeries: number[];
    deltaMetaLabel: string;
    ltcProcessed?: ReturnType<typeof normalizeLtcResults>;
    ltcYAxis?: ReturnType<typeof resolveLtcYAxis>;
};

export function renderConvergenceLoading(): void {
    renderConvergenceLoadingLayout();
    resetConvergenceCharts('loading');
}

export function renderConvergenceError(message: string): void {
    renderConvergenceErrorLayout(message);
    resetConvergenceCharts(message);
}

export function renderConvergenceResult(data: SpsaConvergenceResponse): void {
    const layout = ensureConvergenceLayout();
    if (!layout) return;

    clearConvergenceContent();

    const api = getDashboardSpsaApi();
    const ltcSnapshot = data.ltc_results ?? api?.getLtcResultsSnapshot?.(100) ?? null;
    convergenceComputeSeq += 1;
    const computeId = convergenceComputeSeq;
    computeConvergencePayload(data, ltcSnapshot)
        .then((payload) => {
            if (computeId !== convergenceComputeSeq) {
                return;
            }
            renderConvergenceComputed(payload, ltcSnapshot);
        })
        .catch((error) => {
            if (computeId !== convergenceComputeSeq) {
                return;
            }
            console.warn('[SPSA] convergence compute failed, falling back to main thread', error);
            const domainSize = computeConvergenceDomainSize(data);
            const deltaSeries = normalizeDeltaSeries(data.delta_norm_history ?? []);
            const directionalSeries = normalizeDirectionalSeries(data.delta_mean_vector_norm_history ?? []);
            const windowSize = resolveNumeric(data.delta_mean_vector_window);
            const deltaMetaLabel = buildDeltaMetaLabel(deltaSeries, directionalSeries, windowSize);
            renderConvergenceComputed(
                {
                    domainSize,
                    windowSize: typeof windowSize === 'number' ? windowSize : null,
                    deltaSeries,
                    directionalSeries,
                    deltaMetaLabel,
                },
                ltcSnapshot,
            );
        });
}

export function renderLtcResultsUpdate(snapshot?: SpsaLtcResultsResponse | null): void {
    if (!snapshot) {
        return;
    }
    renderLtcEloChart(snapshot.results, snapshot.summary, { domainSize: lastConvergenceDomainSize ?? undefined });
}

export function renderConvergenceComputed(
    payload: ConvergenceWorkerPayload,
    ltcSnapshot: SpsaLtcResultsResponse | null,
): void {
    clearConvergenceContent();
    lastConvergenceDomainSize = payload.domainSize;
    renderDeltaNormHistoryChartNumeric(payload.deltaSeries, payload.directionalSeries, {
        domainSize: payload.domainSize,
        windowSize: payload.windowSize,
        metaLabel: payload.deltaMetaLabel,
    });
    const api = getDashboardSpsaApi();
    if (api?.getStateSnapshot) {
        renderMobility(api.getStateSnapshot());
    }
    if (ltcSnapshot) {
        const processed = payload.ltcProcessed ?? normalizeLtcResults(ltcSnapshot.results);
        const yAxis = payload.ltcYAxis ?? resolveLtcYAxis(processed);
        renderLtcEloChartProcessed(processed, ltcSnapshot.summary, {
            domainSize: payload.domainSize,
            yAxis,
        });
    } else {
        renderLtcEloChart([], null, { domainSize: payload.domainSize });
    }
}

function getConvergenceWorker(): Worker | null {
    if (convergenceWorkerDisabled) return null;
    if (convergenceWorker) return convergenceWorker;
    if (typeof Worker === 'undefined') {
        return null;
    }
    try {
        const worker = new Worker(new URL('../../workers/convergence-worker.ts', import.meta.url), { type: 'module' });
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
        }, CONVERGENCE_WORKER_TIMEOUT_MS);
        worker.onmessage = (
            event: MessageEvent<{ type?: 'ready' | 'result'; id: number; error?: string } & ConvergenceWorkerPayload>,
        ) => {
            if (event.data?.type === 'ready') {
                window.clearTimeout(readyTimer);
                resolveReady?.(worker);
                return;
            }
            const { id, error, ...payload } = event.data;
            const pending = convergenceWorkerRequests.get(id);
            if (!pending) return;
            window.clearTimeout(pending.timeoutId);
            convergenceWorkerRequests.delete(id);
            if (error) {
                pending.reject(new Error(error));
                return;
            }
            pending.resolve(payload as ConvergenceWorkerPayload);
        };
        worker.onmessageerror = (event) => {
            console.warn('[SPSA] convergence worker message error', event);
        };
        worker.onerror = (event) => {
            console.warn('[SPSA] convergence worker failed', event);
            convergenceWorkerRequests.forEach((pending, requestId) => {
                window.clearTimeout(pending.timeoutId);
                pending.reject(new Error('Convergence worker failed'));
                convergenceWorkerRequests.delete(requestId);
            });
            worker.terminate();
            convergenceWorker = null;
            convergenceWorkerDisabled = true;
            convergenceWorkerReady = null;
            rejectReady?.(event);
        };
        convergenceWorker = worker;
        return worker;
    } catch (error) {
        console.warn('[SPSA] convergence worker init failed', error);
        convergenceWorker = null;
        convergenceWorkerDisabled = true;
        convergenceWorkerReady = null;
        return null;
    }
}

function computeConvergencePayload(
    data: SpsaConvergenceResponse,
    ltcSnapshot: SpsaLtcResultsResponse | null,
): Promise<ConvergenceWorkerPayload> {
    const worker = getConvergenceWorker();
    if (!worker) {
        const domainSize = computeConvergenceDomainSize(data);
        const deltaSeries = normalizeDeltaSeries(data.delta_norm_history ?? []);
        const directionalSeries = normalizeDirectionalSeries(data.delta_mean_vector_norm_history ?? []);
        const windowSize = resolveNumeric(data.delta_mean_vector_window);
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
    convergenceWorkerSeq += 1;
    const id = convergenceWorkerSeq;
    return new Promise<ConvergenceWorkerPayload>((resolve, reject) => {
        const readyPromise = convergenceWorkerReady;
        const sendRequest = () => {
            const timeoutId = window.setTimeout(() => {
                convergenceWorkerRequests.delete(id);
                if (convergenceWorker) {
                    convergenceWorker.terminate();
                }
                // Recreate on next call instead of permanently disabling: a single slow compute
                // must not degrade the whole session to main-thread fallback.
                convergenceWorker = null;
                convergenceWorkerReady = null;
                reject(new Error('Convergence worker timeout'));
            }, CONVERGENCE_WORKER_TIMEOUT_MS);
            convergenceWorkerRequests.set(id, { resolve, reject, timeoutId });
            worker.postMessage({ id, convergence: data, ltcSnapshot });
        };
        if (readyPromise) {
            readyPromise
                .then((readyWorker) => {
                    if (!readyWorker) {
                        reject(new Error('Convergence worker unavailable'));
                        return;
                    }
                    sendRequest();
                })
                .catch((error) => {
                    reject(error instanceof Error ? error : new Error(String(error)));
                });
            return;
        }
        sendRequest();
    });
}
