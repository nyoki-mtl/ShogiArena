import type { DashboardCore, DashboardCoreState } from '@/types/dashboard';
import type { LiveCardState } from '@/modules/live/types';
import { createClocksController } from './clocks';
import { createCardEventHandlers } from './events';
import type { WorkerSnapshotRecord } from './types';
import { recordLiveDiagnosticsMetric } from '@/modules/live/utils/liveNamespace/metrics';
import { getWorkerState, peekWorkerSnapshotRecord, setWorkerState } from '@/modules/live/state/updates';
import type { WorkerViewModelMessage } from '@/modules/live/services/updates/workerBridge';
import { mergeWorkerSnapshotMutable } from '@/modules/live/services/updates/worker/snapshotMerge';
import type { WorkerSnapshotUpdate } from '@/modules/live/types/updates';
import { debugCheckWorkerSnapshotLegality } from '@/modules/live/utils/debug/legalMoveCheck';
import { createEmptyWorkerSnapshot } from '@/modules/live/utils';
import {
    maybeApplyImmediateClockStart,
    queueClockCorrections,
    syncClockToTurnBoundary,
    updateTimeControlState,
} from '@/modules/live/utils/clockSync';

// Unified sync design: clock-only and analysis-only delta detection removed.
// All updates now follow the same refresh path for consistent sync timing.

function hasMoveDelta(delta: WorkerSnapshotUpdate | undefined): boolean {
    if (!delta || typeof delta !== 'object') return false;
    return Boolean((delta as { move?: unknown }).move || (delta as { ki2_move?: unknown }).ki2_move);
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
    const basePly = typeof base.currentPly === 'number' && Number.isFinite(base.currentPly) ? base.currentPly : null;
    const incomingPly =
        typeof incoming.currentPly === 'number' && Number.isFinite(incoming.currentPly) ? incoming.currentPly : null;
    if (basePly != null && incomingPly != null && basePly !== incomingPly) {
        // When updates arrive in bursts (e.g. mate sequences), we may merge multiple VMs into one rAF frame.
        // Never mix per-ply fields (move/eval/search stats) across different currentPly values, or we can
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

    const baseType = (base as { type?: unknown }).type;
    const incomingType = (incoming as { type?: unknown }).type;
    const basePriority = deltaPriority(base);
    const incomingPriority = deltaPriority(incoming);
    if (incomingPriority > basePriority && typeof incomingType === 'string') {
        (merged as Record<string, unknown>).type = incomingType;
    } else if (typeof baseType === 'string') {
        (merged as Record<string, unknown>).type = baseType;
    }

    if (incomingPly != null) merged.currentPly = incomingPly;
    else if (basePly != null) merged.currentPly = basePly;

    // Last-write-wins: ordering is guaranteed upstream.
    if (incoming.move != null) merged.move = incoming.move;
    if (incoming.ki2_move != null) merged.ki2_move = incoming.ki2_move;

    for (const [key, value] of Object.entries(incoming as Record<string, unknown>)) {
        if (value === undefined) continue;
        if (key === 'move' || key === 'ki2_move') continue;
        if (key === 'currentPly') continue;
        if (key === 'type') continue;
        (merged as Record<string, unknown>)[key] = value;
    }

    return merged;
}

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
}

export interface LiveCardsEventsController {
    startWorkerClockTimer: (workerIdx: number) => void;
    stopWorkerClockTimer: (workerIdx: number) => void;
    stopAllWorkerClockTimers: () => void;
    updateTimeControlLabelsForWorker: (
        workerIdx: number,
        clock: { timeControlBlack?: unknown; timeControlWhite?: unknown } | null | undefined,
    ) => void;
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
        moveDeltas: WorkerSnapshotUpdate[];
        mergedNonMoveDelta?: WorkerSnapshotUpdate;
    };
    const pendingVmByWorker = new Map<number, PendingVmEntry>();
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
        // Sync tempo determines clock update strategy:
        // - 'off': stop clock timer, no updates (live updates stopped)
        // - 'unlimited' or 'auto': use 100ms interval for smooth ticking
        // - 2/4/8 (fixed tempo): update clock synchronously with refresh
        const syncTempo = getSyncTempo();
        if (syncTempo === 'off') {
            // OFF mode: stop clock timer, no updates.
            stopWorkerClockTimer(workerIdx);
        } else if (syncTempo === 'unlimited' || syncTempo === 'auto') {
            startWorkerClockTimer(workerIdx);
            // Sync clock/turn highlight with the move refresh frame before the 100ms ticker kicks in.
            updateWorkerClockDisplay(workerIdx);
        } else {
            // Fixed tempo: stop any existing interval and update clock display directly.
            // This ensures perfect sync with moves/eval/nodes.
            stopWorkerClockTimer(workerIdx);
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
            entry.moveDeltas.length === 0 &&
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

        // Apply authoritative snapshot first (if any), then apply buffered move deltas in-order, then apply
        // the merged non-move delta. This prevents dropping intermediate move updates when updates arrive
        // faster than rAF (e.g. 20 moves/sec).
        if (vm.snapshot && typeof cacheWorkerSnapshot === 'function') {
            cacheWorkerSnapshot(workerIdx, vm.snapshot as WorkerSnapshotRecord);
        }
        if (entry.moveDeltas.length > 0 && typeof cacheWorkerSnapshot === 'function') {
            let base = peekWorkerSnapshotRecord(state, workerIdx);
            for (const delta of entry.moveDeltas) {
                base = mergeWorkerSnapshotMutable(base, delta, (value) => normalizeSFEN(value ?? ''));
            }
            cacheWorkerSnapshot(workerIdx, base);
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
        const lastMoveDelta = entry.moveDeltas.length ? entry.moveDeltas[entry.moveDeltas.length - 1] : undefined;
        if (isDocumentVisible()) {
            debugCheckWorkerSnapshotLegality(
                workerIdx,
                snapshotAfter,
                lastMoveDelta ?? entry.mergedNonMoveDelta ?? (vm.snapshotDelta as WorkerSnapshotUpdate | undefined),
            );
        }
        tAfterSnapshot = getNow();

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
            setWorkerState(state, workerIdx, ws);
        }

        if (vm.clock && typeof vm.clock === 'object') {
            const ws = getWorkerState(state, workerIdx);
            const clock = vm.clock as Record<string, unknown>;
            const now = Date.now();
            queueClockCorrections(ws, clock, 'snapshot', now);
            updateTimeControlState(ws, clock.time_control_black, clock.time_control_white);
            maybeApplyImmediateClockStart(ws, clock, now);
            if (typeof clock.time_control_black === 'string') {
                ws.timeControlBlack = clock.time_control_black;
            }
            if (typeof clock.time_control_white === 'string') {
                ws.timeControlWhite = clock.time_control_white;
            }
            if (typeof clock.byoyomi_ms_black === 'number') {
                ws.byoyomiMsBlack = clock.byoyomi_ms_black;
            }
            if (typeof clock.byoyomi_ms_white === 'number') {
                ws.byoyomiMsWhite = clock.byoyomi_ms_white;
            }
            setWorkerState(state, workerIdx, ws);
        }

        if (typeof vm.currentPly === 'number' && Number.isFinite(vm.currentPly)) {
            const ws = getWorkerState(state, workerIdx);
            ws.lastAppliedPly = vm.currentPly;
            ws.lastAppliedAtMs = Date.now();
            setWorkerState(state, workerIdx, ws);
        }

        {
            const ws = getWorkerState(state, workerIdx);
            const ply =
                typeof vm.currentPly === 'number' && Number.isFinite(vm.currentPly)
                    ? vm.currentPly
                    : typeof snapshotAfter?.currentPly === 'number' && Number.isFinite(snapshotAfter.currentPly)
                      ? snapshotAfter.currentPly
                      : null;
            syncClockToTurnBoundary({
                ws,
                currentPly: ply,
                initialSfen: snapshotAfter?.initial_sfen ?? null,
                normalizeSFEN,
                nowMs: Date.now(),
            });
            setWorkerState(state, workerIdx, ws);
        }

        tAfterState = getNow();

        const cards = getCards();
        const target = `worker-latest:${workerIdx}`;
        let gidChanged = false;
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
                // `vm.currentPly` may advance via analysis-only updates even when the move list has holes.
                card.viewPly = snapshotSummary.liveDisplayPly;
            }
            if (gidAfter) {
                card.lastGameId = gidAfter;
            }
        }
        if (gidAfter) {
            const ws = getWorkerState(state, workerIdx);
            if (ws.currentGameId && ws.currentGameId !== gidAfter) {
                ws.prevGameId = ws.currentGameId;
                gidChanged = true;
            } else if (!ws.currentGameId) {
                gidChanged = true;
            }
            if (gidChanged) {
                ws.lastSeenPly = undefined;
                ws.lastSideToMove = undefined;
                ws.pendingClockBySide = undefined;
                ws.clockActive = null;
                ws.startedAtMs = undefined;
                ws.blackFrozenByoText = null;
                ws.whiteFrozenByoText = null;
                ws.tcBlackHasTimePool = undefined;
                ws.tcWhiteHasTimePool = undefined;
                ws.tcBlackHasSearchLimit = undefined;
                ws.tcWhiteHasSearchLimit = undefined;
                ws.clockDisplayMode = undefined;
            }
            ws.currentGameId = gidAfter;
            ws.lastGameId = gidAfter;
            setWorkerState(state, workerIdx, ws);
        }
        if ((gidChanged || (gidBefore !== gidAfter && gidAfter != null)) && isDocumentVisible()) {
            updateWorkerOptionLabels?.(workerIdx);
        }

        if (vm.clock && isDocumentVisible()) {
            const clockStart = getNow();
            // Sync tempo determines clock update strategy:
            // - 'off': stop clock timer, no updates (live updates stopped)
            // - 'unlimited' or 'auto': use 100ms interval for smooth ticking
            // - 2/4/8 (fixed tempo): update only on refresh for perfect sync with moves/eval/nodes
            const syncTempo = getSyncTempo();
            if (syncTempo === 'off') {
                // OFF mode: stop clock timer, no updates.
                stopWorkerClockTimer(workerIdx);
            } else if (syncTempo === 'unlimited' || syncTempo === 'auto') {
                startWorkerClockTimer(workerIdx);
            } else {
                // Fixed tempo: stop interval and let refresh handle clock updates.
                stopWorkerClockTimer(workerIdx);
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
        const hidden = !isDocumentVisible();
        const existing = pendingVmByWorker.get(vm.workerIdx);
        if (existing) {
            const prev = existing.vm;
            const merged: WorkerViewModelMessage = { ...prev };
            merged.workerIdx = vm.workerIdx;
            if (Object.hasOwn(vm, 'gid')) {
                merged.gid = vm.gid;
            }
            if (vm.currentPly != null) merged.currentPly = vm.currentPly;
            if (vm.sfen != null) merged.sfen = vm.sfen;
            if (vm.lastMove != null) merged.lastMove = vm.lastMove;
            if (vm.ki2Move != null) merged.ki2Move = vm.ki2Move;
            if (vm.evalCp != null) merged.evalCp = vm.evalCp;

            if (vm.clock && typeof vm.clock === 'object') {
                merged.clock = vm.clock as Record<string, unknown>;
            }
            // Snapshots are authoritative. If a snapshot arrives within the same frame as replayed diffs,
            // drop buffered move deltas and use the snapshot as the new baseline.
            if (vm.snapshot && typeof vm.snapshot === 'object') {
                merged.snapshot = vm.snapshot as Record<string, unknown>;
                existing.moveDeltas = [];
                existing.mergedNonMoveDelta = undefined;
            }
            if (nextDelta) {
                if (nextHasMove) {
                    existing.moveDeltas.push(nextDelta);
                } else {
                    existing.mergedNonMoveDelta = mergeSnapshotDelta(existing.mergedNonMoveDelta, nextDelta);
                }
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
                moveDeltas: [],
                mergedNonMoveDelta: undefined,
            };
            if (nextDelta) {
                if (nextHasMove) {
                    entry.moveDeltas.push(nextDelta);
                } else {
                    entry.mergedNonMoveDelta = nextDelta;
                    entry.vm.snapshotDelta = entry.mergedNonMoveDelta;
                }
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
            flashIncrement(workerIdx, color, incMs);
        },
        applyByoyomiFreezeCache,
        updateTimeControlLabelsForWorker: (workerIdx, clock) => {
            if (!isDocumentVisible()) return;
            updateTimeControlLabelsForWorker(workerIdx, clock);
        },
        startWorkerClockTimer: (workerIdx) => {
            if (!isDocumentVisible()) return;
            // Sync tempo determines clock strategy:
            // - 'off': do not start timer (live updates stopped)
            // - 'unlimited' or 'auto': use interval
            // - 2/4/8 (fixed tempo): skip interval, rely on refresh sync
            const syncTempo = getSyncTempo();
            if (syncTempo === 'off') {
                // OFF mode: do not start clock timer.
                return;
            }
            if (syncTempo === 'unlimited' || syncTempo === 'auto') {
                startWorkerClockTimer(workerIdx);
            }
        },
        updateWorkerClockDisplay: (workerIdx) => {
            if (!isDocumentVisible()) return;
            updateWorkerClockDisplay(workerIdx);
        },
        showNotice: (message) => {
            if (!isDocumentVisible()) return;
            showNotice(message);
        },
        state: core.state as DashboardCoreState,
        onVm: (vm, receivedAt) => enqueueVm(vm, receivedAt),
        setMergeWorkerActive: (active) => {
            (state as unknown as { mergeWorkerActive?: boolean }).mergeWorkerActive = active;
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
            console.debug?.('[Live] visibility resume', {
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
        // Sync tempo determines clock behavior on resume:
        // - 'off': do nothing (live updates stopped)
        // - 'unlimited' or 'auto': restart interval for smooth ticking
        // - 2/4/8 (fixed tempo): just update display once; refresh will handle further updates
        const syncTempo = getSyncTempo();
        if (syncTempo !== 'off') {
            for (const idx of Array.from(visibleWorkers)) {
                if (syncTempo === 'unlimited' || syncTempo === 'auto') {
                    startWorkerClockTimer(idx);
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
        console.debug?.('[Live] resume burst', {
            source,
            pending_workers: pendingWorkerRefresh.size,
            pending_vm: pendingVmByWorker.size,
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
