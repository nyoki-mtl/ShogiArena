import type {
    DashboardInstancesApi,
    InstanceRecord,
    InstancesSnapshot,
    InstancesStats,
} from '@/modules/instances/types';
import { apiBase, requestJson } from '@/modules/instances/services/api';
import { type FormHandlers, initializeForms } from '@/modules/instances/components/forms';
import {
    applyActiveHighlights,
    processHighlightQueue,
    queueInstanceHighlights,
} from '@/modules/instances/utils/highlights';
import { type RenderCallbacks, renderError, renderSnapshot } from '@/modules/instances/components/render';
import { drawSparklines } from '@/modules/instances/components/metrics';
import { instancesState } from '@/modules/instances/state';
import { SPARK_HISTORY_MS } from '@/modules/instances/state/constants';
import type { DashboardTabsApi } from '@/types/globals';

interface InstancesWindow extends Window {
    DashboardInstances?: DashboardInstancesApi;
    DashboardTabs?: DashboardTabsApi;
}

const state = instancesState;
const defaultWindow = window as InstancesWindow;
const INSTANCES_SSE_RECONNECT_MS = 3_000;
const INSTANCES_SNAPSHOT_FALLBACK_MS = 1_200;
const INSTANCES_SPARKLINE_TICK_MS = 1_000;
const SPARKLINE_MAX_PER_FRAME = 6;
const SPARKLINE_OBSERVER_THRESHOLD = 0.05;

let installedInstancesApi: DashboardInstancesApi | null = null;

function requireTabsApi(owner: InstancesWindow): DashboardTabsApi {
    const tabs = owner.DashboardTabs;
    if (!tabs) {
        throw new Error('DashboardTabs must be loaded before instances module');
    }
    if (typeof tabs.setActive !== 'function') {
        throw new Error('DashboardTabs.setActive is unavailable');
    }
    if (typeof tabs.getActive !== 'function') {
        throw new Error('DashboardTabs.getActive is unavailable');
    }
    return tabs;
}

function createInstancesModule(owner: InstancesWindow): DashboardInstancesApi {
    const tabsApi = requireTabsApi(owner);
    let sseSource: EventSource | null = null;
    let sseReconnectTimer: number | null = null;
    let sseFallbackTimer: number | null = null;
    let initialSnapshotReceived = false;
    let sparklineTimer: number | null = null;
    let sparklineRaf: number | null = null;
    let visibilityObserver: IntersectionObserver | null = null;
    let sparklineWorker: Worker | null = null;
    let sparklineWorkerFailed = false;
    let sparklineCanvasCounter = 0;
    const observedCards = new Map<Element, string>();
    const sparklineQueue: string[] = [];
    const sparklineQueueSet = new Set<string>();
    const sparklineCanvasMeta = new Map<HTMLCanvasElement, { id: string; kind: string; core: string | null }>();
    const sparklineCanvasesByInstance = new Map<string, Set<HTMLCanvasElement>>();

    const enqueueSparklineDraw = (ids?: readonly string[]): void => {
        if (!state.active) return;
        const visible = state.visibleInstanceIds;
        if (!visible.size) return;
        const targets = ids?.length ? ids : Array.from(visible);
        const pending: string[] = [];
        targets.forEach((id) => {
            if (!id || !visible.has(id)) return;
            if (sparklineQueueSet.has(id)) return;
            sparklineQueueSet.add(id);
            sparklineQueue.push(id);
            pending.push(id);
        });
        if (pending.length === 0) return;
        if (sparklineRaf !== null) return;
        sparklineRaf = owner.requestAnimationFrame(processSparklineQueue);
    };

    const processSparklineQueue = (): void => {
        sparklineRaf = null;
        if (!state.active || sparklineQueue.length === 0) {
            sparklineQueue.length = 0;
            sparklineQueueSet.clear();
            return;
        }
        const batch = sparklineQueue.splice(0, SPARKLINE_MAX_PER_FRAME);
        for (const id of batch) {
            sparklineQueueSet.delete(id);
        }
        if (batch.length) {
            if (sparklineWorker) {
                for (const id of batch) {
                    renderInstanceSparklines(id);
                }
            } else {
                drawSparklines(state, batch);
            }
        }
        if (sparklineQueue.length > 0) {
            sparklineRaf = owner.requestAnimationFrame(processSparklineQueue);
        }
    };

    const ensureSparklineWorker = (): boolean => {
        if (sparklineWorker) return true;
        if (sparklineWorkerFailed) return false;
        if (typeof Worker !== 'function' || typeof OffscreenCanvas !== 'function') {
            sparklineWorkerFailed = true;
            return false;
        }
        if (typeof HTMLCanvasElement === 'undefined') {
            sparklineWorkerFailed = true;
            return false;
        }
        if (typeof HTMLCanvasElement.prototype.transferControlToOffscreen !== 'function') {
            sparklineWorkerFailed = true;
            return false;
        }
        try {
            sparklineWorker = new Worker(new URL('../workers/sparklineWorker.ts', import.meta.url), { type: 'module' });
            sparklineWorker.onerror = (event) => {
                console.warn('[Instances] Sparkline worker failed', event);
                sparklineWorkerFailed = true;
            };
            sparklineWorker.onmessageerror = (event) => {
                console.warn('[Instances] Sparkline worker message error', event);
            };
            return true;
        } catch (error) {
            console.warn('[Instances] Sparkline worker init failed', error);
            sparklineWorkerFailed = true;
            return false;
        }
    };

    const registerSparklineCanvas = (canvas: HTMLCanvasElement, instanceId: string): void => {
        if (!ensureSparklineWorker()) return;
        if (sparklineCanvasMeta.has(canvas)) return;
        if (canvas.dataset.offscreenTransferred === 'true') return;
        if (typeof canvas.transferControlToOffscreen !== 'function') return;
        const kind = canvas.dataset.kind === 'mem' ? 'mem' : canvas.dataset.kind === 'core' ? 'core' : 'cpu';
        const core = canvas.dataset.core ?? null;
        const canvasId = `inst:${instanceId}:${kind}:${core ?? 'all'}:${sparklineCanvasCounter++}`;
        const offscreen = canvas.transferControlToOffscreen();
        canvas.dataset.offscreenTransferred = 'true';
        sparklineCanvasMeta.set(canvas, { id: canvasId, kind, core });
        let set = sparklineCanvasesByInstance.get(instanceId);
        if (!set) {
            set = new Set();
            sparklineCanvasesByInstance.set(instanceId, set);
        }
        set.add(canvas);
        sparklineWorker?.postMessage(
            {
                type: 'register',
                canvasId,
                instanceId,
                kind,
                core,
                baseWidth: kind === 'core' ? 180 : 240,
                baseHeight: kind === 'core' ? 80 : 96,
                canvas: offscreen,
            },
            [offscreen],
        );
    };

    const unregisterSparklineCanvases = (instanceId: string): void => {
        const canvases = sparklineCanvasesByInstance.get(instanceId);
        if (!canvases) return;
        canvases.forEach((canvas) => {
            const meta = sparklineCanvasMeta.get(canvas);
            if (!meta) return;
            sparklineWorker?.postMessage({ type: 'unregister', canvasId: meta.id });
            sparklineCanvasMeta.delete(canvas);
        });
        sparklineCanvasesByInstance.delete(instanceId);
    };

    const registerSparklineCanvasesForCard = (card: HTMLElement): void => {
        if (!ensureSparklineWorker()) return;
        const instanceId = card.dataset.instId || '';
        if (!instanceId) return;
        const canvases = card.querySelectorAll<HTMLCanvasElement>('canvas.inst-spark');
        for (let idx = 0; idx < canvases.length; idx += 1) {
            let canvas = canvases.item(idx);
            if (!canvas) continue;
            if (canvas.dataset.offscreenTransferred === 'true' && !sparklineCanvasMeta.has(canvas)) {
                const replacement = document.createElement('canvas');
                replacement.className = canvas.className;
                replacement.width = canvas.width;
                replacement.height = canvas.height;
                Array.from(canvas.attributes).forEach((attr) => {
                    if (attr.name === 'class' || attr.name === 'width' || attr.name === 'height') return;
                    replacement.setAttribute(attr.name, attr.value);
                });
                canvas.replaceWith(replacement);
                canvas = replacement;
            }
            registerSparklineCanvas(canvas, instanceId);
        }
    };

    const renderInstanceSparklines = (instanceId: string): void => {
        if (!sparklineWorker) {
            drawSparklines(state, [instanceId]);
            return;
        }
        const history = state.history[instanceId];
        if (!history) return;
        const canvases = sparklineCanvasesByInstance.get(instanceId);
        if (!canvases || canvases.size === 0) return;
        const now = Date.now();
        const startTs = now - SPARK_HISTORY_MS;
        const ratio = owner.devicePixelRatio || 1;
        canvases.forEach((canvas) => {
            const meta = sparklineCanvasMeta.get(canvas);
            if (!meta || !canvas.isConnected) return;
            const kind = meta.kind === 'mem' ? 'mem' : meta.kind === 'core' ? 'core' : 'cpu';
            const series =
                kind === 'mem' ? history.mem : kind === 'core' ? (history.cores[meta.core ?? ''] ?? []) : history.cpu;
            const points = series
                .map((point) => ({
                    ts: typeof point.ts === 'number' ? point.ts : now,
                    value: typeof point.value === 'number' ? point.value : null,
                }))
                .filter((point) => point.value != null);
            const clientWidth = Math.floor(canvas.clientWidth || 0);
            const width = Math.max(kind === 'core' ? 180 : 240, clientWidth || canvas.width || 0);
            sparklineWorker?.postMessage({
                type: 'render',
                canvasId: meta.id,
                kind,
                now,
                startTs,
                points,
                width,
                height: kind === 'core' ? 80 : 96,
                devicePixelRatio: ratio,
            });
        });
    };
    async function loadInstances(): Promise<InstancesSnapshot> {
        return requestJson<InstancesSnapshot>(`${apiBase()}/api/instances`, { cache: 'no-store' });
    }

    async function pollOnce(): Promise<void> {
        if (state.loading) return;
        state.loading = true;
        try {
            const snapshot = await loadInstances();
            state.data = snapshot;
            renderSnapshot(state, snapshot, renderCallbacks);
            syncVisibilityObserver();
        } finally {
            state.loading = false;
        }
    }

    type InstancesDeltaPayload = {
        instances?: InstanceRecord[];
        removed?: string[];
        stats?: InstancesStats;
        timestamp?: number;
    };

    function applyInstancesDelta(snapshot: InstancesSnapshot, delta: InstancesDeltaPayload): InstancesSnapshot {
        const existing = Array.isArray(snapshot.instances) ? [...snapshot.instances] : [];
        const removed = new Set((delta.removed || []).map((id) => String(id)));
        const filtered = existing.filter((inst) => !removed.has(inst.id));
        const indexById = new Map(filtered.map((inst, idx) => [inst.id, idx]));

        const upserts = Array.isArray(delta.instances) ? delta.instances : [];
        upserts.forEach((inst) => {
            const id = inst.id;
            if (!id) return;
            const idx = indexById.get(id);
            if (idx === undefined) {
                filtered.push(inst);
                indexById.set(id, filtered.length - 1);
            } else {
                filtered[idx] = inst;
            }
        });

        return {
            instances: filtered,
            stats: delta.stats ?? snapshot.stats,
            timestamp: delta.timestamp ?? snapshot.timestamp,
        };
    }

    async function refresh(): Promise<void> {
        try {
            await pollOnce();
        } catch (error) {
            renderError(error);
            throw error;
        }
    }

    const clearFallback = (): void => {
        if (sseFallbackTimer !== null) {
            owner.clearTimeout(sseFallbackTimer);
            sseFallbackTimer = null;
        }
    };

    const scheduleFallback = (): void => {
        if (sseFallbackTimer !== null) return;
        sseFallbackTimer = owner.setTimeout(() => {
            sseFallbackTimer = null;
            if (initialSnapshotReceived) {
                return;
            }
            pollOnce().catch((error) => {
                renderError(error);
                throw error instanceof Error ? error : new Error(String(error));
            });
        }, INSTANCES_SNAPSHOT_FALLBACK_MS);
    };

    const clearReconnect = (): void => {
        if (sseReconnectTimer !== null) {
            owner.clearTimeout(sseReconnectTimer);
            sseReconnectTimer = null;
        }
    };

    const closeStream = (): void => {
        if (sseSource) {
            try {
                sseSource.close();
            } catch {
                // ignore
            }
        }
        sseSource = null;
    };

    const openStream = (): void => {
        if (typeof EventSource !== 'function') {
            scheduleFallback();
            return;
        }
        if (sseSource) {
            return;
        }
        const url = `${apiBase()}/api/instances/stream?send_initial=true`;
        try {
            const source = new EventSource(url);
            sseSource = source;

            const handleEvent = (event: MessageEvent) => {
                try {
                    const payload = JSON.parse(event.data) as {
                        type?: string;
                        data?: InstancesSnapshot | InstancesDeltaPayload;
                    };
                    if (payload.type === 'heartbeat') {
                        return;
                    }
                    if (!payload.data) {
                        return;
                    }
                    if (payload.type === 'instances_delta') {
                        if (state.data) {
                            const merged = applyInstancesDelta(state.data, payload.data as InstancesDeltaPayload);
                            state.data = merged;
                            renderSnapshot(state, merged, renderCallbacks);
                            syncVisibilityObserver();
                        } else {
                            void pollOnce();
                        }
                    } else {
                        state.data = payload.data as InstancesSnapshot;
                        renderSnapshot(state, payload.data as InstancesSnapshot, renderCallbacks);
                        syncVisibilityObserver();
                    }
                    if (!initialSnapshotReceived) {
                        initialSnapshotReceived = true;
                        clearFallback();
                    }
                } catch (error) {
                    renderError(error);
                }
            };

            source.addEventListener('instances', handleEvent);
            source.onmessage = handleEvent;

            source.onerror = () => {
                closeStream();
                clearReconnect();
                sseReconnectTimer = owner.setTimeout(() => {
                    sseReconnectTimer = null;
                    openStream();
                    scheduleFallback();
                }, INSTANCES_SSE_RECONNECT_MS);
            };
        } catch (error) {
            renderError(error);
            scheduleFallback();
        }
    };

    const startSparklineTicker = (): void => {
        if (sparklineTimer !== null) return;
        sparklineTimer = owner.setInterval(() => {
            if (!state.active) return;
            const dirtyIds = Array.from(state.sparklineDirtyIds);
            state.sparklineDirtyIds.clear();
            if (dirtyIds.length) {
                enqueueSparklineDraw(dirtyIds);
            }
            enqueueSparklineDraw();
        }, INSTANCES_SPARKLINE_TICK_MS);
    };

    const stopSparklineTicker = (): void => {
        if (sparklineTimer === null) return;
        owner.clearInterval(sparklineTimer);
        sparklineTimer = null;
    };

    const ensureVisibilityObserver = (): void => {
        if (visibilityObserver) return;
        if (typeof IntersectionObserver !== 'function') {
            visibilityObserver = null;
            return;
        }
        visibilityObserver = new IntersectionObserver(
            (entries) => {
                let touched = false;
                entries.forEach((entry) => {
                    const id = observedCards.get(entry.target);
                    if (!id) return;
                    if (entry.isIntersecting) {
                        if (!state.visibleInstanceIds.has(id)) {
                            state.visibleInstanceIds.add(id);
                            touched = true;
                            enqueueSparklineDraw([id]);
                        }
                    } else if (state.visibleInstanceIds.delete(id)) {
                        touched = true;
                    }
                });
                if (touched && state.visibleInstanceIds.size === 0) {
                    sparklineQueue.length = 0;
                    sparklineQueueSet.clear();
                }
            },
            { root: null, threshold: SPARKLINE_OBSERVER_THRESHOLD },
        );
    };

    const syncVisibilityObserver = (): void => {
        const grid = owner.document.getElementById('instancesGrid');
        if (!grid) return;
        ensureVisibilityObserver();
        if (!visibilityObserver) return;
        const observer = visibilityObserver;
        const cards = Array.from(grid.querySelectorAll<HTMLElement>('.inst-card[data-inst-id]'));
        const activeIds = new Set<string>();
        cards.forEach((card) => {
            const id = card.dataset.instId || '';
            if (!id) return;
            activeIds.add(id);
            if (!observedCards.has(card)) {
                observedCards.set(card, id);
                observer.observe(card);
            }
            registerSparklineCanvasesForCard(card);
        });
        Array.from(observedCards.keys()).forEach((card) => {
            const id = observedCards.get(card);
            if (!id || activeIds.has(id)) return;
            observer.unobserve(card);
            observedCards.delete(card);
            state.visibleInstanceIds.delete(id);
            unregisterSparklineCanvases(id);
        });
    };

    function startPolling(): void {
        if (state.pollTimer) return;
        state.pollTimer = -1;
        openStream();
        scheduleFallback();
        startSparklineTicker();
    }

    function stopPolling(): void {
        state.pollTimer = null;
        clearFallback();
        clearReconnect();
        closeStream();
        stopSparklineTicker();
        if (sparklineRaf !== null) {
            owner.cancelAnimationFrame(sparklineRaf);
            sparklineRaf = null;
        }
        sparklineQueue.length = 0;
        sparklineQueueSet.clear();
        state.visibleInstanceIds.clear();
        if (visibilityObserver) {
            visibilityObserver.disconnect();
            visibilityObserver = null;
        }
        observedCards.clear();
        sparklineCanvasesByInstance.clear();
        sparklineCanvasMeta.clear();
        if (sparklineWorker) {
            sparklineWorker.terminate();
            sparklineWorker = null;
        }
        sparklineWorkerFailed = false;
    }

    let formHandlers: FormHandlers | null = null;

    const renderCallbacks: RenderCallbacks = {
        refresh,
        onRequestEdit: (instance) => formHandlers?.openForm('edit', instance),
        onRequestProvision: (instance) => {
            void formHandlers?.promptProvision(instance);
        },
        onDrainStarted: (instance) => formHandlers?.showDrainConfirmation(instance),
        requestSparklineDraw: enqueueSparklineDraw,
    };

    function bindControls(doc: Document): void {
        const root = doc.getElementById('instancesTab');
        if (!root) {
            throw new Error('Instances dashboard requires #instancesTab element');
        }
        root.addEventListener('click', (event) => {
            const target = (event.target as HTMLElement | null)?.closest<HTMLElement>('[data-action]');
            if (!target) return;
            const action = target.dataset.action;
            if (!action || !action.startsWith('instances:')) {
                return;
            }
            switch (action) {
                case 'instances:refresh':
                    event.preventDefault();
                    void refresh();
                    break;
                case 'instances:add':
                    event.preventDefault();
                    formHandlers?.openForm('create');
                    break;
                default:
                    break;
            }
        });
    }

    function focusInstances(ids: readonly string[] | string): void {
        queueInstanceHighlights(state, ids);
        if (!state.active) {
            void refresh();
        } else {
            applyActiveHighlights(state);
            processHighlightQueue(state);
        }
    }

    let api: DashboardInstancesApi;

    const initializeDom = (): void => {
        const doc = owner.document;
        formHandlers = initializeForms(state, refresh);
        bindControls(doc);
        syncVisibilityObserver();
        if (tabsApi.getActive() === 'instances') {
            api.setActive(true);
        }
    };

    api = {
        refresh: () => refresh(),
        setActive(active: boolean) {
            state.active = Boolean(active);
            if (state.active) {
                startPolling();
            } else {
                stopPolling();
            }
        },
        focusInstances,
        focusInstance(id: string) {
            focusInstances(id);
        },
    };

    const doc = owner.document;
    if (doc.readyState === 'loading') {
        doc.addEventListener('DOMContentLoaded', initializeDom, { once: true });
    } else {
        initializeDom();
    }

    return api;
}

export function installInstancesModule(owner: InstancesWindow = defaultWindow): DashboardInstancesApi {
    if (installedInstancesApi) {
        owner.DashboardInstances = installedInstancesApi;
        return installedInstancesApi;
    }

    const api = createInstancesModule(owner);
    installedInstancesApi = api;
    owner.DashboardInstances = api;
    return api;
}
