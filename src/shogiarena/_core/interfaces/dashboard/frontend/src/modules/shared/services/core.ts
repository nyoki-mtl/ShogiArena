import type {
    DashboardCore,
    DashboardCoreState,
    DashboardCoreStateKey,
    DashboardEventHandler,
    DashboardFetcher,
    DashboardNoticeOptions,
    DashboardNoticeVariant,
    DashboardShared,
    DashboardStateUpdater,
} from '@/types/dashboard';
import type { JsonObject } from '@/types/shared';
import { crash, formatErrorMessage } from '@/modules/shared/utils/errors';

declare const window: Window & typeof globalThis;

type EventListeners = Map<string, Set<DashboardEventHandler>>;

type FetcherRegistry = Map<string, DashboardFetcher>;

const state: DashboardCoreState = {
    cards: [],
    workers: new Map(),
    boardAdapters: new Map(),
    workerSnapshots: new Map(),
    numWorkers:
        typeof window.__ARENA_NUM_WORKERS__ === 'number' && Number.isFinite(window.__ARENA_NUM_WORKERS__)
            ? Number(window.__ARENA_NUM_WORKERS__)
            : 0,
    maxLiveBoards: 6,
    nextCardId: 0,
    gameDataCache: new Map(),
    gamesSignature: '',
    gamesSignatureByCard: new Map(),
    gameMetadata: new Map(),
    offlineNotified: false,
    liveStreamingEnabled: true,
    engineFinalRatings: new Map(),
    highlightEngine: null,
    prevRatings: new Map(),
    ratingDeltas: new Map(),
    ratingDeltaTimerId: null,
    pendingDeltaHint: new Map(),
    pentaCache: new Map(),
    gamesList: null,
    popoverTimer: null,
    popoverPinned: false,
    popoverInvokerEl: null,
    expanded: null,
    standingsTCMap: new Map(),
    runtimeMode: 'unknown',
    liveViewSnapshot: null,
    spsaMode: false,
    spsaSummary: null,
    spsaParams: null,
    spsaEvents: null,
    spsaTimerId: null,
    spsaSort: 'time_desc',
    selectedCardId: null,
    gamesListCache: undefined,
    openingStatsRendered: false,
    lastOpeningStatsCompletedGames: null,
    standingsSort: { key: null, direction: null },
    openingStatsSort: { key: 'games', direction: 'desc' },
    openingStatsData: null,
    engineFullOptions: new Map(),
    openingBoardAdapter: null,
    openingBoardElement: null,
    openingBoardInfo: null,
    openingSelectedRow: null,
    openingDetailRow: null,
    selectedOpeningKey: null,
    openingDetailMode: null,
};

const fetchers: FetcherRegistry = new Map();
const eventListeners: EventListeners = new Map();

function warnSoftFailure(context: string, error: unknown): never {
    const message = `[Dashboard] ${context}: ${formatErrorMessage(error)}`;
    console.error(message, error);
    throw crash(context, error);
}

function on<T = unknown>(eventName: string, handler: DashboardEventHandler<T>): () => void {
    if (!eventName || typeof handler !== 'function') {
        throw new Error('DashboardCore.events.on requires an event name and handler');
    }
    const listeners = eventListeners.get(eventName) ?? new Set();
    listeners.add(handler as DashboardEventHandler);
    eventListeners.set(eventName, listeners);
    return () => off(eventName, handler);
}

function off<T = unknown>(eventName: string, handler: DashboardEventHandler<T>): void {
    const listeners = eventListeners.get(eventName);
    if (!listeners) {
        return;
    }
    listeners.delete(handler as DashboardEventHandler);
    if (listeners.size === 0) {
        eventListeners.delete(eventName);
    }
}

function emit<T = unknown>(eventName: string, payload?: T): void {
    const listeners = eventListeners.get(eventName);
    if (!listeners || listeners.size === 0) {
        return;
    }
    for (const listener of Array.from(listeners)) {
        try {
            (listener as DashboardEventHandler<T>)((payload ?? undefined) as T);
        } catch (error) {
            warnSoftFailure(`DashboardCore.events handler failed for ${eventName}`, error);
        }
    }
}

function mutateState<K extends DashboardCoreStateKey>(
    key: K,
    updater: DashboardStateUpdater<K>,
): DashboardCoreState[K] {
    const hasKey = Object.hasOwn(state, key);
    if (!hasKey) {
        throw new Error(`DashboardCore.mutateState received unknown key: ${String(key)}`);
    }
    const current = state[key];
    const next =
        typeof updater === 'function'
            ? (updater as (value: DashboardCoreState[K]) => DashboardCoreState[K])(current)
            : updater;
    if (next === undefined) {
        return state[key];
    }
    state[key] = next;
    return state[key];
}

function getStateSlice<K extends DashboardCoreStateKey>(keys: readonly K[]): Pick<DashboardCoreState, K> {
    if (!Array.isArray(keys)) {
        throw new Error('DashboardCore.getStateSlice expects an array of keys');
    }

    const result = {} as Pick<DashboardCoreState, K>;
    for (const key of keys) {
        if (!Object.hasOwn(state, key)) {
            continue;
        }
        const typedKey = key as K;
        result[typedKey] = state[typedKey];
    }
    return result;
}

function registerFetcher(name: string, fn: DashboardFetcher): void {
    if (!name || typeof fn !== 'function') {
        throw new Error('DashboardCore.registerFetcher requires a name and function');
    }
    fetchers.set(String(name), fn);
}

function getFetcher(name: string): DashboardFetcher | null {
    return fetchers.get(name) ?? null;
}

function getShared(): DashboardShared | null {
    return window.DashboardShared ?? null;
}

function showNotice(message: string, variant?: DashboardNoticeVariant, options?: DashboardNoticeOptions): void {
    const normalizedVariant =
        variant === 'info' || variant === 'success' || variant === 'warn' || variant === 'error'
            ? (variant as DashboardNoticeVariant)
            : 'info';
    const shared = getShared();
    const impl = shared?.notices?.showNotice;
    if (typeof impl === 'function') {
        impl(message, normalizedVariant, options);
        return;
    }
    const payload = String(message ?? '');
    if (normalizedVariant === 'error') {
        console.error('[Dashboard Notice]', payload);
    } else {
        console.log('[Dashboard Notice]', payload);
    }
}

function showApiError(title: string, detail: unknown): void {
    try {
        const container = document.querySelector('header') ?? document.body;
        let banner = document.getElementById('apiErrorBanner');
        if (!banner) {
            banner = document.createElement('div');
            banner.id = 'apiErrorBanner';
            banner.style.cssText =
                'margin:8px 0; padding:8px 12px; border-radius:6px; background:#fef2f2; color:#991b1b; border:1px solid #fecaca; font-weight:600;';
            container.insertBefore(banner, container.firstChild);
        }
        const now = new Date();
        const ts = `${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}:${String(now.getSeconds()).padStart(2, '0')}`;
        const detailsStr = typeof detail === 'string' ? detail : (JSON.stringify(detail, null, 2) ?? '');
        banner.innerHTML = `<div>${title}</div><pre style="white-space:pre-wrap; font-weight:400; margin:4px 0 0 0;">[${ts}] ${detailsStr}</pre>`;
        console.error('[Dashboard API]', title, detail);
    } catch (error) {
        console.error('[Dashboard API] Error while reporting API error', error, { title, detail });
    }
}

function showSpsaDetailStreamError(title: string, detail: unknown): void {
    const formattedTitle = `[SPSA detail stream] ${title}`;
    const detailKey = JSON.stringify(detail ?? '');
    const now = Date.now();
    const SUPPRESS_MS = 10_000;

    if (
        lastSpsaDetailNotice &&
        lastSpsaDetailNotice.detailKey === detailKey &&
        now - lastSpsaDetailNotice.ts < SUPPRESS_MS
    ) {
        return;
    }

    lastSpsaDetailNotice = { title: formattedTitle, detailKey, ts: now };

    showNotice(formattedTitle, 'error', { timeout: 8000 });
    showApiError(formattedTitle, detail);
}

function notifyDashboardServerStopped(): void {
    if (state.offlineNotified) {
        return;
    }
    state.offlineNotified = true;
    state.liveStreamingEnabled = false;
    window.ARENA_DASHBOARD_STOPPED = true;
    emit('dashboard:offline');
    const runDirCandidate =
        typeof window.__ARENA_RUN_DIR__ === 'string' && window.__ARENA_RUN_DIR__.trim().length
            ? window.__ARENA_RUN_DIR__.trim()
            : null;
    const replayCommand = runDirCandidate
        ? `uv run shogiarena dashboard serve --run-dir "${runDirCandidate}"`
        : 'uv run shogiarena dashboard serve --run-dir <run-directory>';
    showNotice(`Live updates stopped. Replay with: ${replayCommand}`, 'info', { timeout: 0 });
}

function setDisplay(target: Element | string, displayValue?: string | null): void {
    const shared = getShared();
    const impl = shared?.dom?.setDisplay;
    if (typeof impl !== 'function') {
        throw new Error('DashboardShared.dom.setDisplay must be loaded before shared/core');
    }
    impl(target, displayValue);
}

function getApiBase(): string {
    const shared = getShared();
    const impl = shared?.api?.getApiBase;
    if (typeof impl !== 'function') {
        throw new Error('DashboardShared.api.getApiBase must be loaded before shared/core');
    }
    return impl();
}

function updateElement(id: string, value: unknown): void {
    const el = document.getElementById(id);
    if (!el) {
        return;
    }
    const next = String(value ?? '');
    if (el.textContent !== next) {
        el.textContent = next;
    }
}

let dashboardCoreSingleton: DashboardCore | null = null;
let lastSpsaDetailNotice: { title: string; detailKey: string; ts: number } | null = null;

function createDashboardCore(): DashboardCore {
    return {
        state,
        events: { on, off, emit },
        mutateState,
        getStateSlice,
        registerFetcher,
        getFetcher,
        warnSoftFailure,
        showApiError,
        showSpsaDetailStreamError,
        showNotice,
        notifyDashboardServerStopped,
        setDisplay,
        getApiBase,
        updateElement,
    };
}

function attachCore(owner: Window & typeof globalThis, core: DashboardCore): void {
    owner.DashboardCore = core;
    owner.DashboardShowNotice = (message: string, variant?: string, options?: JsonObject) => {
        const normalizedVariant: DashboardNoticeVariant | undefined =
            variant === 'info' || variant === 'success' || variant === 'warn' || variant === 'error'
                ? variant
                : undefined;
        showNotice(message, normalizedVariant, options as DashboardNoticeOptions | undefined);
    };
    owner.notifyDashboardServerStopped = notifyDashboardServerStopped;
}

export function installDashboardCore(owner: Window & typeof globalThis = window): DashboardCore {
    if (dashboardCoreSingleton) {
        attachCore(owner, dashboardCoreSingleton);
        return dashboardCoreSingleton;
    }

    const core = createDashboardCore();
    dashboardCoreSingleton = core;
    attachCore(owner, core);
    return core;
}
