import type { LiveBoardAdapter, LiveCardId, LiveCardState } from '@/modules/live/types';
import type { DashboardCore } from '@/types/dashboard';
import type { DashboardNavigationApi, DashboardTabsApi, LiveGameNavigationOptions } from '@/types/globals';
import type { LiveCardsApi, LiveDashboardNamespace, LiveTimeApi } from '@/types/live';
import type { JsonObject } from '@/types/shared';
import { requestJson } from '@/modules/shared/services/api';
import { createKifuHandlers } from './kifu';
import type { KifuHandlers, WorkerSnapshotRecord } from './types';
import { createLayoutController, type MutableRef } from './layout';
import { createCardElement, renderAddCardTile } from './ui';
import { createCardsBootstrap } from './bootstrap';
import { createCardsController, type CardsControllerApi } from './cardsController';
import {
    computeAutoViewPly,
    resolveCardView,
    syncWorkerViewFromCard as syncWorkerViewFromCardState,
} from './stateSync';
import { createCardsNavigation, type CardsNavigationApi } from './navigation';
import {
    createEmptySnapshot,
    findCardById,
    findCardBySource,
    getBoardAdapter,
    getCardList,
    parseLiveCardId,
    setCardList,
    toNumber,
    ensureWorkerSnapshot,
} from './state';
import { getWorkerSnapshotRecord, peekWorkerSnapshotRecord } from '@/modules/live/state/updates';
import { createBoardAdapterLayer } from './boardLayer';
import { createLiveCardsEventsController } from './eventsController';
import { ensureLiveNamespace, requireLiveApi, type LiveNamespaceOwner } from '@/modules/live/utils/liveNamespace';
import { getLiveViewSnapshotStore } from '@/modules/shared/stores/liveViewSnapshot';
import {
    formatVariantDisplay,
    getGameMetadataEntry,
    getGameMetadataStore,
    setGameMetadataEntry,
    type GameMeta,
} from './metadata';
import { createSavedGamesController } from './savedGames';
import { createWorkerSnapshotsController } from './workerSnapshots';
import { createHeaderController } from './header';
import { createSelectionController } from './selection';
import { createUpdateCardData } from './cardData';

export interface EnsureArchivedOptions {
    forceNew?: boolean;
}

export type FocusLiveTabOptions = LiveGameNavigationOptions;

export type CardsWindow = LiveNamespaceOwner & {
    DashboardCore?: DashboardCore;
    DashboardLive?: LiveDashboardNamespace;
    DashboardLiveCards?: LiveCardsApi;
    DashboardLiveTime?: LiveTimeApi;
    DashboardNavigation?: DashboardNavigationApi;
    DashboardTabs?: DashboardTabsApi;
    DashboardGames?: unknown;
    ShogiBoardAdapter?: new () => LiveBoardAdapter;
    ResizeObserver?: typeof ResizeObserver;
    ARENA_DASHBOARD_STOPPED?: boolean;
};

export function createLiveCardsApi(owner: CardsWindow): LiveCardsApi {
    // Manages the live worker "cards" grid: keeps snapshots in sync, restores layout, and
    // drives per-card controls (kifu view, clocks, sources). This file owns UI wiring rather
    // than tournament statistics logic.

    const core = owner.DashboardCore;
    if (!core) {
        throw new Error('DashboardCore must be loaded before live cards module');
    }

    ensureLiveNamespace(owner);

    const {
        state,
        events,
        warnSoftFailure,
        showApiError,
        notifyDashboardServerStopped: notifyDashboardServerStoppedCore,
        getApiBase,
        updateElement,
        showNotice,
    } = core;

    const liveViewStore = getLiveViewSnapshotStore(core);

    const gameMetadata = getGameMetadataStore(state);

    const timeApi = requireLiveApi(owner, 'time', 'DashboardLiveTime must be loaded before live cards module');

    const {
        normalizeSFEN,
        getStartingPlyNumber,
        formatTimeControlShort,
        updateStaticClocksForCard,
        formatRemain,
        formatInc,
        formatCountUp,
        formatByoyomi,
    } = timeApi;

    const {
        manager: boardAdapterManager,
        clearBoardAdapters,
        disposeBoardAdapter,
    } = createBoardAdapterLayer(owner, state, warnSoftFailure);
    const draggingCardRef: MutableRef<number | null> = { value: null };
    const cardsApi: Partial<LiveCardsApi> & JsonObject = {} as JsonObject;

    const getCards = () => getCardList(state);
    const findCardByIdSafe = (cardId: LiveCardId) => findCardById(state, cardId);
    const findCardBySourceSafe = (source: string) => findCardBySource(state, source);
    const emitCardsChanged = (reason: string) => {
        events?.emit?.('live:cards-changed', { reason });
    };

    const hasWorkerCards = (workerIdx: number) =>
        getCards().some((cardState) => cardState && cardState.source === `worker-latest:${workerIdx}`);

    const selectionController = createSelectionController({ state, warnSoftFailure });
    const { setSelectedCard, isInteractiveElement } = selectionController;

    const workerSnapshotsController = createWorkerSnapshotsController({
        state,
        warnSoftFailure,
        showApiError,
        notifyDashboardServerStopped: notifyDashboardServerStoppedCore,
        getApiBase,
        requestJson,
    });

    const {
        cacheWorkerSnapshot,
        getCachedWorkerSnapshot,
        getWorkerSnapshot,
        findWorkerIndexByGameId,
        currentWorkerGameId,
        workerLatestLabel,
        getStableGameKey,
        fetchAndCacheWorkerSnapshot,
    } = workerSnapshotsController;

    const ENGINE_LOG_MAX_LINES = 1000;

    const emitEngineLogToggle = (gid: string, role: 'black' | 'white', open: boolean): void => {
        events?.emit?.('live:engine-log-toggle', { gid, role, open });
    };

    const emitEngineLogSwitch = (prevGid: string | null, nextGid: string | null, role: 'black' | 'white'): void => {
        events?.emit?.('live:engine-log-switch', { prevGid, nextGid, role });
    };

    type EngineLogPreference = { black?: boolean | null; white?: boolean | null };

    const getEngineLogPreference = (cardState: LiveCardState, role: 'black' | 'white'): boolean | null => {
        const prefs = (cardState.engineLogPreference ?? {}) as EngineLogPreference;
        return prefs[role] ?? null;
    };

    const setEngineLogPreference = (cardState: LiveCardState, role: 'black' | 'white', value: boolean | null): void => {
        const prefs = (cardState.engineLogPreference ?? {}) as EngineLogPreference;
        prefs[role] = value;
        cardState.engineLogPreference = prefs;
    };

    const isPreferenceOn = (value: boolean | null): boolean => value === true;

    const resolveManualOpenRoles = (cardState: LiveCardState): { black: boolean; white: boolean } => ({
        black: isPreferenceOn(getEngineLogPreference(cardState, 'black')),
        white: isPreferenceOn(getEngineLogPreference(cardState, 'white')),
    });

    const setEngineLogPreferenceForCard = (
        cardState: LiveCardState,
        role: 'black' | 'white',
        nextPref: boolean | null,
        options: { emit?: boolean } = {},
    ): void => {
        const prevPref = getEngineLogPreference(cardState, role);
        if (prevPref === nextPref) return;
        const prevOpen = isPreferenceOn(prevPref);
        const nextOpen = isPreferenceOn(nextPref);
        setEngineLogPreference(cardState, role, nextPref);
        if (options.emit && prevOpen !== nextOpen) {
            const gid = cardState.engineLogGameKey ?? resolveEngineLogGameKey(cardState) ?? null;
            if (nextOpen) {
                cardState.engineLogGameKey = gid;
                applyEngineLogSnapshot(cardState.id, role, []);
                if (gid) {
                    emitEngineLogToggle(gid, role, true);
                }
            } else if (gid) {
                emitEngineLogToggle(gid, role, false);
            }
        }
    };

    const setEngineLogVisibility = (
        cardState: LiveCardState,
        next: { black?: boolean | null; white?: boolean | null },
        options: { emit?: boolean } = {},
    ): void => {
        if (Object.hasOwn(next, 'black')) {
            setEngineLogPreferenceForCard(cardState, 'black', next.black ?? null, options);
        }
        if (Object.hasOwn(next, 'white')) {
            setEngineLogPreferenceForCard(cardState, 'white', next.white ?? null, options);
        }
        const { black, white } = resolveManualOpenRoles(cardState);
        if (!black && !white) {
            cardState.engineLogGameKey = null;
        }
        applyEngineLogClasses(cardState);
    };

    const applyEngineLogClasses = (cardState: LiveCardState): void => {
        const cardEl = document.getElementById(`card-${cardState.id}`);
        if (!cardEl) return;
        const { black, white } = resolveManualOpenRoles(cardState);
        const anyOpen = black || white;
        // Keep text selection usable in the log panel by disabling card drag-and-drop while log is open.
        cardEl.setAttribute('draggable', anyOpen ? 'false' : 'true');
        cardEl.classList.toggle('worker-card--log-open', anyOpen);
        cardEl.classList.toggle('worker-card--log-open-black', black);
        cardEl.classList.toggle('worker-card--log-open-white', white);
        cardEl.classList.toggle('worker-card--log-visible-black', black);
        cardEl.classList.toggle('worker-card--log-visible-white', white);
        const logPanel = cardEl.querySelector<HTMLElement>('.worker-card__engine-log');
        if (logPanel) {
            logPanel.setAttribute('aria-hidden', anyOpen ? 'false' : 'true');
        }
    };

    const resolveEngineLogGameKey = (cardState: LiveCardState): string | null => {
        const last = cardState.lastGameId;
        if (last && typeof last === 'string') {
            return last;
        }
        if (cardState.source.startsWith('db-game:')) {
            const gameId = cardState.source.split(':')[1];
            return gameId ? String(gameId).trim() || null : null;
        }
        if (cardState.source.startsWith('worker-latest:')) {
            const workerIdx = Number(cardState.source.split(':')[1]);
            if (!Number.isFinite(workerIdx)) return null;
            return currentWorkerGameId(workerIdx);
        }
        return null;
    };

    const headerController = createHeaderController({
        getCards,
        findCardBySource: findCardBySourceSafe,
        getGameMetadata: (gameId: string) => getGameMetadataEntry(gameMetadata, gameId),
        formatVariantDisplay,
        workerLatestLabel,
    });
    const { resolveSourceDisplay, updateCardHeaderTitle, updateWorkerOptionLabels } = headerController;

    const createEventsController = () =>
        createLiveCardsEventsController({
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
            updateCardData: (cardState: LiveCardState) => updateCardData(cardState),
            handleEngineLogEvent,
        });

    const savedGamesController = createSavedGamesController({
        state,
        events,
        warnSoftFailure,
        showApiError,
        notifyDashboardServerStopped: notifyDashboardServerStoppedCore,
        getApiBase,
        liveViewStore,
        setGameMetadata: (gid, meta) => setGameMetadataEntry(gameMetadata, gid, meta),
        findCardById: (cardId) => findCardById(state, cardId),
        workerLatestLabel,
        formatVariantDisplay,
        updateCardHeaderTitle,
        owner,
    });
    const { populateSourceDropdown, getGameData } = savedGamesController;

    const { saveLayout, applySavedLayout, updateStateOrderFromDOM, enableDragAndDropForCard } = createLayoutController({
        getCards,
        setCards: (cards) => setCardList(state, cards),
        warnSoftFailure,
        parseLiveCardId,
        draggingCardRef,
    });

    let softLimitNoticeShown = false;
    let navigationApi: CardsNavigationApi | null = null;

    const ensureEngineLogSection = (
        cardId: LiveCardState['id'],
        role: 'black' | 'white',
    ): { entriesEl: HTMLElement } | null => {
        const body = document.getElementById(`engine-log-body-${cardId}`);
        if (!body) return null;
        const sectionId = `engine-log-section-${role}-${cardId}`;
        let sectionEl = document.getElementById(sectionId);
        if (!sectionEl) {
            sectionEl = document.createElement('div');
            sectionEl.id = sectionId;
            sectionEl.className = `worker-card__overlay-section worker-card__overlay-section--${role}`;
            sectionEl.style.gridRow = role === 'black' ? '2 / 3' : '1 / 2';

            const labelEl = document.createElement('div');
            labelEl.className = 'worker-card__overlay-label';
            sectionEl.appendChild(labelEl);

            const entriesEl = document.createElement('div');
            entriesEl.className = 'worker-card__overlay-entries';
            entriesEl.id = `engine-log-entries-${role}-${cardId}`;
            sectionEl.appendChild(entriesEl);
            body.appendChild(sectionEl);
        }
        const labelEl = sectionEl.querySelector<HTMLElement>('.worker-card__overlay-label');
        sectionEl.style.gridRow = role === 'black' ? '2 / 3' : '1 / 2';
        if (labelEl) {
            const nameEl = document.getElementById(`${role}-name-${cardId}`);
            const nameText = nameEl?.textContent?.trim() || '';
            labelEl.textContent = nameText ? `${role === 'black' ? 'Black' : 'White'}: ${nameText}` : role;
        }
        const entriesEl = sectionEl.querySelector<HTMLElement>('.worker-card__overlay-entries');
        if (!entriesEl) return null;
        return { entriesEl };
    };

    const setEngineLogPlaceholder = (entriesEl: HTMLElement, text: string): void => {
        entriesEl.innerHTML = '';
        const placeholder = document.createElement('div');
        placeholder.className = 'worker-card__overlay-line worker-card__overlay-line--placeholder';
        placeholder.textContent = text;
        entriesEl.appendChild(placeholder);
    };

    const trimEngineLogEntries = (entriesEl: HTMLElement): void => {
        let lines = entriesEl.querySelectorAll(
            '.worker-card__overlay-line:not(.worker-card__overlay-line--placeholder)',
        );
        while (lines.length > ENGINE_LOG_MAX_LINES) {
            const first = lines[0];
            first?.parentElement?.removeChild(first);
            lines = entriesEl.querySelectorAll(
                '.worker-card__overlay-line:not(.worker-card__overlay-line--placeholder)',
            );
        }
    };

    const appendEngineLogEntries = (entriesEl: HTMLElement, entries: unknown[]): void => {
        if (!entries.length) return;
        const shouldStickToBottom = entriesEl.scrollTop + entriesEl.clientHeight >= entriesEl.scrollHeight - 20;
        const placeholder = entriesEl.querySelector('.worker-card__overlay-line--placeholder');
        if (placeholder) {
            placeholder.remove();
        }
        const fragment = document.createDocumentFragment();
        for (const raw of entries) {
            if (!raw || typeof raw !== 'object') continue;
            const entry = raw as { dir?: unknown; line?: unknown };
            const dir = entry.dir === 'out' ? 'out' : 'in';
            const lineText = typeof entry.line === 'string' && entry.line.trim() ? entry.line : '(no command)';
            const arrow = dir === 'out' ? '>' : '<';
            const lineEl = document.createElement('div');
            lineEl.className = 'worker-card__overlay-line';
            lineEl.textContent = `${arrow} ${lineText}`;
            fragment.appendChild(lineEl);
        }
        entriesEl.appendChild(fragment);
        trimEngineLogEntries(entriesEl);
        if (shouldStickToBottom) {
            entriesEl.scrollTop = entriesEl.scrollHeight;
        }
    };

    const applyEngineLogSnapshot = (cardId: LiveCardState['id'], role: 'black' | 'white', entries: unknown[]): void => {
        const section = ensureEngineLogSection(cardId, role);
        if (!section) return;
        setEngineLogPlaceholder(section.entriesEl, 'ログがまだありません');
        appendEngineLogEntries(section.entriesEl, entries);
    };

    function requireNavigation(): CardsNavigationApi {
        if (!navigationApi) {
            throw new Error('Live cards navigation is not initialized');
        }
        return navigationApi;
    }

    function getMaxLiveBoardLimit(): number | null {
        const limitRaw = Number(state.maxLiveBoards ?? 0);
        if (!Number.isFinite(limitRaw) || limitRaw <= 0) {
            return null;
        }
        return Math.max(1, Math.floor(limitRaw));
    }

    function isSoftLimitReached(additional = 0): boolean {
        const limit = getMaxLiveBoardLimit();
        if (limit == null) return false;
        return getCards().length + additional > limit;
    }

    function notifySoftLimit(): void {
        const limit = getMaxLiveBoardLimit();
        if (limit == null) return;
        if (softLimitNoticeShown) return;
        showNotice?.(`ライブボードの同時表示は最大 ${limit} 枚までです`, 'warn', { timeout: 6000 });
        softLimitNoticeShown = true;
    }

    function resetSoftLimitNotice(): void {
        const limit = getMaxLiveBoardLimit();
        if (limit == null) {
            softLimitNoticeShown = false;
            return;
        }
        if (getCards().length < limit) {
            softLimitNoticeShown = false;
        }
    }

    function updateAddCardTileState(): void {
        const tile = document.getElementById('add-card-tile');
        if (!tile) return;
        const limit = getMaxLiveBoardLimit();
        const atCapacity = limit != null && getCards().length >= limit;
        tile.classList.toggle('add-card-tile--disabled', atCapacity);
        if (atCapacity) {
            tile.setAttribute('aria-disabled', 'true');
        } else {
            tile.removeAttribute('aria-disabled');
        }
    }

    const getGameMetadata = (gameId: string): GameMeta | null => getGameMetadataEntry(gameMetadata, gameId);

    const toggleEngineLog = (cardId: LiveCardId, role: 'black' | 'white'): void => {
        const cardState = findCardByIdSafe(cardId);
        if (!cardState) return;
        const cardEl = document.getElementById(`card-${cardState.id}`);
        const isVisible = cardEl?.classList.contains(`worker-card--log-visible-${role}`) ?? false;
        const nextPref = !isVisible;
        setEngineLogPreferenceForCard(cardState, role, nextPref, { emit: true });
        const { black, white } = resolveManualOpenRoles(cardState);
        if (!black && !white) {
            cardState.engineLogGameKey = null;
        }
        applyEngineLogClasses(cardState);
    };

    const closeEngineLogForCard = (cardState: LiveCardState): void => {
        const { black, white } = resolveManualOpenRoles(cardState);
        const gid = cardState.engineLogGameKey ?? resolveEngineLogGameKey(cardState) ?? null;
        if (gid) {
            if (black) emitEngineLogToggle(gid, 'black', false);
            if (white) emitEngineLogToggle(gid, 'white', false);
        }
        setEngineLogVisibility(cardState, { black: false, white: false }, { emit: false });
        cardState.engineLogGameKey = null;
    };

    const handleEngineLogGameKeyChange = (cardState: LiveCardState, prevKey: string | null, nextKey: string): void => {
        const { black, white } = resolveManualOpenRoles(cardState);
        if (!black && !white) return;
        if (black) {
            applyEngineLogSnapshot(cardState.id, 'black', []);
        }
        if (white) {
            applyEngineLogSnapshot(cardState.id, 'white', []);
        }
        for (const role of ['black', 'white'] as const) {
            if (!(role === 'black' ? black : white)) continue;
            if (prevKey) {
                emitEngineLogSwitch(prevKey, nextKey, role);
            } else {
                emitEngineLogToggle(nextKey, role, true);
            }
        }
    };

    function handleEngineLogEvent(payload: unknown): void {
        if (!payload || typeof payload !== 'object') return;
        const data = payload as {
            gid?: unknown;
            role?: unknown;
            entries?: unknown;
            isSnapshot?: unknown;
        };
        const gid = typeof data.gid === 'string' && data.gid.trim() ? data.gid.trim() : null;
        const role = data.role === 'black' || data.role === 'white' ? data.role : null;
        if (!gid || !role) return;
        const entries = Array.isArray(data.entries) ? data.entries : [];
        const isSnapshot = data.isSnapshot === true;
        for (const cardState of getCards()) {
            if (!cardState) continue;
            const pref = getEngineLogPreference(cardState, role);
            if (!isPreferenceOn(pref)) continue;
            const cardGid = cardState.engineLogGameKey ?? resolveEngineLogGameKey(cardState);
            if (cardGid !== gid) continue;
            if (isSnapshot) {
                applyEngineLogSnapshot(cardState.id, role, entries);
            } else {
                const section = ensureEngineLogSection(cardState.id, role);
                if (!section) continue;
                appendEngineLogEntries(section.entriesEl, entries);
            }
        }
    }

    const syncWorkerViewFromCard = (cardState: LiveCardState) => {
        syncWorkerViewFromCardState(state, cardState);
    };

    const kifuHandlers = createKifuHandlers({
        state,
        owner,
        getWorkerSnapshot: (workerIdx) => ensureWorkerSnapshot(getWorkerSnapshotRecord(state, workerIdx)),
        normalizeSFEN,
        getStartingPlyNumber,
        formatRemain,
        formatInc,
        formatCountUp,
        formatByoyomi,
        updateElement,
        warnSoftFailure,
        findCardById: findCardByIdSafe,
        getCardList: getCards,
        parseLiveCardId,
        computeAutoViewPly,
        syncWorkerViewFromCard,
        goToMoveCard: (cardId, target) => {
            const handler = cardsApi.goToMoveCard as ((id: LiveCardId, t: number) => Promise<void> | void) | undefined;
            return handler ? handler(cardId, target) : undefined;
        },
        resolveCardData,
    });
    const {
        resultCodeToKifJP: _unusedResultCodeToKifJP,
        ensureKifuLayers,
        positionKifuPopover,
        scrollKifuToCurrent,
        renderKifuLinesCard,
        renderKifuLines,
        updateKI2MovesCard,
        updateKI2Moves,
        getMovePrefix,
        getMovePrefixFromData,
        formatKifuLineSimple,
        formatTerminalLineSimple,
        updateMoveSummaryCard,
        formatMoveSummaryWithStats,
        formatTerminalLineWithStats,
        extractSearchStats,
        formatNodes,
        formatDepth,
        formatMs,
        updateEvalChartCard,
        updateMoveSummary,
        toggleKifu,
        toggleKifuCard,
        closeKifuPopover,
    } = kifuHandlers satisfies KifuHandlers;

    const updateCardData = createUpdateCardData({
        state,
        normalizeSFEN,
        computeAutoViewPly,
        syncWorkerViewFromCard,
        getStableGameKey,
        fetchAndCacheWorkerSnapshot,
        getCachedWorkerSnapshot,
        getGameData,
        updateElement,
        formatTimeControlShort,
        updateMoveSummaryCard,
        updateEvalChartCard,
        updateStaticClocksForCard,
        getBoardAdapter,
        warnSoftFailure,
        closeKifuPopover,
        onEngineLogGameKeyChange: handleEngineLogGameKeyChange,
        setEngineLogVisibility,
    });

    const eventsController = createEventsController();

    const {
        freezeAllWorkerClocks,
        refreshCardsForWorker,
        triggerTabResume,
        teardown: teardownEvents,
        startWorkerClockTimer,
        updateWorkerClockDisplay,
        onTempoChange,
        stopLiveRendering,
    } = eventsController;

    // Initialize sync tempo select handler.
    // This allows the user to control the update rate for all cards (moves/eval/time/nodes sync).
    // 'off' = live updates stopped (equivalent to old Toggle OFF)
    let syncTempoCleanup: (() => void) | null = null;
    let syncTempoInitialized = false;

    /**
     * Resolve live updates API for controlling stream start/stop.
     */
    const resolveUpdatesApi = (): {
        setupLiveUpdates?: () => void;
        teardownLiveUpdates?: (reason?: string) => void;
    } | null => {
        const direct = owner.DashboardLiveUpdates ?? null;
        if (direct) return direct;
        const namespace = owner.DashboardLive;
        if (namespace?.updates) {
            return namespace.updates;
        }
        return null;
    };

    function initSyncTempoControl(): void {
        // Prevent double initialization if createLiveCardsApi is called multiple times.
        if (syncTempoInitialized) return;

        const select = document.getElementById('syncTempoSelect') as HTMLSelectElement | null;
        if (!select) return;

        syncTempoInitialized = true;

        // Restore initial value from state (session-only, not persisted).
        const currentTempo = (state as { syncTempo?: string }).syncTempo ?? 'auto';
        select.value = currentTempo === 'off' ? 'off' : currentTempo;

        const handleChange = () => {
            const value = select.value;
            type SyncTempoValue = 'off' | 'auto' | 2 | 4 | 8 | 'unlimited';
            const prevTempo: SyncTempoValue = (state as { syncTempo?: SyncTempoValue }).syncTempo ?? 'auto';
            let tempo: 'off' | 'auto' | 2 | 4 | 8 | 'unlimited' = 'auto';
            if (value === 'off') {
                tempo = 'off';
            } else if (value === 'unlimited') {
                tempo = 'unlimited';
            } else if (value === '2') {
                tempo = 2;
            } else if (value === '4') {
                tempo = 4;
            } else if (value === '8') {
                tempo = 8;
            }
            (state as { syncTempo?: 'off' | 'auto' | 2 | 4 | 8 | 'unlimited' }).syncTempo = tempo;

            // Handle OFF state: stop live updates stream, clear pending queues, freeze clocks.
            if (tempo === 'off') {
                // Stop all live rendering immediately: clear queues, cancel timers, stop clocks.
                stopLiveRendering();
                // Teardown live updates stream.
                const updatesApi = resolveUpdatesApi();
                updatesApi?.teardownLiveUpdates?.('user');
                // Update liveStreamingEnabled state for consistency.
                if (core.mutateState) {
                    core.mutateState('liveStreamingEnabled', false);
                } else {
                    state.liveStreamingEnabled = false;
                }
                return;
            }

            // Handle OFF → active transition: restart live updates stream.
            // Note: at this point tempo !== 'off' is guaranteed (early return above).
            if (prevTempo === 'off') {
                const updatesApi = resolveUpdatesApi();
                updatesApi?.setupLiveUpdates?.();
                // Update liveStreamingEnabled state for consistency.
                if (core.mutateState) {
                    core.mutateState('liveStreamingEnabled', true);
                } else {
                    state.liveStreamingEnabled = true;
                }
                // Trigger tab resume to resync with latest state.
                triggerTabResume();
            }

            // Reset fps timer state so new tempo takes effect immediately.
            onTempoChange();

            // Immediately apply clock strategy to visible workers on tempo change.
            // This ensures the user sees the effect without waiting for the next update.
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
            for (const idx of Array.from(visibleWorkers)) {
                // Clock ticking is independent from sync tempo.
                startWorkerClockTimer(idx);
                updateWorkerClockDisplay(idx);
            }
        };

        select.addEventListener('change', handleChange);
        syncTempoCleanup = () => {
            select.removeEventListener('change', handleChange);
        };
    }

    // Defer initialization to allow DOM to be ready.
    setTimeout(initSyncTempoControl, 0);

    function teardown(): void {
        syncTempoCleanup?.();
        syncTempoCleanup = null;
        syncTempoInitialized = false;
        teardownEvents();
        clearBoardAdapters();
        boardAdapterManager.teardownAll();
    }

    // Update card data based on source
    // Change card source
    async function changeCardSource(cardId: LiveCardId, newSource: string): Promise<void> {
        const cardState = findCardByIdSafe(cardId);
        if (!cardState) return;

        cardState.source = newSource;
        cardState.viewPly = 0;
        syncWorkerViewFromCard(cardState);
        updateCardHeaderTitle(cardState);

        // Auto-set autoSync based on source type
        if (newSource.startsWith('worker-latest:')) {
            cardState.autoSync = true;
        } else if (newSource.startsWith('db-game:')) {
            cardState.autoSync = false;
        }

        // AutoSync UI removed; state only

        setSelectedCard(cardState.id);

        // Update card data
        await updateCardData(cardState);
        saveLayout();
        emitCardsChanged('source_change');
    }

    const { bootstrapVisibleWorkerCards, initializeCards, openAllWorkerCards } = createCardsBootstrap({
        getCards,
        setCards: (cards) => setCardList(state, cards),
        getNumWorkers: () => (Number.isFinite(state.numWorkers) ? Number(state.numWorkers) : 0),
        getMaxLiveBoardLimit,
        getSelectedCardId: () => (state.selectedCardId ?? null) as LiveCardId | null,
        isOfflineNotified: () => Boolean(state.offlineNotified),
        allocateNextCardId: () => state.nextCardId++,
        createCardElement: (cardState) => createCard(cardState),
        isSoftLimitReached,
        notifySoftLimit,
        resetSoftLimitNotice,
        updateAddCardTileState,
        appendAddCardTile,
        setSelectedCard,
        refreshCardsForWorker,
        freezeAllWorkerClocks,
        clearBoardAdapters,
        saveLayout,
        updateCardData,
        warnSoftFailure,
    });

    interface GoToMoveOptions {
        data?: WorkerSnapshotRecord;
    }

    async function resolveCardData(cardState: LiveCardState): Promise<WorkerSnapshotRecord | null> {
        return requireNavigation().resolveCardData(cardState);
    }

    async function handleEvalChartClickCard(cardId: LiveCardId, event: MouseEvent): Promise<void> {
        return requireNavigation().handleEvalChartClickCard(cardId, event);
    }

    async function goToMoveCard(
        cardId: LiveCardId,
        target: number | 'first' | 'prev' | 'next' | 'last',
        options: GoToMoveOptions = {},
    ): Promise<void> {
        return requireNavigation().goToMoveCard(cardId, target, options);
    }

    let cardsController: CardsControllerApi;

    function appendAddCardTile(): void {
        const limit = getMaxLiveBoardLimit();
        const atCapacity = limit != null && getCards().length >= limit;
        renderAddCardTile(() => {
            cardsController?.addCard();
        }, atCapacity);
    }

    function createCard(cardState: LiveCardState): HTMLElement {
        return createCardElement(cardState, {
            resolveSourceDisplay,
            enableDragAndDropForCard,
            handleEvalChartClickCard,
            setSelectedCard,
            deleteCard: (cardId) => {
                const cardState = findCardByIdSafe(cardId as LiveCardId);
                if (cardState) {
                    closeEngineLogForCard(cardState);
                }
                cardsController.deleteCard(cardId as LiveCardId);
            },
            changeCardSource,
            goToMoveCard,
            toggleKifuCard,
            toggleEngineLog,
            isInteractiveElement,
            populateSourceDropdown,
            mountBoardAdapter: boardAdapterManager.mount,
        });
    }

    cardsController = createCardsController({
        state,
        getCards,
        notifySoftLimit,
        isSoftLimitReached,
        resetSoftLimitNotice,
        updateAddCardTileState,
        appendAddCardTile,
        saveLayout,
        updateCardData,
        createCard,
        disposeBoardAdapter,
        setSelectedCard,
        onCardsChanged: emitCardsChanged,
    });

    const { addCard, createCardForSource, deleteCard } = cardsController;

    const ensureDefaultCardOnTabActivate = (): void => {
        if (getCards().length > 0) {
            return;
        }
        const workerCount = Number.isFinite(state.numWorkers) ? Number(state.numWorkers) : 0;
        if (workerCount > 0) {
            const created = createCardForSource('worker-latest:0', { autoSync: true });
            if (!created) {
                addCard();
            }
            return;
        }
        addCard();
    };

    navigationApi = createCardsNavigation({
        owner,
        getCards,
        findCardById: findCardByIdSafe,
        findCardBySource: findCardBySourceSafe,
        createCardForSource,
        changeCardSource,
        updateCardData,
        getGameData,
        setSelectedCard,
        getGameMetadata: (gameId: string) => getGameMetadataEntry(gameMetadata, gameId),
        formatVariantDisplay,
        findWorkerIndexByGameId,
        peekWorkerSnapshotRecord: (workerIdx: number) => peekWorkerSnapshotRecord(state, workerIdx),
        getCachedWorkerSnapshot,
        ensureWorkerSnapshot,
        createEmptySnapshot,
        warnSoftFailure,
        getStartingPlyNumber,
        updateElement,
        updateMoveSummaryCard,
        updateKI2MovesCard,
        updateEvalChartCard,
        updateStaticClocksForCard,
        syncWorkerViewFromCard,
        getBoardAdapterForCard: (cardId: LiveCardId) => getBoardAdapter(state, cardId),
    });

    Object.assign(cardsApi, {
        findWorkerIndexByGameId,
        setSelectedCard,
        isInteractiveElement,
        getWorkerSnapshot,
        currentWorkerGameId,
        workerLatestLabel,
        updateWorkerOptionLabels,
        resolveCardView,
        computeAutoViewPly,
        syncWorkerViewFromCard,
        saveLayout,
        applySavedLayout,
        updateStateOrderFromDOM,
        enableDragAndDropForCard,
        normalizeSFEN,
        getStartingPlyNumber,
        initializeCards,
        freezeAllWorkerClocks,
        createCard,
        addCard,
        createCardForSource,
        deleteCard,
        ensureSelectOption: (select: HTMLSelectElement | null, value: string, label: string) =>
            requireNavigation().ensureSelectOption(select, value, label),
        ensureArchivedGameCard: (gameId: string | number, options: EnsureArchivedOptions = {}) =>
            requireNavigation().ensureArchivedGameCard(gameId, options),
        focusGameOnLiveTab: (gameId: string | number, options: FocusLiveTabOptions = {}) =>
            requireNavigation().focusGameOnLiveTab(gameId, options),
        populateSourceDropdown,
        changeCardSource,
        updateCardData,
        goToMoveCard,
        toggleKifuCard,
        toggleKifu,
        closeKifuPopover,
        parseLiveCardId,
        toNumber,
        getCardList: getCards,
        findCardById: findCardByIdSafe,
        findCardBySource: findCardBySourceSafe,
        openAllWorkerCards,
        renderKifuLinesCard,
        renderKifuLines,
        updateKI2MovesCard,
        updateKI2Moves,
        updateMoveSummaryCard,
        updateEvalChartCard,
        updateMoveSummary,
        handleEvalChartClickCard,
        resolveCardData: (cardState: LiveCardState) => requireNavigation().resolveCardData(cardState),
        appendAddCardTile,
        bootstrapVisibleWorkerCards,
        fullGameOptionLabel: (gameId: string) => {
            const meta = getGameMetadata(gameId);
            const variantLabel = formatVariantDisplay(meta?.variantId ?? null, meta?.phase ?? null);
            const blackName = meta?.blackPlayer ?? 'Black';
            const whiteName = meta?.whitePlayer ?? 'White';
            const prefix = variantLabel ?? gameId;
            return `${prefix}: ${blackName} vs ${whiteName}`;
        },
        savedGameOptionLabel: (gameId: string) => {
            const meta = getGameMetadata(gameId);
            const variantLabel = formatVariantDisplay(meta?.variantId ?? null, meta?.phase ?? null);
            return variantLabel ?? gameId;
        },
        ensureKifuLayers,
        positionKifuPopover,
        scrollKifuToCurrent,
        getMovePrefix,
        getMovePrefixFromData,
        formatKifuLineSimple,
        formatTerminalLineSimple,
        formatMoveSummaryWithStats,
        formatTerminalLineWithStats,
        extractSearchStats,
        formatNodes,
        formatDepth,
        formatMs,
        getGameData,
        onTabActivate: () => {
            ensureDefaultCardOnTabActivate();
            triggerTabResume();
        },
        teardown,
    });

    return cardsApi as LiveCardsApi;
}
