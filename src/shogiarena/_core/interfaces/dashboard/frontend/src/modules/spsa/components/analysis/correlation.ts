import { getState } from '@/modules/spsa/state';
import { escapeHtml } from '@/modules/shared/utils/html';
import type { SpsaCorrelationResponse } from '@/modules/spsa/types';
import {
    ensureCorrelationLayout,
    resetParameterDetail,
    renderCorrelationErrorLayout,
    renderCorrelationLoadingLayout,
    getCorrelationContainer,
    PARAMETER_DETAIL_CONTAINER_ID,
    PARAMETER_DETAIL_TITLE_ID,
} from './correlation-layout';
import {
    applyParameterSort,
    buildParameterTable,
    isParameterSortKey,
    sortParameterInsights,
} from './correlation-table';
import { buildChartData, renderParameterChart } from './correlation-charts';
import {
    buildParameterInsights,
    getParameterInsight,
    getParameterInsights,
    getParameterSortState,
    getSelectedParameter,
    hasParameterInsight,
    resetCorrelationState,
    resolvePreferredSelection,
    setParameterInsights,
    setPendingParameterSelection,
    setSelectedParameter,
    updateParameterSortState,
} from './correlation-state';
import type { ParameterCorrelationEntry, ParameterInsight } from './parameter-shared';

let tableInteractionController: AbortController | null = null;
let parameterTableHost: HTMLElement | null = null;
let pendingCorrelationPayload: SpsaCorrelationResponse | null = null;
let pendingCorrelationInsights: ParameterInsight[] | null = null;
let pendingCorrelationResolveTimer: number | null = null;
let pendingCorrelationLayoutRetryScheduled = false;
const PENDING_CORRELATION_RECHECK_MS = 250;
let correlationComputeSeq = 0;
let correlationWorker: Worker | null = null;
let correlationWorkerDisabled = false;
let correlationWorkerSeq = 0;
let correlationWorkerReady: Promise<Worker | null> | null = null;
const CORRELATION_WORKER_TIMEOUT_MS = 5_000;
const correlationWorkerRequests = new Map<
    number,
    { resolve: (value: ParameterInsight[]) => void; reject: (reason?: unknown) => void; timeoutId: number }
>();

export function focusParameterInAnalysis(name: string, options?: { scrollIntoView?: boolean }): void {
    const trimmed = typeof name === 'string' ? name.trim() : '';
    if (!trimmed) return;
    setPendingParameterSelection({
        name: trimmed,
        scrollIntoView: options?.scrollIntoView ?? false,
    });
    if (hasParameterInsight(trimmed)) {
        selectParameterRow(trimmed, { scrollIntoView: options?.scrollIntoView ?? false });
        setPendingParameterSelection(null);
    }
}

export function renderCorrelationLoading(): void {
    resetCorrelationState();
    clearCorrelationSortState();
    renderCorrelationLoadingLayout();
    const layout = ensureCorrelationLayout();
    if (!layout) return;
    parameterTableHost = layout.tableHost;
}

export function renderCorrelationError(message: string): void {
    renderCorrelationErrorLayout(message);
    const layout = ensureCorrelationLayout();
    if (!layout) return;
    parameterTableHost = layout.tableHost;
    resetCorrelationState();
    clearCorrelationSortState();
}

export function flushPendingCorrelationResult(): boolean {
    if (!pendingCorrelationPayload) {
        return flushPendingCorrelationInsights();
    }
    clearPendingCorrelationResolve();
    const snapshot = pendingCorrelationPayload;
    pendingCorrelationPayload = null;
    renderCorrelationResult(snapshot);
    return true;
}

export function renderCorrelationInsights(insights: ParameterInsight[]): void {
    const layout = ensureCorrelationLayout();
    if (!layout) {
        pendingCorrelationInsights = insights;
        scheduleCorrelationLayoutRetry();
        console.warn('[SPSA] renderCorrelationInsights deferred: layout unavailable');
        return;
    }
    pendingCorrelationPayload = null;
    pendingCorrelationInsights = null;
    parameterTableHost = layout.tableHost;
    applyCorrelationInsights(insights);
}

export function renderCorrelationResult(data: SpsaCorrelationResponse): void {
    const layout = ensureCorrelationLayout();
    if (!layout) {
        pendingCorrelationPayload = data;
        scheduleCorrelationLayoutRetry();
        console.warn('[SPSA] renderCorrelationResult deferred: layout unavailable');
        return;
    }
    const state = getState();
    const paramsData = state.params.data ?? null;
    if (!paramsData) {
        if (state.params.status === 'error') {
            renderCorrelationError(state.params.error || 'Failed to load parameters');
            return;
        }
        pendingCorrelationPayload = data;
        renderCorrelationLoading();
        schedulePendingCorrelationResolve();
        return;
    }
    clearPendingCorrelationResolve();
    const updatesEntries = state.updates.entries ?? [];
    const worker = getCorrelationWorker();
    if (!worker) {
        const insights = buildParameterInsights(data, paramsData, updatesEntries, {
            initialEntry: state.updates.initialEntry ?? null,
        });
        pendingCorrelationPayload = null;
        pendingCorrelationInsights = null;
        parameterTableHost = layout.tableHost;
        applyCorrelationInsights(insights);
        return;
    }
    correlationComputeSeq += 1;
    const computeId = correlationComputeSeq;
    computeParameterInsightsWithWorker(worker, data, paramsData, updatesEntries, state.updates.initialEntry ?? null)
        .then((insights) => {
            if (computeId !== correlationComputeSeq) {
                return;
            }
            pendingCorrelationPayload = null;
            pendingCorrelationInsights = null;
            parameterTableHost = layout.tableHost;
            applyCorrelationInsights(insights);
        })
        .catch((error) => {
            if (computeId !== correlationComputeSeq) {
                return;
            }
            console.warn('[SPSA] correlation compute failed, falling back to main thread', error);
            const insights = buildParameterInsights(data, paramsData, updatesEntries, {
                initialEntry: state.updates.initialEntry ?? null,
            });
            pendingCorrelationPayload = null;
            pendingCorrelationInsights = null;
            parameterTableHost = layout.tableHost;
            applyCorrelationInsights(insights);
        });
}

function getCorrelationWorker(): Worker | null {
    if (correlationWorkerDisabled) return null;
    if (correlationWorker) return correlationWorker;
    if (typeof Worker === 'undefined') {
        return null;
    }
    try {
        const worker = new Worker(new URL('../../workers/correlation-worker.ts', import.meta.url), { type: 'module' });
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
        }, CORRELATION_WORKER_TIMEOUT_MS);
        worker.onmessage = (
            event: MessageEvent<{
                type?: 'ready' | 'result';
                id: number;
                insights?: ParameterInsight[];
                error?: string;
            }>,
        ) => {
            if (event.data?.type === 'ready') {
                window.clearTimeout(readyTimer);
                resolveReady?.(worker);
                return;
            }
            const { id, insights, error } = event.data;
            const pending = correlationWorkerRequests.get(id);
            if (!pending) return;
            window.clearTimeout(pending.timeoutId);
            correlationWorkerRequests.delete(id);
            if (error) {
                pending.reject(new Error(error));
                return;
            }
            pending.resolve(insights ?? []);
        };
        worker.onmessageerror = (event) => {
            console.warn('[SPSA] correlation worker message error', event);
        };
        worker.onerror = (event) => {
            console.warn('[SPSA] correlation worker failed', event);
            correlationWorkerRequests.forEach((pending, requestId) => {
                window.clearTimeout(pending.timeoutId);
                pending.reject(new Error('Correlation worker failed'));
                correlationWorkerRequests.delete(requestId);
            });
            worker.terminate();
            correlationWorker = null;
            correlationWorkerDisabled = true;
            correlationWorkerReady = null;
            rejectReady?.(event);
        };
        correlationWorker = worker;
        return worker;
    } catch (error) {
        console.warn('[SPSA] correlation worker init failed', error);
        correlationWorker = null;
        correlationWorkerDisabled = true;
        correlationWorkerReady = null;
        return null;
    }
}

function computeParameterInsightsWithWorker(
    worker: Worker,
    data: SpsaCorrelationResponse,
    paramsData: ReturnType<typeof getState>['params']['data'],
    updatesEntries: ReturnType<typeof getState>['updates']['entries'],
    initialEntry: ReturnType<typeof getState>['updates']['initialEntry'],
): Promise<ParameterInsight[]> {
    correlationWorkerSeq += 1;
    const id = correlationWorkerSeq;
    const payload = {
        id,
        payload: data,
        paramsData,
        updatesEntries,
        initialEntry,
    };
    return new Promise<ParameterInsight[]>((resolve, reject) => {
        const readyPromise = correlationWorkerReady;
        const sendRequest = () => {
            const timeoutId = window.setTimeout(() => {
                correlationWorkerRequests.delete(id);
                if (correlationWorker) {
                    correlationWorker.terminate();
                }
                // A single slow computation must not permanently disable the worker for the whole
                // session (unlike a ready-timeout/onerror). Drop the instance so the next request
                // recreates a fresh worker; leave correlationWorkerDisabled untouched.
                correlationWorker = null;
                correlationWorkerReady = null;
                reject(new Error('Correlation worker timeout'));
            }, CORRELATION_WORKER_TIMEOUT_MS);
            correlationWorkerRequests.set(id, { resolve, reject, timeoutId });
            worker.postMessage(payload);
        };
        if (readyPromise) {
            readyPromise
                .then((readyWorker) => {
                    if (!readyWorker) {
                        reject(new Error('Correlation worker unavailable'));
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

function schedulePendingCorrelationResolve(): void {
    if (pendingCorrelationResolveTimer !== null) {
        return;
    }
    const tick = (): void => {
        pendingCorrelationResolveTimer = null;
        if (!pendingCorrelationPayload) {
            return;
        }
        const currentState = getState();
        if (currentState.params.data) {
            flushPendingCorrelationResult();
            return;
        }
        if (currentState.params.status === 'error') {
            renderCorrelationError(currentState.params.error || 'Failed to load parameters');
            pendingCorrelationPayload = null;
            return;
        }
        pendingCorrelationResolveTimer = window.setTimeout(tick, PENDING_CORRELATION_RECHECK_MS);
    };
    pendingCorrelationResolveTimer = window.setTimeout(tick, PENDING_CORRELATION_RECHECK_MS);
}

function clearPendingCorrelationResolve(): void {
    if (pendingCorrelationResolveTimer !== null) {
        window.clearTimeout(pendingCorrelationResolveTimer);
        pendingCorrelationResolveTimer = null;
    }
}

function repaintParameterTable(): void {
    if (!parameterTableHost) return;
    const sorted = getSortedParameterInsights();
    parameterTableHost.innerHTML = buildParameterTable(sorted, getParameterSortState());
    tableInteractionController?.abort();
    bindParameterTableInteractions(parameterTableHost);
    const selected = getSelectedParameter();
    if (selected) {
        updateRowSelection(selected);
    }
}

function getSortedParameterInsights(): ParameterInsight[] {
    return sortParameterInsights(getParameterInsights(), getParameterSortState());
}

// Correlation table sort types and state
type CorrelationSortKey = 'name' | 'value' | 'absValue';
type CorrelationSortDirection = 'asc' | 'desc';
type CorrelationSortState = {
    key: CorrelationSortKey;
    direction: CorrelationSortDirection;
};

// Default: sort by |correlation| descending (strongest first)
const DEFAULT_CORRELATION_SORT: CorrelationSortState = { key: 'absValue', direction: 'desc' };

// Per-parameter sort state cache
const correlationSortStateMap = new Map<string, CorrelationSortState>();

function clearCorrelationSortState(): void {
    correlationSortStateMap.clear();
}

function getCorrelationSortState(parameterName: string): CorrelationSortState {
    return correlationSortStateMap.get(parameterName) ?? DEFAULT_CORRELATION_SORT;
}

function setCorrelationSortState(parameterName: string, state: CorrelationSortState): void {
    correlationSortStateMap.set(parameterName, state);
}

function cycleCorrelationSort(parameterName: string, key: CorrelationSortKey): CorrelationSortState {
    const current = getCorrelationSortState(parameterName);
    if (current.key === key) {
        // Toggle direction
        return { key, direction: current.direction === 'asc' ? 'desc' : 'asc' };
    }
    // New key: default to desc for value/absValue, asc for name
    const defaultDirection: CorrelationSortDirection = key === 'name' ? 'asc' : 'desc';
    return { key, direction: defaultDirection };
}

function sortCorrelationEntries(
    entries: readonly ParameterCorrelationEntry[],
    sortState: CorrelationSortState,
): ParameterCorrelationEntry[] {
    const sorted = [...entries];
    sorted.sort((a, b) => {
        let cmp: number;
        switch (sortState.key) {
            case 'name':
                cmp = a.name.localeCompare(b.name);
                break;
            case 'value':
                cmp = a.value - b.value;
                break;
            default:
                // 'absValue' or fallback
                cmp = Math.abs(a.value) - Math.abs(b.value);
                break;
        }
        return sortState.direction === 'asc' ? cmp : -cmp;
    });
    return sorted;
}

function renderCorrelationSortButton(
    key: CorrelationSortKey,
    label: string,
    currentState: CorrelationSortState,
    extraClass?: string,
): string {
    const isActive = currentState.key === key;
    const indicator = isActive ? (currentState.direction === 'asc' ? '↑' : '↓') : '↕';
    const activeClass = isActive ? ' is-active' : '';
    const classList = `corr-sort-button${activeClass}${extraClass ? ` ${extraClass}` : ''}`;
    return `
        <button type="button" class="${classList}" data-corr-sort-key="${key}">
            <span>${label}</span>
            <span class="sort-indicator" aria-hidden="true">${indicator}</span>
        </button>
    `;
}

function renderCorrelationTable(
    entries: readonly ParameterCorrelationEntry[],
    sampleCount?: number | null,
    parameterName?: string,
): string {
    if (!entries.length) return '<p class="muted text-sm">No correlated parameters.</p>';

    const sortState = parameterName ? getCorrelationSortState(parameterName) : DEFAULT_CORRELATION_SORT;
    const sortedEntries = sortCorrelationEntries(entries, sortState);

    const sampleInfo =
        typeof sampleCount === 'number' && sampleCount > 0
            ? `<span class="corr-sample-count">(${sampleCount} tracked)</span>`
            : '';

    const rows = sortedEntries
        .map((entry, index) => {
            const signClass = entry.value >= 0 ? 'metric-positive' : 'metric-negative';
            const valueLabel = `${entry.value >= 0 ? '+' : ''}${entry.value.toFixed(3)}`;
            const rank = index + 1;
            return `
            <tr>
                <td class="corr-rank-cell">${rank}</td>
                <td>${escapeHtml(entry.name)}</td>
                <td><span class="${signClass}">${valueLabel}</span></td>
            </tr>
        `;
        })
        .join('');

    const nameButton = renderCorrelationSortButton('name', 'Parameter', sortState);
    const valueButton = renderCorrelationSortButton('value', 'Value', sortState, 'corr-sort-value');
    const absValueButton = renderCorrelationSortButton('absValue', '|Value|', sortState, 'corr-sort-abs');

    return `
        <div class="corr-table-scroll-wrapper" data-corr-param="${escapeHtml(parameterName ?? '')}">
            <table class="table-simple correlation-detail-table">
                <thead>
                    <tr>
                        <th class="corr-rank-header">#</th>
                        <th>${nameButton}</th>
                        <th>
                            <div class="corr-header-group">
                                <span>Correlation</span>
                                ${sampleInfo}
                                <div class="corr-sort-group">
                                    ${valueButton}
                                    ${absValueButton}
                                </div>
                            </div>
                        </th>
                    </tr>
                </thead>
                <tbody>
                    ${rows}
                </tbody>
            </table>
        </div>
    `;
}

function bindParameterTableInteractions(host: HTMLElement): void {
    const controller = new AbortController();
    tableInteractionController = controller;
    host.addEventListener(
        'click',
        (event) => {
            const rawTarget = event.target as HTMLElement | null;
            if (!rawTarget) return;
            const sortButton = rawTarget.closest<HTMLButtonElement>('button[data-sort-key]');
            if (sortButton && host.contains(sortButton)) {
                const sortKey = sortButton.dataset.sortKey;
                if (isParameterSortKey(sortKey)) {
                    event.preventDefault();
                    const next = applyParameterSort(getParameterSortState(), sortKey);
                    updateParameterSortState(next);
                    repaintParameterTable();
                }
                return;
            }
            const row = rawTarget.closest<HTMLTableRowElement>('tr[data-parameter-row]');
            if (!row || !host.contains(row)) return;
            const name = row.dataset.parameterRow;
            if (name) {
                event.preventDefault();
                selectParameterRow(name);
            }
        },
        { signal: controller.signal },
    );
}

function selectParameterRow(name: string, options?: { scrollIntoView?: boolean }): void {
    if (!hasParameterInsight(name)) return;
    setSelectedParameter(name);
    updateRowSelection(name);
    renderParameterDetail(getParameterInsight(name));
    if (options?.scrollIntoView) {
        const container = getCorrelationContainer();
        const rows = container?.querySelectorAll<HTMLTableRowElement>('tr[data-parameter-row]');
        if (rows?.length) {
            for (let index = 0; index < rows.length; index += 1) {
                const row = rows[index];
                if (row.dataset.parameterRow === name) {
                    const supportsSmoothScroll =
                        document.documentElement != null && 'scrollBehavior' in document.documentElement.style;
                    if (supportsSmoothScroll) {
                        row.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
                    } else {
                        row.scrollIntoView();
                    }
                    break;
                }
            }
        }
    }
}

function updateRowSelection(activeName: string | null): void {
    const container = getCorrelationContainer();
    if (!container) return;
    const rows = container.querySelectorAll<HTMLTableRowElement>('tr[data-parameter-row]');
    rows.forEach((row) => {
        row.classList.toggle('is-selected', row.dataset.parameterRow === activeName);
    });
}

function renderParameterDetail(insight: ParameterInsight | null): void {
    if (!insight) {
        resetParameterDetail('Select a parameter row to view details.');
        return;
    }

    const detailContainer = document.getElementById(PARAMETER_DETAIL_CONTAINER_ID) as HTMLElement | null;
    if (!detailContainer) return;
    const title = document.getElementById(PARAMETER_DETAIL_TITLE_ID);

    if (title) {
        title.textContent = `Parameter Detail — ${insight.name}`;
    }

    const chartData = buildChartData(insight);
    if (!chartData) {
        detailContainer.innerHTML = '<p class="muted">Insufficient trend data available yet.</p>';
        return;
    }

    detailContainer.innerHTML = buildParameterDetailContent(insight);
    const canvas = detailContainer.querySelector<HTMLCanvasElement>('[data-parameter-chart]');
    if (canvas) {
        renderParameterChart(canvas, chartData, insight);
    }

    // Bind correlation table sort buttons
    bindCorrelationTableSort(detailContainer, insight);
}

function bindCorrelationTableSort(container: HTMLElement, insight: ParameterInsight): void {
    const wrapper = container.querySelector<HTMLElement>('.corr-table-scroll-wrapper');
    if (!wrapper) return;

    const parameterName = wrapper.dataset.corrParam;
    if (!parameterName) return;

    wrapper.addEventListener('click', (event) => {
        const target = event.target as HTMLElement | null;
        if (!target) return;

        const button = target.closest<HTMLButtonElement>('button[data-corr-sort-key]');
        if (!button || !wrapper.contains(button)) return;

        const key = button.dataset.corrSortKey as CorrelationSortKey | undefined;
        if (!key || !['name', 'value', 'absValue'].includes(key)) return;

        event.preventDefault();
        const newState = cycleCorrelationSort(parameterName, key);
        setCorrelationSortState(parameterName, newState);

        // Re-render just the correlation table
        const section = container.querySelector<HTMLElement>('.parameter-correlation-section');
        if (section) {
            const sampleCount = insight.variantsTracked > 0 ? insight.variantsTracked : null;
            const newTable = renderCorrelationTable(insight.correlated, sampleCount, parameterName);
            const h5 = section.querySelector('h5');
            section.innerHTML = '';
            if (h5) section.appendChild(h5.cloneNode(true));
            section.insertAdjacentHTML('beforeend', newTable);
            // Re-bind after DOM update
            bindCorrelationTableSort(container, insight);
        }
    });
}

function buildParameterDetailContent(insight: ParameterInsight): string {
    const sampleCount = insight.variantsTracked > 0 ? insight.variantsTracked : null;
    const correlated = renderCorrelationTable(insight.correlated, sampleCount, insight.name);

    return `
        <div>
            <div class="parameter-detail-grid">
                <canvas data-parameter-chart="${escapeHtml(insight.name)}" role="img" aria-label="${escapeHtml(
                    `${insight.name} evolution`,
                )}"></canvas>
            </div>
            <div class="parameter-correlation-section">
                <h5>Correlations</h5>
                ${correlated}
            </div>
        </div>
    `;
}

function scheduleCorrelationLayoutRetry(): void {
    if (pendingCorrelationLayoutRetryScheduled) {
        return;
    }
    pendingCorrelationLayoutRetryScheduled = true;
    const scheduler =
        typeof window !== 'undefined' && typeof window.setTimeout === 'function' ? window.setTimeout : setTimeout;
    scheduler(() => {
        pendingCorrelationLayoutRetryScheduled = false;
        flushPendingCorrelationResult();
    }, 50);
}

function flushPendingCorrelationInsights(): boolean {
    if (!pendingCorrelationInsights) {
        return false;
    }
    const insights = pendingCorrelationInsights;
    pendingCorrelationInsights = null;
    renderCorrelationInsights(insights);
    return true;
}

function applyCorrelationInsights(insights: ParameterInsight[]): void {
    setParameterInsights(insights);
    repaintParameterTable();
    const sortedInsights = getSortedParameterInsights();

    if (!insights.length) {
        setSelectedParameter(null);
        console.warn('[SPSA] renderCorrelationResult has no insights to display');
        resetParameterDetail('No parameter information available yet.');
        return;
    }

    const preferred = resolvePreferredSelection(sortedInsights);
    if (preferred) {
        requestAnimationFrame(() => {
            selectParameterRow(preferred.name, { scrollIntoView: preferred.scrollIntoView });
        });
    }
}
