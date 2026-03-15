import { INCREMENT_PLACEHOLDER } from '@/modules/live/utils';
import type { LiveCardState } from '@/modules/live/types';
import type { WorkerSnapshotRecord } from './types';
import type { DashboardCoreState } from '@/types/dashboard';
import { getWorkerStateEntry, setWorkerStateEntry } from './state';
import { recordLiveDiagnosticsMetric } from '@/modules/live/utils/liveNamespace/metrics';

const BYOYOMI_POSTMOVE_BEHAVIOR = 'freeze';

interface RunningClockDisplay {
    mainRemainMs: number;
    byoyomiRemainMs: number | null;
}

function toFiniteNumber(value: unknown): number | null {
    if (typeof value !== 'number' || !Number.isFinite(value)) return null;
    return value;
}

export function computeByoyomiRemainMs(
    byoyomiMs: number,
    elapsedMs: number,
    mainRemainAtTurnStartMs: number | null | undefined,
): number {
    const safeByoyomi = Math.max(0, Number(byoyomiMs || 0));
    const safeElapsed = Math.max(0, Number(elapsedMs || 0));
    const mainRemain = Math.max(0, Number(mainRemainAtTurnStartMs ?? 0));
    const byoyomiElapsed = Math.max(0, safeElapsed - mainRemain);
    return Math.max(0, safeByoyomi - byoyomiElapsed);
}

export function computeRunningClockDisplay(
    remainMs: number,
    byoyomiMs: number,
    elapsedMs: number,
): RunningClockDisplay {
    const safeRemain = Math.max(0, Number(remainMs || 0));
    const safeByoyomi = Math.max(0, Number(byoyomiMs || 0));
    const safeElapsed = Math.max(0, Number(elapsedMs || 0));
    const rawMainRemain = safeRemain - safeElapsed;
    if (safeByoyomi > 0 && rawMainRemain <= 0) {
        return {
            mainRemainMs: 0,
            byoyomiRemainMs: computeByoyomiRemainMs(safeByoyomi, safeElapsed, safeRemain),
        };
    }
    return {
        mainRemainMs: Math.max(0, rawMainRemain),
        byoyomiRemainMs: null,
    };
}

interface ClocksDeps {
    state: DashboardCoreState;
    getCards: () => LiveCardState[];
    hasWorkerCards: (workerIdx: number) => boolean;
    getWorkerSnapshot: (workerIdx: number) => { data: WorkerSnapshotRecord; maxPly: number; liveDisplayPly: number };
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
    /** Get current sync tempo setting. Used to control clock interval behavior.
     *  'off' = live updates stopped. */
    getSyncTempo?: () => 'off' | 'auto' | 2 | 4 | 8 | 'unlimited';
}

export function createClocksController(deps: ClocksDeps) {
    const {
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
        getSyncTempo,
    } = deps;

    type WorkerClockTimer = ReturnType<typeof setInterval>;
    const workerClockTimers = new Map<number, WorkerClockTimer>();
    const incrementTimeouts = new Map<string, ReturnType<typeof setTimeout>>();
    const incrementExpiresAt = new Map<string, number>();

    /**
     * Start clock timer for a worker. Respects syncTempo setting:
     * - 'off': do NOT start interval; live updates are stopped
     * - otherwise: start 100ms interval for smooth ticking
     */
    function startWorkerClockTimer(workerIdx: number): void {
        if (!hasWorkerCards(workerIdx)) {
            return;
        }
        const syncTempo = getSyncTempo?.() ?? 'auto';
        // OFF mode: do not start interval. Live updates are stopped.
        if (syncTempo === 'off') {
            stopWorkerClockTimer(workerIdx);
            return;
        }
        // Avoid recreating the interval on every incoming clock update; that causes unnecessary work
        // and can introduce UI jitter under high-frequency updates.
        if (workerClockTimers.has(workerIdx)) {
            return;
        }
        const tick = () => {
            updateWorkerClockDisplay(workerIdx);
        };
        const timerId = setInterval(tick, 100);
        workerClockTimers.set(workerIdx, timerId);
    }

    function stopWorkerClockTimer(workerIdx: number): void {
        const timerId = workerClockTimers.get(workerIdx);
        if (timerId) {
            clearInterval(timerId);
        }
        workerClockTimers.delete(workerIdx);
    }

    function stopAllWorkerClockTimers(): void {
        for (const workerIdx of Array.from(workerClockTimers.keys())) {
            stopWorkerClockTimer(workerIdx);
        }
    }

    function updateTimeControlLabelsForWorker(
        workerIdx: number,
        clock: { timeControlBlack?: unknown; timeControlWhite?: unknown } | null | undefined,
    ): void {
        if (!clock) return;
        const clockFields = clock as Record<string, unknown>;
        const tcBlack = clock.timeControlBlack ?? clockFields.time_control_black;
        const tcWhite = clock.timeControlWhite ?? clockFields.time_control_white;
        if (!tcBlack && !tcWhite) return;
        const labelB = tcBlack ? formatTimeControlShort(tcBlack) : null;
        const labelW = tcWhite ? formatTimeControlShort(tcWhite) : null;
        for (const cardState of getCards()) {
            if (cardState && cardState.source === `worker-latest:${workerIdx}`) {
                const btc = document.getElementById(`black-tc-${cardState.id}`);
                const wtc = document.getElementById(`white-tc-${cardState.id}`);
                if (btc && labelB !== null) btc.textContent = labelB;
                if (wtc && labelW !== null) wtc.textContent = labelW;
            }
        }
    }

    function ensureIncrementPlaceholders(workerIdx: number): void {
        for (const cardState of getCards()) {
            if (cardState && cardState.source === `worker-latest:${workerIdx}`) {
                const bi = document.getElementById(`black-inc-${cardState.id}`);
                const wi = document.getElementById(`white-inc-${cardState.id}`);
                const blackKey = `black-inc-${cardState.id}`;
                const whiteKey = `white-inc-${cardState.id}`;
                if (bi && !bi.textContent) bi.textContent = INCREMENT_PLACEHOLDER;
                if (wi && !wi.textContent) wi.textContent = INCREMENT_PLACEHOLDER;
                if (bi) bi.classList.remove('inc-flash');
                if (wi) wi.classList.remove('inc-flash');
                const blackTimeout = incrementTimeouts.get(blackKey);
                const whiteTimeout = incrementTimeouts.get(whiteKey);
                if (blackTimeout) clearTimeout(blackTimeout);
                if (whiteTimeout) clearTimeout(whiteTimeout);
                incrementTimeouts.delete(blackKey);
                incrementTimeouts.delete(whiteKey);
                incrementExpiresAt.delete(blackKey);
                incrementExpiresAt.delete(whiteKey);
            }
        }
    }

    function flashIncrement(workerIdx: number, side: string, incMs: number): void {
        if (!incMs || incMs <= 0) return;
        const targetSide = side === 'black' ? 'black' : 'white';
        for (const cardState of getCards()) {
            if (!cardState || cardState.source !== `worker-latest:${workerIdx}`) continue;
            const elId = `${targetSide}-inc-${cardState.id}`;
            const el = document.getElementById(elId);
            if (!el) continue;
            const timeoutKey = `${elId}`;
            if (incrementTimeouts.has(timeoutKey)) {
                clearTimeout(incrementTimeouts.get(timeoutKey));
                incrementTimeouts.delete(timeoutKey);
            }
            el.textContent = `+${formatInc(incMs)}`;
            el.classList.add('inc-flash');
            incrementExpiresAt.set(timeoutKey, Date.now() + 1200);
            const timeoutId = setTimeout(() => {
                const current = document.getElementById(elId);
                if (current) {
                    current.textContent = INCREMENT_PLACEHOLDER;
                    current.classList.remove('inc-flash');
                }
                incrementTimeouts.delete(timeoutKey);
                incrementExpiresAt.delete(timeoutKey);
            }, 1000);
            incrementTimeouts.set(timeoutKey, timeoutId);
        }
    }

    function cleanupExpiredIncrementBadge(cardId: string | number, side: 'black' | 'white', now: number): void {
        const elId = `${side}-inc-${cardId}`;
        const key = elId;
        const expiresAt = incrementExpiresAt.get(key);
        if (typeof expiresAt !== 'number' || now < expiresAt) return;
        const timeoutId = incrementTimeouts.get(key);
        if (timeoutId) {
            clearTimeout(timeoutId);
            incrementTimeouts.delete(key);
        }
        incrementExpiresAt.delete(key);
        const el = document.getElementById(elId);
        if (el) {
            el.textContent = INCREMENT_PLACEHOLDER;
            el.classList.remove('inc-flash');
        }
    }

    function updateWorkerClockDisplay(workerIdx: number): void {
        const t0 =
            typeof performance !== 'undefined' && typeof performance.now === 'function'
                ? performance.now()
                : Date.now();
        if (!hasWorkerCards(workerIdx)) {
            stopWorkerClockTimer(workerIdx);
            return;
        }

        const ws = getWorkerStateEntry(state, workerIdx);
        const active = ws.clockActive || null;
        const blackRemainBase = Number(ws.blackRemainMs || 0);
        const whiteRemainBase = Number(ws.whiteRemainMs || 0);
        const startedAt = Number(ws.startedAtMs || 0);
        const now = Date.now();
        const elapsed = Math.max(0, now - startedAt);

        const tcMode = ws.clockDisplayMode;
        const searchLimitsOnly = tcMode === 'search';

        const wsEntry = getWorkerStateEntry(state, workerIdx);
        const byoyomiMsBlack = toNumber(wsEntry.byoyomiMsBlack, 0);
        const byoyomiMsWhite = toNumber(wsEntry.byoyomiMsWhite, 0);
        let blackRemainDisplay = blackRemainBase;
        let whiteRemainDisplay = whiteRemainBase;
        let blackByoyomiDisplay: number | null = null;
        let whiteByoyomiDisplay: number | null = null;

        if (!searchLimitsOnly) {
            if (active === 'black') {
                const running = computeRunningClockDisplay(blackRemainBase, byoyomiMsBlack, elapsed);
                blackRemainDisplay = running.mainRemainMs;
                blackByoyomiDisplay = running.byoyomiRemainMs;
            } else if (active === 'white') {
                const running = computeRunningClockDisplay(whiteRemainBase, byoyomiMsWhite, elapsed);
                whiteRemainDisplay = running.mainRemainMs;
                whiteByoyomiDisplay = running.byoyomiRemainMs;
            }
        }

        const snapshot = getWorkerSnapshot(workerIdx);
        let cardsTouched = 0;
        let liveCardsTouched = 0;
        let staticCardsTouched = 0;
        for (const cardState of getCards()) {
            if (!cardState || cardState.source !== `worker-latest:${workerIdx}`) continue;
            cardsTouched += 1;
            const { isLiveView } = resolveCardView(cardState, snapshot);
            if (!isLiveView) {
                staticCardsTouched += 1;
                updateStaticClocksForCard(cardState.id, snapshot.data);
                const rowB = document.getElementById(`row-black-${cardState.id}`);
                const rowW = document.getElementById(`row-white-${cardState.id}`);
                if (rowB && rowW) {
                    const sfen = normalizeSFEN(snapshot.data.initial_sfen || 'startpos');
                    const parts = sfen.split(' ');
                    const startIsBlack = parts.length > 1 ? parts[1] === 'b' : true;
                    const viewPlyRaw = cardState.viewPly ?? 0;
                    const viewPly = Math.min(Number(viewPlyRaw) || 0, snapshot.maxPly);
                    const sideToMoveIsBlack = startIsBlack ? viewPly % 2 === 0 : viewPly % 2 === 1;
                    rowB.classList.toggle('active', sideToMoveIsBlack);
                    rowW.classList.toggle('active', !sideToMoveIsBlack);
                }
                continue;
            }
            liveCardsTouched += 1;
            cleanupExpiredIncrementBadge(cardState.id, 'black', now);
            cleanupExpiredIncrementBadge(cardState.id, 'white', now);
            const rowB = document.getElementById(`row-black-${cardState.id}`);
            const rowW = document.getElementById(`row-white-${cardState.id}`);
            const remElB = document.getElementById(`black-remaining-${cardState.id}`);
            const remElW = document.getElementById(`white-remaining-${cardState.id}`);

            if (remElB) {
                if (searchLimitsOnly) {
                    remElB.textContent = active === 'black' ? formatCountUp(elapsed) : '00:00';
                    remElB.classList.remove('byoyomi');
                } else if (active === 'black') {
                    if (blackByoyomiDisplay != null) {
                        remElB.textContent = formatByoyomi(blackByoyomiDisplay);
                        remElB.classList.add('byoyomi');
                    } else {
                        remElB.textContent = formatRemain(blackRemainDisplay);
                        remElB.classList.remove('byoyomi');
                    }
                } else {
                    const frozen = wsEntry.blackFrozenByoText;
                    if (frozen) {
                        remElB.textContent = frozen;
                        remElB.classList.add('byoyomi');
                    } else if (byoyomiMsBlack > 0 && blackRemainBase <= 0) {
                        remElB.textContent = formatByoyomi(byoyomiMsBlack);
                        remElB.classList.add('byoyomi');
                    } else {
                        remElB.textContent = formatRemain(blackRemainBase);
                        remElB.classList.remove('byoyomi');
                    }
                }
            }

            if (remElW) {
                if (searchLimitsOnly) {
                    remElW.textContent = active === 'white' ? formatCountUp(elapsed) : '00:00';
                    remElW.classList.remove('byoyomi');
                } else if (active === 'white') {
                    if (whiteByoyomiDisplay != null) {
                        remElW.textContent = formatByoyomi(whiteByoyomiDisplay);
                        remElW.classList.add('byoyomi');
                    } else {
                        remElW.textContent = formatRemain(whiteRemainDisplay);
                        remElW.classList.remove('byoyomi');
                    }
                } else {
                    const frozen = wsEntry.whiteFrozenByoText;
                    if (frozen) {
                        remElW.textContent = frozen;
                        remElW.classList.add('byoyomi');
                    } else if (byoyomiMsWhite > 0 && whiteRemainBase <= 0) {
                        remElW.textContent = formatByoyomi(byoyomiMsWhite);
                        remElW.classList.add('byoyomi');
                    } else {
                        remElW.textContent = formatRemain(whiteRemainBase);
                        remElW.classList.remove('byoyomi');
                    }
                }
            }

            if (rowB && rowW) {
                rowB.classList.toggle('active', active === 'black');
                rowW.classList.toggle('active', active === 'white');
            }
        }

        setWorkerStateEntry(state, workerIdx, wsEntry);

        const t1 =
            typeof performance !== 'undefined' && typeof performance.now === 'function'
                ? performance.now()
                : Date.now();
        recordLiveDiagnosticsMetric('live.cards.clock_update', {
            triggered: 1,
            worker: workerIdx,
            latency_ms: Math.max(0, t1 - t0),
            cards: cardsTouched,
            live_cards: liveCardsTouched,
            static_cards: staticCardsTouched,
        });
    }

    function freezeAllWorkerClocks(): void {
        const workerIds =
            state.workers instanceof Map ? Array.from(state.workers.keys()).filter((idx) => Number.isFinite(idx)) : [];
        for (const workerIdx of workerIds) {
            updateWorkerClockDisplay(Number(workerIdx));
        }
        stopAllWorkerClockTimers();
    }

    function applyByoyomiFreezeCache(
        workerIdx: number,
        clock: { side?: string; occurredAtMs?: number } | null | undefined,
    ): void {
        if (!clock || !clock.side) return;
        const ws = getWorkerStateEntry(state, workerIdx);
        const side = String(clock.side).toLowerCase();
        const byoyomiMs = side === 'black' ? Number(ws.byoyomiMsBlack || 0) : Number(ws.byoyomiMsWhite || 0);
        if (byoyomiMs <= 0) {
            if (side === 'black') ws.blackFrozenByoText = null;
            else ws.whiteFrozenByoText = null;
            setWorkerStateEntry(state, workerIdx, ws);
            return;
        }
        const source = clock as Record<string, unknown>;
        const snake = clock as unknown as {
            occurred_at_ms?: number;
            pre_black_remain_ms?: number;
            pre_white_remain_ms?: number;
            preBlackRemainMs?: number;
            preWhiteRemainMs?: number;
        };
        const occurredAt = Number(clock.occurredAtMs ?? snake.occurred_at_ms ?? Date.now());
        const startedAt = Number(ws.startedAtMs || occurredAt);
        const elapsedMs = Math.max(0, occurredAt - startedAt);
        const preRemainMs =
            side === 'black'
                ? toFiniteNumber(source.pre_black_remain_ms ?? snake.preBlackRemainMs)
                : toFiniteNumber(source.pre_white_remain_ms ?? snake.preWhiteRemainMs);
        const freezeMs = computeByoyomiRemainMs(byoyomiMs, elapsedMs, preRemainMs);
        const text = BYOYOMI_POSTMOVE_BEHAVIOR === 'freeze' ? formatByoyomi(freezeMs) : formatByoyomi(byoyomiMs);
        if (side === 'black') ws.blackFrozenByoText = text;
        else ws.whiteFrozenByoText = text;
        setWorkerStateEntry(state, workerIdx, ws);
    }

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
    };
}
