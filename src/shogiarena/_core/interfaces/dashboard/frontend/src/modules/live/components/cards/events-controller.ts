import { mergeWorkerSnapshotMutable } from '@/modules/live/services/updates/worker/snapshot-merge';
import type { WorkerViewModelMessage } from '@/modules/live/services/updates/worker-bridge';
import { getWorkerState, peekWorkerSnapshotRecord, setWorkerState } from '@/modules/live/state/updates';
import type { LiveCardState, WorkerRuntimeState } from '@/modules/live/types';
import type { WorkerSnapshotUpdate } from '@/modules/live/types/updates';
import { createEmptyWorkerSnapshot } from '@/modules/live/utils';
import {
    maybeApplyImmediateClockStart,
    queueClockCorrections,
    resetWorkerRuntimeClockStateForNewGame,
    syncClockToTurnBoundary,
    updateTimeControlState,
} from '@/modules/live/utils/clock-sync';
import { debugCheckWorkerSnapshotLegality } from '@/modules/live/utils/debug/legal-move-check';
import { asEngineStatusSnapshot, shouldRunEngineClock } from '@/modules/live/utils/engine-status';
import { recordLiveDiagnosticsMetric } from '@/modules/live/utils/liveNamespace/metrics';
import type { DashboardCore, DashboardCoreState } from '@/types/dashboard';
import { createClocksController } from './clocks';
import { createCardEventHandlers } from './events';
import type { WorkerSnapshotRecord } from './types';
import { clearCsaWaitingCardSync } from './worker-snapshots';

// Unified sync design: clock-only and analysis-only delta detection removed.
// All updates now follow the same refresh path for consistent sync timing.

function hasMoveDelta(delta: WorkerSnapshotUpdate | undefined): boolean {
    if (!delta || typeof delta !== 'object') return false;
    return Boolean((delta as { move?: unknown }).move || (delta as { ki2_move?: unknown }).ki2_move);
}

const CLOCK_DELTA_FIELDS = [
    'active',
    'side',
    'black_remain_ms',
    'white_remain_ms',
    'applied_increment_ms',
    'occurred_at_ms',
    'started_at_ms',
    'pre_black_remain_ms',
    'pre_white_remain_ms',
    'byoyomi_ms_black',
    'byoyomi_ms_white',
    'increment_ms_black',
    'increment_ms_white',
    'time_control_black',
    'time_control_white',
] as const;

function hasClockSignal(delta: WorkerSnapshotUpdate | undefined): boolean {
    if (!delta || typeof delta !== 'object') return false;
    const typeRaw = (delta as { type?: unknown }).type;
    const type = typeof typeRaw === 'string' ? typeRaw.trim().toLowerCase() : '';
    if (type === 'clock_start' || type === 'clock_increment') return true;
    const clock = (delta as { clock?: unknown }).clock;
    if (clock && typeof clock === 'object' && !Array.isArray(clock)) return true;
    for (const field of CLOCK_DELTA_FIELDS) {
        if (Object.hasOwn(delta, field)) {
            return true;
        }
    }
    return false;
}

function deltaPriority(delta: WorkerSnapshotUpdate | undefined): number {
    if (!delta || typeof delta !== 'object') return 0;
    if (hasMoveDelta(delta)) return 3;
    const type = (delta as { type?: unknown }).type;
    if (type === 'clock_start' || type === 'clock_increment') return 2;
    if (type === 'analysis') return 1;
    return 0;
}

function mergeSnapshotDelta(
    base: WorkerSnapshotUpdate | undefined,
    incoming: WorkerSnapshotUpdate | undefined,
): WorkerSnapshotUpdate | undefined {
    if (!base) return incoming;
    if (!incoming) return base;
    const basePly = typeof base.current_ply === 'number' && Number.isFinite(base.current_ply) ? base.current_ply : null;
    const incomingPly =
        typeof incoming.current_ply === 'number' && Number.isFinite(incoming.current_ply) ? incoming.current_ply : null;
    if (basePly != null && incomingPly != null && basePly !== incomingPly) {
        // When updates arrive in bursts (e.g. mate sequences), we may merge multiple VMs into one rAF frame.
        // Never mix per-ply fields (move/eval/search stats) across different current_ply values, or we can
        // accidentally attach the previous ply's eval to the next ply's move.
        const baseHasMove = hasMoveDelta(base);
        const incomingHasMove = hasMoveDelta(incoming);
        if (incomingPly > basePly) {
            // Prefer move-bearing updates so we don't drop the actual move when an analysis-only delta
            // for the next ply arrives in the same frame (common near mate).
            if (baseHasMove && !incomingHasMove) return base;
            return incoming;
        }
        // incoming is a ply regression (Undo/"待った" or correction): treat the later message as authoritative.
        return incoming;
    }
    const merged: WorkerSnapshotUpdate = { ...base };
    const mergedRecord = merged as Record<string, unknown>;

    const baseType = (base as { type?: unknown }).type;
    const incomingType = (incoming as { type?: unknown }).type;
    const basePriority = deltaPriority(base);
    const incomingPriority = deltaPriority(incoming);
    if (incomingPriority > basePriority && typeof incomingType === 'string') {
        mergedRecord.type = incomingType;
    } else if (typeof baseType === 'string') {
        mergedRecord.type = baseType;
    }

    if (incomingPly != null) merged.current_ply = incomingPly;
    else if (basePly != null) merged.current_ply = basePly;

    // Last-write-wins: ordering is guaranteed upstream.
    if (incoming.move != null) merged.move = incoming.move;
    if (incoming.ki2_move != null) merged.ki2_move = incoming.ki2_move;

    for (const [key, value] of Object.entries(incoming as Record<string, unknown>)) {
        if (value === undefined) continue;
        if (key === 'move' || key === 'ki2_move') continue;
        if (key === 'current_ply') continue;
        if (key === 'type') continue;
        mergedRecord[key] = value;
    }

    return merged;
}

function toClockPayload(value: unknown): Record<string, unknown> | null {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
    return value as Record<string, unknown>;
}

function pickClockField(
    snapshot: Record<string, unknown>,
    nestedClock: Record<string, unknown> | null,
    keys: string[],
): unknown {
    for (const key of keys) {
        if (nestedClock && nestedClock[key] !== undefined) return nestedClock[key];
        if (snapshot[key] !== undefined) return snapshot[key];
    }
    return undefined;
}

export function extractClockPayloadFromSnapshot(
    snapshotRecord: Record<string, unknown>,
): Record<string, unknown> | null {
    const nestedClock = toClockPayload(snapshotRecord.clock);
    const payload: Record<string, unknown> = {};

    const assign = (target: string, keys: string[]) => {
        const value = pickClockField(snapshotRecord, nestedClock, keys);
        if (value !== undefined) payload[target] = value;
    };

    assign('active', ['active', 'clock_active', '_clock_active']);
    assign('black_remain_ms', ['black_remain_ms', '_black_remain_ms']);
    assign('white_remain_ms', ['white_remain_ms', '_white_remain_ms']);
    assign('started_at_ms', ['started_at_ms', 'clock_started_at_ms', '_clock_started_at_ms']);
    assign('occurred_at_ms', ['occurred_at_ms']);
    assign('byoyomi_ms_black', ['byoyomi_ms_black']);
    assign('byoyomi_ms_white', ['byoyomi_ms_white']);
    assign('time_control_black', ['time_control_black']);
    assign('time_control_white', ['time_control_white']);

    return Object.keys(payload).length > 0 ? payload : null;
}

export function shouldTickWorkerClock(ws: WorkerRuntimeState, snapshot: WorkerSnapshotRecord): boolean {
    if (snapshot.game_result) return false;
    if (ws.engineStatus) return shouldRunEngineClock(ws.engineStatus);
    const active = ws.clockActive;
    return (active === 'black' || active === 'white') && Number(ws.startedAtMs ?? 0) > 0;
}

function toFiniteNumber(value: unknown): number | null {
    return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function toClockSide(value: unknown): 'black' | 'white' | null {
    if (typeof value !== 'string') return null;
    const normalized = value.trim().toLowerCase();
    if (normalized === 'black' || normalized === 'white') return normalized;
    return null;
}

function inferIncrementSide(payload: Record<string, unknown>): 'black' | 'white' | null {
    const direct = toClockSide(payload.side);
    if (direct) return direct;
    const preBlack = toFiniteNumber(payload.pre_black_remain_ms);
    const preWhite = toFiniteNumber(payload.pre_white_remain_ms);
    const postBlack = toFiniteNumber(payload.black_remain_ms);
    const postWhite = toFiniteNumber(payload.white_remain_ms);
    if (preBlack != null && preWhite != null && postBlack != null && postWhite != null) {
        const deltaBlack = postBlack - preBlack;
        const deltaWhite = postWhite - preWhite;
        if (deltaBlack !== 0 && deltaWhite === 0) return 'black';
        if (deltaWhite !== 0 && deltaBlack === 0) return 'white';
    }
    const active = toClockSide(payload.active);
    if (active === 'black') return 'white';
    if (active === 'white') return 'black';
    return null;
}

function normalizeVmGid(value: unknown): string | null {
    if (typeof value !== 'string') return null;
    const trimmed = value.trim();
    return trimmed ? trimmed : null;
}

function mergeWorkerVmMessage(
    prev: WorkerViewModelMessage,
    incoming: WorkerViewModelMessage,
): { merged: WorkerViewModelMessage; gidChanged: boolean } {
    const prevGid = normalizeVmGid(prev.gid);
    const nextGid = normalizeVmGid(incoming.gid);
    const gidChanged = prevGid !== nextGid;
    const merged: WorkerViewModelMessage = { ...prev };
    merged.workerIdx = incoming.workerIdx;
    merged.gid = incoming.gid;
    merged.current_ply = incoming.current_ply;
    merged.sfen = incoming.sfen;
    merged.lastMove = incoming.lastMove;
    merged.ki2Move = incoming.ki2Move;
    merged.evalCp = incoming.evalCp;

    if (gidChanged) {
        // Do not carry old-game state across gid boundaries.
        merged.snapshot = undefined;
        merged.snapshotDelta = undefined;
        merged.clock = undefined;
    }

    if (incoming.clock && typeof incoming.clock === 'object') {
        merged.clock = incoming.clock as Record<string, unknown>;
    }
    // Snapshot is authoritative for state fields.
    if (incoming.snapshot && typeof incoming.snapshot === 'object') {
        merged.snapshot = incoming.snapshot as Record<string, unknown>;
    }

    return { merged, gidChanged };
}

export const __testMergeWorkerVmMessage = mergeWorkerVmMessage;

interface LiveCardsEventsControllerDeps {
    core: DashboardCore;
    getCards: () => LiveCardState[];
    hasWorkerCards: (workerIdx: number) => boolean;
    updateWorkerOptionLabels?: (workerIdx?: number | null) => void;
    getWorkerSnapshot: (workerIdx: number) => {
        data: WorkerSnapshotRecord;
        maxPly: number;
        liveDisplayPly: number;
    };
    resolveCardView: (
        card: LiveCardState,
        snapshot: { data: WorkerSnapshotRecord; maxPly: number; liveDisplayPly: number },
    ) => {
        isLiveView: boolean;
        viewPly: number;
    };
    normalizeSFEN: (sfen: string) => string;
    updateStaticClocksForCard: (cardId: LiveCardState['id'], snapshot: WorkerSnapshotRecord) => void;
    formatRemain: (value: number) => string;
    formatInc: (value: number) => string;
    formatCountUp: (value: number) => string;
    formatByoyomi: (value: number) => string;
    formatTimeControlShort: (spec: string | unknown) => string;
    toNumber: (value: unknown, fallback?: number) => number;
    cacheWorkerSnapshot: (workerIdx: number, data: WorkerSnapshotRecord | null | undefined) => void;
    updateCardData: (cardState: LiveCardState) => Promise<void> | void;
    handleEngineLogEvent?: (payload: unknown) => void;
}

export interface LiveCardsEventsController {
    startWorkerClockTimer: (workerIdx: number) => void;
    stopWorkerClockTimer: (workerIdx: number) => void;
    stopAllWorkerClockTimers: () => void;
    updateTimeControlLabelsForWorker: (workerIdx: number, clock: Record<string, unknown> | null | undefined) => void;
    ensureIncrementPlaceholders: (workerIdx: number) => void;
    flashIncrement: (workerIdx: number, side: string, appliedIncrementMs: number) => void;
    updateWorkerClockDisplay: (workerIdx: number) => void;
    freezeAllWorkerClocks: () => void;
    applyByoyomiFreezeCache: (workerIdx: number, clockData: Record<string, unknown>) => void;
    refreshCardsForWorker: (workerIdx: number) => void;
    triggerTabResume: () => void;
    /** Called when syncTempo changes to reset fps timer state for immediate effect. */
    onTempoChange: () => void;
    /** Stop all live rendering: clear pending queues, cancel timers, stop clocks.
     *  Used when switching to OFF mode. */
    stopLiveRendering: () => void;
    teardown: () => void;
}

export function createLiveCardsEventsController(deps: LiveCardsEventsControllerDeps): LiveCardsEventsController {
    const {
        core,
        getCards,
        hasWorkerCards,
        updateWorkerOptionLabels,
        getWorkerSnapshot,
        resolveCardView,
        normalizeSFEN,
        updateStaticClocksForCard,
        formatRemain,
        formatInc,
        formatCountUp,
        formatByoyomi,
        formatTimeControlShort,
        toNumber,
        cacheWorkerSnapshot,
        updateCardData,
        handleEngineLogEvent,
    } = deps;

    const { state, events, showNotice } = core;

    /**
     * Get the sync tempo setting from state.
     * Returns 'off' | 'auto' | 2 | 4 | 8 | 'unlimited' (defaults to 'auto').
     * 'off' = live updates stopped (equivalent to old Toggle OFF).
     * Defined early so it can be shared with clocksController.
     */
    const getSyncTempo = (): 'off' | 'auto' | 2 | 4 | 8 | 'unlimited' => {
        const tempo = (state as { syncTempo?: 'off' | 'auto' | 2 | 4 | 8 | 'unlimited' }).syncTempo;
        if (tempo === 'off' || tempo === 2 || tempo === 4 || tempo === 8 || tempo === 'unlimited') {
            return tempo;
        }
        return 'auto';
    };

    const clocksController = createClocksController({
        state,
        getCards,
        hasWorkerCards,
        getWorkerSnapshot,
        resolveCardView,
        normalizeSFEN,
        updateStaticClocksForCard,
        formatRemain,
        formatInc,
        formatCountUp,
        formatByoyomi,
        formatTimeControlShort,
        toNumber,
        // Share getSyncTempo so clocks can respect fixed tempo mode (no interval).
        getSyncTempo,
    });

    const {
        startWorkerClockTimer,
        stopWorkerClockTimer,
        stopAllWorkerClockTimers,
        updateTimeControlLabelsForWorker,
        ensureIncrementPlaceholders,
        flashIncrement,
        updateWorkerClockDisplay,
        freezeAllWorkerClocks,
        applyByoyomiFreezeCache,
    } = clocksController;

    const pendingWorkerRefresh = new Set<number>();
    type PendingVmEntry = {
        vm: WorkerViewModelMessage;
        receivedAt: number;
        mergedNonMoveDelta?: WorkerSnapshotUpdate;
    };
    const pendingVmByWorker = new Map<number, PendingVmEntry>();
    const lastVmFlashKeyByWorker = new Map<number, string>();
    const lastVmIncrementOccurredAtByWorker = new Map<number, number>();
    const lastVmContractWarnAtByWorker = new Map<number, number>();
    let pendingRafId: number | null = null;
    const inFlightUpdateByCardId = new Map<LiveCardState['id'], Promise<unknown>>();
    const inFlightStartedAtByCardId = new Map<LiveCardState['id'], number>();
    const refreshAfterInFlightByCardId = new Map<LiveCardState['id'], number>();
    let hiddenCoalesceStartMs: number | null = null;
    let resumeBurstUntilMs: number | null = null;
    let resumeDeferTimer: ReturnType<typeof setTimeout> | null = null;
    let syncDelayedTimer: ReturnType<typeof setTimeout> | null = null;
    const lastLagLogAtByWorker = new Map<number, number>();

    // FPS control: track last flush time for time-based frame rate limiting.
    // When syncTempo is 2/4/8, we limit refreshes to 2/4/8 times per second.
    let lastFlushAtMs = 0;
    let pendingFpsTimer: ReturnType<typeof setTimeout> | null = null;

    const isDocumentVisible = (): boolean =>
        typeof document === 'undefined' ||
        typeof document.visibilityState !== 'string' ||
        document.visibilityState === 'visible';

    const canRunWorkerClock = (workerIdx: number): boolean => {
        const ws = getWorkerState(state, workerIdx);
        return shouldTickWorkerClock(ws, getWorkerSnapshot(workerIdx).data);
    };

    const scheduleFrame = (): void => {
        // OFF mode: do not schedule any frames. Live updates are stopped.
        if (getSyncTempo() === 'off') {
            return;
        }
        // When the tab is hidden, browsers heavily throttle rAF and CPU. Rendering every update while hidden
        // consumes throttled time slices and makes the UI feel "slow to catch up" when the user comes back.
        // Coalesce while hidden and flush once on visibility resume.
        if (!isDocumentVisible()) {
            if (hiddenCoalesceStartMs == null) {
                hiddenCoalesceStartMs =
                    typeof performance !== 'undefined' && typeof performance.now === 'function'
                        ? performance.now()
                        : Date.now();
            }
            return;
        }
        if (typeof requestAnimationFrame !== 'function') {
            flushPending();
            flushPendingVm();
            return;
        }
        if (pendingRafId !== null) return;
        // If an fps timer is already scheduled, let it handle the flush to avoid double execution.
        // The fps timer represents the authoritative timing for fps-controlled modes.
        if (pendingFpsTimer != null) return;
        pendingRafId = requestAnimationFrame(() => {
            pendingRafId = null;
            flushPending();
            flushPendingVm();
            if (pendingWorkerRefresh.size || pendingVmByWorker.size) {
                scheduleFrame();
            }
        });
    };

    const applyRefreshForWorker = (workerIdx: number): void => {
        const target = `worker-latest:${workerIdx}`;
        let needsRerunAfterInFlight = false;
        for (const cardState of getCards()) {
            if (cardState && cardState.source === target) {
                if (inFlightUpdateByCardId.has(cardState.id)) {
                    // If another update arrives while we are rendering, ensure we schedule one more refresh
                    // once the in-flight update finishes. Otherwise the card can get "stuck behind" after resume.
                    refreshAfterInFlightByCardId.set(cardState.id, workerIdx);
                    needsRerunAfterInFlight = true;
                    continue;
                }
                const started =
                    typeof performance !== 'undefined' && typeof performance.now === 'function'
                        ? performance.now()
                        : Date.now();
                const done = () => {
                    const elapsed =
                        typeof performance !== 'undefined' && typeof performance.now === 'function'
                            ? performance.now() - started
                            : Date.now() - started;
                    recordLiveDiagnosticsMetric('live.cards.update_card', {
                        triggered: 1,
                        latency_ms: Math.max(0, elapsed),
                    });
                };
                try {
                    const result = updateCardData(cardState);
                    if (result && typeof (result as Promise<unknown>).then === 'function') {
                        const inFlight = (result as Promise<unknown>).finally(() => {
                            inFlightUpdateByCardId.delete(cardState.id);
                            inFlightStartedAtByCardId.delete(cardState.id);
                            const queuedWorker = refreshAfterInFlightByCardId.get(cardState.id);
                            if (typeof queuedWorker === 'number') {
                                refreshAfterInFlightByCardId.delete(cardState.id);
                                pendingWorkerRefresh.add(queuedWorker);
                                scheduleFrame();
                            }
                        });
                        inFlightUpdateByCardId.set(cardState.id, inFlight);
                        inFlightStartedAtByCardId.set(cardState.id, started);
                        void inFlight.then(done).catch(() => done());
                    } else {
                        done();
                    }
                } catch {
                    done();
                }
            }
        }
        if (needsRerunAfterInFlight) {
            recordLiveDiagnosticsMetric('live.cards.refresh.deferred_inflight', {
                triggered: 1,
                worker: workerIdx,
            });
        }
        if (!hasWorkerCards(workerIdx)) {
            stopWorkerClockTimer(workerIdx);
            return;
        }
        // Clock ticking is independent from sync tempo (except OFF).
        const syncTempo = getSyncTempo();
        if (syncTempo === 'off') {
            stopWorkerClockTimer(workerIdx);
        } else {
            if (canRunWorkerClock(workerIdx)) {
                startWorkerClockTimer(workerIdx);
            } else {
                stopWorkerClockTimer(workerIdx);
            }
            // Keep clock and turn highlight in sync with each refresh frame.
            updateWorkerClockDisplay(workerIdx);
        }
    };

    /**
     * Compute the target interval in ms for fps-based tempo control.
     * - 'off' = Infinity (never flush - live updates stopped)
     * - 2 fps = 500ms interval
     * - 4 fps = 250ms interval
     * - 8 fps = 125ms interval
     * - 'unlimited' or 'auto' = 0 (no time gating)
     */
    const getFpsIntervalMs = (tempo: 'off' | 'auto' | 2 | 4 | 8 | 'unlimited'): number => {
        if (tempo === 'off') return Infinity; // Never flush when OFF
        if (tempo === 2) return 500;
        if (tempo === 4) return 250;
        if (tempo === 8) return 125;
        return 0; // 'unlimited' or 'auto': no time-based gating
    };

    /**
     * Schedule a delayed flush for fps-controlled modes.
     * When the timing gate blocks an immediate flush, this schedules a retry
     * at the appropriate time.
     *
     * Note: When fps timer fires, we cancel any pending rAF to avoid double-flush.
     * The fps timer takes priority since it represents the actual fps-controlled timing.
     */
    const scheduleFpsDelayedFlush = (delayMs: number): void => {
        if (pendingFpsTimer != null) return; // Already scheduled
        pendingFpsTimer = setTimeout(() => {
            pendingFpsTimer = null;
            // Cancel any pending rAF to prevent double-flush.
            // The fps timer represents the authoritative flush timing for fps-controlled modes.
            if (pendingRafId !== null) {
                try {
                    cancelAnimationFrame(pendingRafId);
                } catch {
                    // ignore
                }
                pendingRafId = null;
            }
            if (pendingWorkerRefresh.size > 0 && isDocumentVisible()) {
                flushPending();
                flushPendingVm();
                if (pendingWorkerRefresh.size || pendingVmByWorker.size) {
                    scheduleFrame();
                }
            }
        }, delayMs);
    };

    const flushPending = (): void => {
        if (!pendingWorkerRefresh.size) {
            return;
        }
        if (!isDocumentVisible()) {
            return;
        }
        const getNow = () =>
            typeof performance !== 'undefined' && typeof performance.now === 'function'
                ? performance.now()
                : Date.now();
        const now = getNow();

        // FPS control: apply time-based gating for fixed tempo modes (2/4/8 fps).
        // This ensures the UI updates at the specified frame rate, not faster.
        const syncTempo = getSyncTempo();

        // OFF mode: do not flush any updates. Live updates are stopped.
        if (syncTempo === 'off') {
            return;
        }

        const fpsIntervalMs = getFpsIntervalMs(syncTempo);

        if (fpsIntervalMs > 0) {
            const elapsed = now - lastFlushAtMs;
            if (elapsed < fpsIntervalMs) {
                // Not enough time has passed since last flush.
                // Schedule a delayed flush for when the interval expires.
                const remaining = Math.max(1, fpsIntervalMs - elapsed);
                scheduleFpsDelayedFlush(remaining);
                return;
            }
        }

        // Update last flush time for fps tracking.
        lastFlushAtMs = now;

        const start = now;
        if (typeof performance !== 'undefined' && typeof performance.mark === 'function') {
            performance.mark('live:cards:refresh_frame');
        }

        const isVisible = true;
        const backlog = pendingWorkerRefresh.size;
        const boost = resumeBurstUntilMs != null && now < resumeBurstUntilMs;

        // Determine maxWorkersPerFrame based on sync tempo.
        // For fps-controlled modes, process ALL pending workers in each allowed frame
        // to ensure all cards update simultaneously.
        let maxWorkersPerFrame: number;
        let budgetMs: number;

        if (syncTempo === 'unlimited') {
            // Process all pending workers in a single frame for perfect sync.
            maxWorkersPerFrame = Infinity;
            budgetMs = Infinity;
        } else if (syncTempo === 2 || syncTempo === 4 || syncTempo === 8) {
            // FPS-controlled mode: process ALL pending workers when allowed to flush.
            // This ensures moves/eval/time/nodes are perfectly synced across all cards.
            maxWorkersPerFrame = Infinity;
            budgetMs = Infinity;
        } else {
            // 'auto': existing adaptive logic based on backlog and boost state.
            // "Catch-up burst" for visibility resume: finish a backlog quickly so the UI reaches the latest position.
            // This intentionally spends more time per frame right after resume, then returns to a smoother cadence.
            maxWorkersPerFrame = boost ? (backlog >= 8 ? 8 : 6) : isVisible ? (backlog >= 8 ? 3 : 2) : 0;
            budgetMs = boost ? (backlog >= 8 ? 40 : 30) : isVisible ? (backlog >= 8 ? 10 : 8) : 0;
        }

        const processed: number[] = [];
        for (const idx of Array.from(pendingWorkerRefresh)) {
            if (processed.length >= maxWorkersPerFrame) break;
            if (getNow() - start >= budgetMs) break;
            pendingWorkerRefresh.delete(idx);
            processed.push(idx);
            applyRefreshForWorker(idx);
        }
        const elapsed = Math.max(0, getNow() - start);
        recordLiveDiagnosticsMetric('live.cards.refresh', {
            triggered: 1,
            workers: processed.length,
            remaining: pendingWorkerRefresh.size,
            budget_ms: budgetMs,
            max_workers_per_frame: maxWorkersPerFrame,
            fps_interval_ms: fpsIntervalMs,
            latency_ms: elapsed,
        });
        if (!pendingWorkerRefresh.size) {
            resumeBurstUntilMs = null;
        }
    };

    const refreshCardsForWorker = (workerIdx: number): void => {
        if (!Number.isFinite(workerIdx)) return;
        // OFF mode: do not accept new refresh requests. Live updates are stopped.
        if (getSyncTempo() === 'off') {
            return;
        }
        // Always coalesce: work while hidden is wasted and hurts catch-up performance.
        if (typeof requestAnimationFrame !== 'function') {
            pendingWorkerRefresh.add(workerIdx);
            if (isDocumentVisible()) {
                flushPending();
            }
            return;
        }
        pendingWorkerRefresh.add(workerIdx);
        scheduleFrame();
    };

    const applyVmToState = (workerIdx: number, entry: PendingVmEntry): void => {
        const { vm } = entry;
        const getNow = () =>
            typeof performance !== 'undefined' && typeof performance.now === 'function'
                ? performance.now()
                : Date.now();
        const start = getNow();
        let tAfterSnapshot = start;
        let tAfterState = start;
        let clockMs = 0;
        let refreshMs = 0;

        const extractSnapshotGid = (snapshot: unknown): string | null => {
            if (!snapshot || typeof snapshot !== 'object') return null;
            const any = snapshot as Record<string, unknown>;
            const gid = any.game_id ?? any.gameId ?? any.gid;
            if (gid == null) return null;
            const trimmed = String(gid).trim();
            return trimmed ? trimmed : null;
        };

        const snapshotBefore = peekWorkerSnapshotRecord(state, workerIdx);
        const gidBefore = extractSnapshotGid(snapshotBefore ?? null);

        const vmGidRaw = (vm as unknown as { gid?: unknown }).gid;
        const vmGid = typeof vmGidRaw === 'string' && vmGidRaw.trim() ? vmGidRaw.trim() : null;
        if (
            vmGid &&
            vmGid !== gidBefore &&
            !vm.snapshot &&
            !entry.mergedNonMoveDelta &&
            typeof cacheWorkerSnapshot === 'function'
        ) {
            // Assignment can change before the first snapshot/delta arrives.
            // Cache a placeholder so UI labels (Worker #N: gid) update immediately.
            cacheWorkerSnapshot(workerIdx, {
                ...(createEmptyWorkerSnapshot() as unknown as WorkerSnapshotRecord),
                game_id: vmGid,
            });
        }

        // Apply authoritative snapshot first (if any), then apply merged non-move deltas.
        // Move history is authoritative from the merge worker snapshot; the main thread does not reconstruct
        // move arrays from buffered deltas.
        if (vm.snapshot && typeof cacheWorkerSnapshot === 'function') {
            cacheWorkerSnapshot(workerIdx, vm.snapshot as WorkerSnapshotRecord);
        }
        if (entry.mergedNonMoveDelta && typeof cacheWorkerSnapshot === 'function') {
            const base = peekWorkerSnapshotRecord(state, workerIdx);
            const merged = mergeWorkerSnapshotMutable(base, entry.mergedNonMoveDelta, (value) =>
                normalizeSFEN(value ?? ''),
            );
            cacheWorkerSnapshot(workerIdx, merged);
        }
        const snapshotAfter = peekWorkerSnapshotRecord(state, workerIdx);
        const gidAfter = extractSnapshotGid(snapshotAfter ?? null);
        let gidChanged = false;
        if (gidAfter) {
            const ws = getWorkerState(state, workerIdx);
            if (ws.currentGameId && ws.currentGameId !== gidAfter) {
                ws.prevGameId = ws.currentGameId;
                gidChanged = true;
            } else if (!ws.currentGameId) {
                gidChanged = true;
            }
            if (gidChanged) {
                resetWorkerRuntimeClockStateForNewGame(ws);
                ws.engineStatus = undefined;
            }
            ws.currentGameId = gidAfter;
            ws.lastGameId = gidAfter;
            setWorkerState(state, workerIdx, ws);
        }
        if (isDocumentVisible()) {
            debugCheckWorkerSnapshotLegality(
                workerIdx,
                snapshotAfter,
                entry.mergedNonMoveDelta ?? (vm.snapshotDelta as WorkerSnapshotUpdate | undefined),
            );
        }
        tAfterSnapshot = getNow();

        const vmDelta = (entry.mergedNonMoveDelta ?? vm.snapshotDelta) as WorkerSnapshotUpdate | undefined;
        const hasExplicitVmClock = Boolean(vm.clock && typeof vm.clock === 'object' && !Array.isArray(vm.clock));
        const shouldReconcileClock = Boolean(vm.snapshot) || hasExplicitVmClock || hasClockSignal(vmDelta);
        const snapshotClockPayload =
            shouldReconcileClock && snapshotAfter && typeof snapshotAfter === 'object'
                ? extractClockPayloadFromSnapshot(snapshotAfter as Record<string, unknown>)
                : null;

        if (snapshotAfter && typeof snapshotAfter === 'object') {
            const ws = getWorkerState(state, workerIdx);
            const snapshotRecord = snapshotAfter as Record<string, unknown>;
            updateTimeControlState(ws, snapshotRecord.time_control_black, snapshotRecord.time_control_white);
            const tcBlack =
                typeof snapshotRecord.time_control_black === 'string' ? snapshotRecord.time_control_black.trim() : '';
            const tcWhite =
                typeof snapshotRecord.time_control_white === 'string' ? snapshotRecord.time_control_white.trim() : '';
            if (tcBlack) ws.timeControlBlack = tcBlack;
            if (tcWhite) ws.timeControlWhite = tcWhite;
            const snapshotEngineStatus = asEngineStatusSnapshot(snapshotRecord.engine_status);
            if (snapshotEngineStatus) {
                ws.engineStatus = snapshotEngineStatus;
            } else if (entry.mergedNonMoveDelta) {
                const deltaEngineStatus = asEngineStatusSnapshot(
                    (entry.mergedNonMoveDelta as Record<string, unknown>).engine_status,
                );
                if (deltaEngineStatus) {
                    ws.engineStatus = deltaEngineStatus;
                }
            }
            if (snapshotClockPayload) {
                const now = Date.now();
                queueClockCorrections(ws, snapshotClockPayload, 'snapshot', now);
                maybeApplyImmediateClockStart(ws, snapshotClockPayload, now);
                updateTimeControlState(
                    ws,
                    snapshotClockPayload.time_control_black,
                    snapshotClockPayload.time_control_white,
                );
                const snapshotTcBlack = snapshotClockPayload.time_control_black;
                const snapshotTcWhite = snapshotClockPayload.time_control_white;
                if (typeof snapshotTcBlack === 'string') {
                    ws.timeControlBlack = snapshotTcBlack;
                }
                if (typeof snapshotTcWhite === 'string') {
                    ws.timeControlWhite = snapshotTcWhite;
                }
                const snapshotByoBlack = snapshotClockPayload.byoyomi_ms_black;
                const snapshotByoWhite = snapshotClockPayload.byoyomi_ms_white;
                if (typeof snapshotByoBlack === 'number') {
                    ws.byoyomiMsBlack = snapshotByoBlack;
                }
                if (typeof snapshotByoWhite === 'number') {
                    ws.byoyomiMsWhite = snapshotByoWhite;
                }
            }
            setWorkerState(state, workerIdx, ws);
        }

        if (hasExplicitVmClock) {
            const ws = getWorkerState(state, workerIdx);
            const clock = vm.clock as Record<string, unknown>;
            const now = Date.now();
            queueClockCorrections(ws, clock, 'snapshot', now);
            updateTimeControlState(ws, clock.time_control_black, clock.time_control_white);
            maybeApplyImmediateClockStart(ws, clock, now);
            const tcBlack = clock.time_control_black;
            const tcWhite = clock.time_control_white;
            if (typeof tcBlack === 'string') {
                ws.timeControlBlack = tcBlack;
            }
            if (typeof tcWhite === 'string') {
                ws.timeControlWhite = tcWhite;
            }
            const byoBlack = clock.byoyomi_ms_black;
            const byoWhite = clock.byoyomi_ms_white;
            if (typeof byoBlack === 'number') {
                ws.byoyomiMsBlack = byoBlack;
            }
            if (typeof byoWhite === 'number') {
                ws.byoyomiMsWhite = byoWhite;
            }
            setWorkerState(state, workerIdx, ws);
        }

        const clockIncrementDelta = (() => {
            const delta = vmDelta as Record<string, unknown> | undefined;
            if (!delta || typeof delta !== 'object') return null;
            const type = typeof delta.type === 'string' ? delta.type.trim().toLowerCase() : '';
            return type === 'clock_increment' ? delta : null;
        })();
        if (!clockIncrementDelta && hasExplicitVmClock) {
            const source = vm.clock as Record<string, unknown>;
            const side = inferIncrementSide(source);
            const incMs = toFiniteNumber(source.applied_increment_ms) ?? 0;
            const occurredAtMs = toFiniteNumber(source.occurred_at_ms);
            const lastOccurred = lastVmIncrementOccurredAtByWorker.get(workerIdx) ?? -1;
            if (occurredAtMs != null && occurredAtMs > lastOccurred && side) {
                const dedupeKey = `${occurredAtMs}:${side}:${incMs}`;
                lastVmIncrementOccurredAtByWorker.set(workerIdx, occurredAtMs);
                if (lastVmFlashKeyByWorker.get(workerIdx) !== dedupeKey) {
                    lastVmFlashKeyByWorker.set(workerIdx, dedupeKey);
                    applyByoyomiFreezeCache(workerIdx, source);
                    if (isDocumentVisible() && incMs > 0) {
                        flashIncrement(workerIdx, side, incMs);
                    }
                }
            }
        }

        if (typeof vm.current_ply === 'number' && Number.isFinite(vm.current_ply)) {
            const ws = getWorkerState(state, workerIdx);
            ws.lastAppliedPly = vm.current_ply;
            ws.lastAppliedAtMs = Date.now();
            setWorkerState(state, workerIdx, ws);
        }

        {
            const ws = getWorkerState(state, workerIdx);
            const ply =
                typeof vm.current_ply === 'number' && Number.isFinite(vm.current_ply)
                    ? vm.current_ply
                    : typeof snapshotAfter?.current_ply === 'number' && Number.isFinite(snapshotAfter.current_ply)
                      ? snapshotAfter.current_ply
                      : null;
            syncClockToTurnBoundary({
                ws,
                current_ply: ply,
                initialSfen: snapshotAfter?.initial_sfen ?? null,
                normalizeSFEN,
                nowMs: Date.now(),
            });
            setWorkerState(state, workerIdx, ws);
        }

        tAfterState = getNow();

        const cards = getCards();
        const target = `worker-latest:${workerIdx}`;
        const snapshotSummary = getWorkerSnapshot(workerIdx);
        const now = getNow();
        const vmDelayMs = Math.max(0, now - entry.receivedAt);
        let maxLagPly = 0;
        let maxLagViewPly = 0;
        const forceLatest = Boolean(vm.snapshot);
        for (const card of cards) {
            if (!card || card.source !== target) continue;
            const prevViewPly = Number(card.viewPly || 0);
            const lagPly = Math.max(0, snapshotSummary.liveDisplayPly - prevViewPly);
            if (lagPly > maxLagPly) {
                maxLagPly = lagPly;
                maxLagViewPly = prevViewPly;
            }
            if (forceLatest) {
                card.viewPly = snapshotSummary.liveDisplayPly;
                // Snapshot received: clear syncing state so overlay disappears.
                if (card.isSyncing) {
                    card.isSyncing = false;
                    delete card.syncingStartedAt;
                }
            } else if (card.autoSync) {
                // Keep the UI in sync with what we can actually display (contiguous moves).
                // `vm.current_ply` may advance via analysis-only updates even when the move list has holes.
                card.viewPly = snapshotSummary.liveDisplayPly;
            }
            if (gidAfter) {
                card.lastGameId = gidAfter;
            }
        }
        if ((gidChanged || (gidBefore !== gidAfter && gidAfter != null)) && isDocumentVisible()) {
            updateWorkerOptionLabels?.(workerIdx);
        }

        if ((hasExplicitVmClock || snapshotClockPayload) && isDocumentVisible()) {
            const clockStart = getNow();
            // Clock ticking is independent from sync tempo (except OFF).
            const syncTempo = getSyncTempo();
            if (syncTempo === 'off') {
                stopWorkerClockTimer(workerIdx);
            } else {
                if (canRunWorkerClock(workerIdx)) {
                    startWorkerClockTimer(workerIdx);
                } else {
                    stopWorkerClockTimer(workerIdx);
                }
            }
            clockMs = Math.max(0, getNow() - clockStart);
        }

        recordLiveDiagnosticsMetric('live.cards.ply_lag', {
            triggered: 1,
            worker: workerIdx,
            lag_ply: maxLagPly,
            live_ply: snapshotSummary.liveDisplayPly,
            view_ply: maxLagViewPly,
            vm_delay_ms: vmDelayMs,
        });
        // Unified refresh path: always refresh regardless of update type (move/clock/analysis).
        // This ensures moves/eval/time/nodes are always synced together per the unified sync design.
        const refreshStart = getNow();
        refreshCardsForWorker(workerIdx);
        refreshMs = Math.max(0, getNow() - refreshStart);

        const elapsed = Math.max(0, getNow() - start);
        const snapshotMs = Math.max(0, tAfterSnapshot - start);
        recordLiveDiagnosticsMetric('live.worker.vm', {
            triggered: 1,
            worker: workerIdx,
            latency_ms: elapsed,
        });
        recordLiveDiagnosticsMetric('live.worker.vm.detail', {
            triggered: 1,
            worker: workerIdx,
            snapshot_ms: snapshotMs,
            state_ms: Math.max(0, tAfterState - tAfterSnapshot),
            clock_ms: clockMs,
            refresh_ms: refreshMs,
            total_ms: elapsed,
        });
    };

    const flushPendingVm = (): void => {
        if (!pendingVmByWorker.size) return;
        const entries = Array.from(pendingVmByWorker.entries());
        pendingVmByWorker.clear();
        for (const [workerIdx, entry] of entries) {
            applyVmToState(workerIdx, entry);
        }
    };

    const enqueueVm = (vm: WorkerViewModelMessage, receivedAt: number): void => {
        if (typeof vm?.workerIdx !== 'number') return;
        // OFF mode: do not accept new VM updates. Live updates are stopped.
        if (getSyncTempo() === 'off') {
            return;
        }
        (state as unknown as { mergeWorkerActive?: boolean }).mergeWorkerActive = true;
        const nextDelta = vm.snapshotDelta as WorkerSnapshotUpdate | undefined;
        const nextHasMove = hasMoveDelta(nextDelta);
        if (nextHasMove && !vm.snapshot) {
            const nowMs = Date.now();
            const lastWarnAt = lastVmContractWarnAtByWorker.get(vm.workerIdx) ?? 0;
            if (nowMs - lastWarnAt >= 1500) {
                lastVmContractWarnAtByWorker.set(vm.workerIdx, nowMs);
                console.warn?.('[LiveVm] dropping move delta without authoritative snapshot', {
                    workerIdx: vm.workerIdx,
                    gid: vm.gid ?? null,
                    current_ply: vm.current_ply ?? null,
                });
            }
        }
        const normalizedNextDelta = nextHasMove ? undefined : nextDelta;
        const hidden = !isDocumentVisible();
        const existing = pendingVmByWorker.get(vm.workerIdx);
        if (existing) {
            const prev = existing.vm;
            const { merged, gidChanged } = mergeWorkerVmMessage(prev, vm);
            if (gidChanged) {
                // Do not carry merged deltas across gid boundaries.
                existing.mergedNonMoveDelta = undefined;
            }
            if (merged.snapshot && typeof merged.snapshot === 'object') {
                // Snapshot is authoritative for merged state fields.
                existing.mergedNonMoveDelta = undefined;
            }
            if (normalizedNextDelta) {
                existing.mergedNonMoveDelta = mergeSnapshotDelta(existing.mergedNonMoveDelta, normalizedNextDelta);
            }
            merged.snapshotDelta = existing.mergedNonMoveDelta;
            existing.vm = merged;
            existing.receivedAt = receivedAt;
            if (hidden) {
                pendingVmByWorker.delete(vm.workerIdx);
                applyVmToState(vm.workerIdx, existing);
                return;
            }
            pendingVmByWorker.set(vm.workerIdx, existing);
        } else {
            const entry: PendingVmEntry = {
                vm: { ...vm, snapshotDelta: undefined },
                receivedAt,
                mergedNonMoveDelta: undefined,
            };
            if (normalizedNextDelta) {
                entry.mergedNonMoveDelta = normalizedNextDelta;
                entry.vm.snapshotDelta = entry.mergedNonMoveDelta;
            }
            if (hidden) {
                applyVmToState(vm.workerIdx, entry);
                return;
            }
            pendingVmByWorker.set(vm.workerIdx, entry);
        }
        if (typeof requestAnimationFrame !== 'function') {
            if (isDocumentVisible()) {
                flushPendingVm();
            }
            return;
        }
        scheduleFrame();
    };

    const { unsubscribe } = createCardEventHandlers({
        events,
        freezeAllWorkerClocks,
        refreshCardsForWorker,
        cacheWorkerSnapshot,
        ensureIncrementPlaceholders: (workerIdx) => {
            if (!isDocumentVisible()) return;
            ensureIncrementPlaceholders(workerIdx);
        },
        flashIncrement: (workerIdx, color, incMs) => {
            if (!isDocumentVisible()) return;
            flashIncrement(workerIdx, color ?? '', incMs);
        },
        applyByoyomiFreezeCache,
        updateTimeControlLabelsForWorker: (workerIdx, clock) => {
            if (!isDocumentVisible()) return;
            updateTimeControlLabelsForWorker(workerIdx, clock);
        },
        startWorkerClockTimer: (workerIdx) => {
            if (!isDocumentVisible()) return;
            const syncTempo = getSyncTempo();
            if (syncTempo === 'off') {
                return;
            }
            if (canRunWorkerClock(workerIdx)) {
                startWorkerClockTimer(workerIdx);
            } else {
                stopWorkerClockTimer(workerIdx);
            }
        },
        stopWorkerClockTimer: (workerIdx) => {
            if (!isDocumentVisible()) return;
            stopWorkerClockTimer(workerIdx);
        },
        updateWorkerClockDisplay: (workerIdx) => {
            if (!isDocumentVisible()) return;
            updateWorkerClockDisplay(workerIdx);
        },
        showNotice: (message) => {
            if (!isDocumentVisible()) return;
            showNotice(message ?? '');
        },
        state: core.state as DashboardCoreState,
        onVm: (vm, receivedAt) => enqueueVm(vm, receivedAt),
        setMergeWorkerActive: (active) => {
            (state as unknown as { mergeWorkerActive?: boolean }).mergeWorkerActive = active;
        },
        handleEngineLogEvent: (payload) => {
            if (!isDocumentVisible()) return;
            handleEngineLogEvent?.(payload);
        },
    });

    const onVisibilityChange = (): void => {
        if (!isDocumentVisible()) {
            if (hiddenCoalesceStartMs == null) {
                hiddenCoalesceStartMs =
                    typeof performance !== 'undefined' && typeof performance.now === 'function'
                        ? performance.now()
                        : Date.now();
            }
            stopAllWorkerClockTimers();
            if (resumeDeferTimer != null) {
                clearTimeout(resumeDeferTimer);
                resumeDeferTimer = null;
            }
            if (pendingRafId !== null) {
                try {
                    cancelAnimationFrame(pendingRafId);
                } catch {
                    // ignore
                }
                pendingRafId = null;
            }
            if (pendingFpsTimer != null) {
                clearTimeout(pendingFpsTimer);
                pendingFpsTimer = null;
            }
            return;
        }
        if (hiddenCoalesceStartMs != null) {
            const now =
                typeof performance !== 'undefined' && typeof performance.now === 'function'
                    ? performance.now()
                    : Date.now();
            recordLiveDiagnosticsMetric('live.cards.visibility_resume', {
                triggered: 1,
                pending_workers: pendingWorkerRefresh.size,
                pending_vm: pendingVmByWorker.size,
                window_ms: Math.max(0, now - hiddenCoalesceStartMs),
            });
            hiddenCoalesceStartMs = null;
        }
        performResumeBurst('visibility');
    };

    /**
     * Perform the resume burst and deferred refresh logic.
     * Shared between visibility resume and internal tab resume.
     * @param source - 'visibility' for browser tab visibility, 'tab' for internal dashboard tab switch
     */
    const performResumeBurst = (source: 'visibility' | 'tab'): void => {
        const resumeStartMs =
            typeof performance !== 'undefined' && typeof performance.now === 'function'
                ? performance.now()
                : Date.now();
        updateWorkerOptionLabels?.(null);
        const nowMs =
            typeof performance !== 'undefined' && typeof performance.now === 'function'
                ? performance.now()
                : Date.now();
        const deferBoardUntil = nowMs + 350;
        // Defer expensive move list hydration right after resume so the first paint happens quickly.
        // Also mark cards as syncing so the UI shows a "syncing" overlay until fresh data arrives.
        for (const cardState of getCards()) {
            if (cardState?.source?.startsWith('worker-latest:')) {
                (cardState as unknown as { _deferBoardMovesUntil?: number })._deferBoardMovesUntil = deferBoardUntil;
                const workerIdx = Number(cardState.source.slice('worker-latest:'.length));
                if (Number.isFinite(workerIdx) && clearCsaWaitingCardSync(state, workerIdx, cardState)) {
                    continue;
                }
                // Mark as syncing to show overlay; cleared when snapshot arrives.
                cardState.isSyncing = true;
                cardState.syncingStartedAt = Date.now();
            }
        }
        // Give ourselves a short window to aggressively drain backlogs.
        resumeBurstUntilMs = nowMs + 1000;
        // We stop clock timers while hidden; restart them on resume so the UI immediately reflects time progression.
        const visibleWorkers = new Set<number>();
        for (const card of getCards()) {
            if (!card || typeof card.source !== 'string') continue;
            if (!card.source.startsWith('worker-latest:')) continue;
            const raw = card.source.slice('worker-latest:'.length);
            const idx = Number(raw);
            if (Number.isFinite(idx)) {
                visibleWorkers.add(idx);
            }
        }
        const syncTempo = getSyncTempo();
        if (syncTempo !== 'off') {
            for (const idx of Array.from(visibleWorkers)) {
                if (canRunWorkerClock(idx)) {
                    startWorkerClockTimer(idx);
                } else {
                    stopWorkerClockTimer(idx);
                }
                updateWorkerClockDisplay(idx);
            }
        }
        if (resumeDeferTimer != null) {
            clearTimeout(resumeDeferTimer);
        }
        const scheduleDeferredRefresh = () => {
            resumeDeferTimer = null;
            if (!isDocumentVisible()) return;
            for (const idx of Array.from(visibleWorkers)) {
                pendingWorkerRefresh.add(idx);
            }
            scheduleFrame();
            const deferredNow =
                typeof performance !== 'undefined' && typeof performance.now === 'function'
                    ? performance.now()
                    : Date.now();
            recordLiveDiagnosticsMetric('live.cards.resume_deferred_refresh', {
                triggered: 1,
                delay_ms: Math.max(0, deferredNow - resumeStartMs),
                visible_workers: visibleWorkers.size,
                pending_workers: pendingWorkerRefresh.size,
            });
        };
        const delayMs = 380;
        if (typeof requestIdleCallback === 'function') {
            resumeDeferTimer = setTimeout(() => {
                requestIdleCallback(() => scheduleDeferredRefresh(), { timeout: delayMs });
            }, delayMs);
        } else {
            resumeDeferTimer = setTimeout(() => scheduleDeferredRefresh(), delayMs);
        }

        // Schedule a timer to update delayed sync state after 3 seconds.
        // This ensures the "delayed" CSS class is applied even if no new updates arrive.
        // Clear any existing timer to avoid accumulation on rapid tab switches.
        if (syncDelayedTimer != null) {
            clearTimeout(syncDelayedTimer);
        }
        syncDelayedTimer = setTimeout(() => {
            syncDelayedTimer = null;
            for (const cardState of getCards()) {
                if (cardState?.isSyncing && typeof cardState.syncingStartedAt === 'number') {
                    const cardEl = document.getElementById(`card-${cardState.id}`);
                    if (cardEl && Date.now() - cardState.syncingStartedAt > 3000) {
                        cardEl.classList.add('worker-card--sync-delayed');
                    }
                }
            }
        }, 3100);

        recordLiveDiagnosticsMetric('live.cards.resume_burst', {
            triggered: 1,
            pending_workers: pendingWorkerRefresh.size,
            pending_vm: pendingVmByWorker.size,
        });
        const resumeEndMs =
            typeof performance !== 'undefined' && typeof performance.now === 'function'
                ? performance.now()
                : Date.now();
        recordLiveDiagnosticsMetric('live.cards.resume_burst_detail', {
            triggered: 1,
            total_ms: Math.max(0, resumeEndMs - resumeStartMs),
            visible_workers: visibleWorkers.size,
            pending_workers: pendingWorkerRefresh.size,
            pending_vm: pendingVmByWorker.size,
            source_code: source === 'visibility' ? 1 : 2,
        });

        scheduleFrame();
    };

    /**
     * Trigger a resume burst when the user switches back to the Live tab from another internal tab.
     * This enables the same catch-up behavior as visibility resume for internal tab switches.
     */
    const triggerTabResume = (): void => {
        // Skip if the document is hidden (browser tab is not visible)
        if (!isDocumentVisible()) {
            return;
        }
        performResumeBurst('tab');
    };

    if (typeof document !== 'undefined' && typeof document.addEventListener === 'function') {
        document.addEventListener('visibilitychange', onVisibilityChange);
    }

    const teardown = (): void => {
        if (typeof document !== 'undefined' && typeof document.removeEventListener === 'function') {
            document.removeEventListener('visibilitychange', onVisibilityChange);
        }
        unsubscribe();
        if (pendingRafId !== null) {
            try {
                cancelAnimationFrame(pendingRafId);
            } catch {
                // ignore
            }
            pendingRafId = null;
        }
        if (resumeDeferTimer != null) {
            clearTimeout(resumeDeferTimer);
            resumeDeferTimer = null;
        }
        if (syncDelayedTimer != null) {
            clearTimeout(syncDelayedTimer);
            syncDelayedTimer = null;
        }
        if (pendingFpsTimer != null) {
            clearTimeout(pendingFpsTimer);
            pendingFpsTimer = null;
        }
        pendingWorkerRefresh.clear();
        pendingVmByWorker.clear();
        lastVmFlashKeyByWorker.clear();
        lastVmIncrementOccurredAtByWorker.clear();
        lastLagLogAtByWorker.clear();
        inFlightUpdateByCardId.clear();
        inFlightStartedAtByCardId.clear();
        refreshAfterInFlightByCardId.clear();
        stopAllWorkerClockTimers();
    };

    /**
     * Called when syncTempo changes to reset fps timer state for immediate effect.
     * Clears any pending fps timer and resets lastFlushAtMs so the new tempo
     * takes effect immediately without waiting for the old timer to fire.
     *
     * For OFF mode, this does NOT trigger scheduleFrame since OFF stops all updates.
     */
    const onTempoChange = (): void => {
        // Clear pending fps timer to prevent stale timing from old tempo.
        if (pendingFpsTimer != null) {
            clearTimeout(pendingFpsTimer);
            pendingFpsTimer = null;
        }
        // Reset last flush time so next scheduleFrame() can flush immediately.
        lastFlushAtMs = 0;

        // OFF mode: do not trigger scheduleFrame. Updates are stopped.
        const syncTempo = getSyncTempo();
        if (syncTempo === 'off') {
            return;
        }

        // Trigger immediate frame if there's pending work.
        if (pendingWorkerRefresh.size || pendingVmByWorker.size) {
            scheduleFrame();
        }
    };

    /**
     * Stop all live rendering: clear pending queues, cancel timers, and stop clocks.
     * Used when switching to OFF mode to ensure immediate stop with no further updates.
     */
    const stopLiveRendering = (): void => {
        // Cancel any pending rAF.
        if (pendingRafId !== null) {
            try {
                cancelAnimationFrame(pendingRafId);
            } catch {
                // ignore
            }
            pendingRafId = null;
        }
        // Cancel fps timer.
        if (pendingFpsTimer != null) {
            clearTimeout(pendingFpsTimer);
            pendingFpsTimer = null;
        }
        // Cancel resume defer timer.
        if (resumeDeferTimer != null) {
            clearTimeout(resumeDeferTimer);
            resumeDeferTimer = null;
        }
        // Cancel sync delayed timer.
        if (syncDelayedTimer != null) {
            clearTimeout(syncDelayedTimer);
            syncDelayedTimer = null;
        }
        // Clear all pending queues.
        pendingWorkerRefresh.clear();
        pendingVmByWorker.clear();
        lastVmFlashKeyByWorker.clear();
        lastVmIncrementOccurredAtByWorker.clear();
        // Reset timing state.
        lastFlushAtMs = 0;
        hiddenCoalesceStartMs = null;
        resumeBurstUntilMs = null;
        // Stop all clock timers.
        stopAllWorkerClockTimers();
    };

    return {
        startWorkerClockTimer,
        stopWorkerClockTimer,
        stopAllWorkerClockTimers,
        updateTimeControlLabelsForWorker,
        ensureIncrementPlaceholders,
        flashIncrement,
        updateWorkerClockDisplay,
        freezeAllWorkerClocks,
        applyByoyomiFreezeCache,
        refreshCardsForWorker,
        triggerTabResume,
        onTempoChange,
        stopLiveRendering,
        teardown,
    };
}
