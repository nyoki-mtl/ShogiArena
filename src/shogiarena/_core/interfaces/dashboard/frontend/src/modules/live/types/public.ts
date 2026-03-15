import type { LiveGameNavigationOptions } from '@/types/globals';
import type { LiveCardId, LiveCardState, LiveClockState, WorkerSnapshotSummary } from './internal';
import type { EngineStatusSnapshot } from '@/modules/live/utils/engineStatus';
import type { IndexedPayload, JsonObject } from '@/types/shared';

export type GameResultName =
    | 'BLACK_WIN'
    | 'WHITE_WIN'
    | 'DRAW_BY_REPETITION'
    | 'ERROR'
    | 'BLACK_WIN_BY_DECLARATION'
    | 'WHITE_WIN_BY_DECLARATION'
    | 'DRAW_BY_MAX_PLIES'
    | 'INVALID'
    | 'BLACK_WIN_BY_FORFEIT'
    | 'WHITE_WIN_BY_FORFEIT'
    | 'DRAW_BY_IMPASSE'
    | 'PAUSED'
    | 'BLACK_WIN_BY_ILLEGAL_MOVE'
    | 'WHITE_WIN_BY_ILLEGAL_MOVE'
    | 'BLACK_WIN_BY_TIMEOUT'
    | 'WHITE_WIN_BY_TIMEOUT';

export interface WorkerSnapshot {
    game_id: string | null;
    initial_sfen: string;
    sfen: string | null;
    black_name: string | null;
    white_name: string | null;
    moves: string[];
    ki2_moves: string[];
    eval_black: (number | null)[];
    eval_white: (number | null)[];
    nodes_values: (number | null)[];
    depth_values: (number | null)[];
    seldepth_values: (number | null)[];
    move_times_ms: (number | null)[];
    wall_times_ms: (number | null)[];
    latency_deltas_ms: (number | null)[];
    latency_alerts: (boolean | null)[];
    current_ply: number;
    game_result?: GameResultName | null;
    time_control_black?: string | null;
    time_control_white?: string | null;
    black_player?: string | null;
    white_player?: string | null;
    engine_status?: EngineStatusSnapshot;
    [key: string]: unknown;
}

export type WorkerUpdateType = 'clock_start' | 'clock_increment' | string;

export interface WorkerUpdatePayload {
    type?: WorkerUpdateType;
    game_id?: string | null;
    initial_sfen?: string | null;
    black_name?: string | null;
    white_name?: string | null;
    current_ply?: number;
    move?: string | null;
    ki2_move?: string | null;
    eval?: number | null;
    depth?: number | null;
    seldepth?: number | null;
    nodes?: number | null;
    time_ms?: number | null;
    wall_time_ms?: number | null;
    latency_ms?: number | null;
    latency_alert?: boolean | null;
    sfen?: string | null;
    game_result?: WorkerSnapshot['game_result'];
    moves?: string[];
    ki2_moves?: string[];
    eval_black?: (number | null)[];
    eval_white?: (number | null)[];
    nodes_values?: (number | null)[];
    depth_values?: (number | null)[];
    seldepth_values?: (number | null)[];
    move_times_ms?: (number | null)[];
    black_remain_ms?: number | null;
    white_remain_ms?: number | null;
    byoyomi_ms_black?: number | null;
    byoyomi_ms_white?: number | null;
    started_at_ms?: number | null;
    applied_increment_ms?: number | null;
    pre_black_remain_ms?: number | null;
    pre_white_remain_ms?: number | null;
    occurred_at_ms?: number | null;
    time_control_black?: string | null;
    time_control_white?: string | null;
    side?: 'black' | 'white' | string | null;
    active?: 'black' | 'white' | null;
}

export type WorkerSnapshotUpdate = Partial<WorkerSnapshot> & WorkerUpdatePayload;

export type LiveViewMode = 'tournament' | 'spsa' | 'match' | 'sprt' | 'unknown';
export type LiveViewProgressKind = 'games' | 'updates' | 'match' | 'sprt' | 'unknown';
export type LiveViewProgressState = 'normal' | 'paused' | 'draining' | 'finished';

export interface LiveViewProgressSnapshot extends JsonObject {
    kind: LiveViewProgressKind;
    unitLabel: string;
    completed: number | null;
    total: number | null;
    cancelled?: number | null;
    isFinal?: boolean;
    state?: LiveViewProgressState;
    updatedAt?: string | null;
}

export interface LiveViewSnapshot extends JsonObject {
    version: number | null;
    mode: LiveViewMode;
    progress?: LiveViewProgressSnapshot | null;
}

export type SseWorkerUpdatePayload = IndexedPayload<WorkerSnapshotUpdate>;

export interface SseSummaryPayload extends JsonObject {
    defaultTimeControl?: string | null;
    engineTimeControls?: Record<string, string>;
    gamesCompleted?: number;
    gamesScheduled?: number;
    liveView?: LiveViewSnapshot | null;
    summarySource?: string;
}

export type LiveTimeControlMode = 'time' | 'fixed' | 'search';

export interface LiveTimeControlSpec {
    mode: LiveTimeControlMode;
    initial: number;
    byoyomi: number;
    increment: number;
    fixedMs: number;
    depth: number | null;
    nodes: number | null;
    marginMs: number | null;
    allowTimeout: boolean;
    maxWaitMs: number | null;
}

export interface LiveGameRecord {
    game_id?: string | number | null;
    initial_sfen?: string | null;
    moves?: readonly string[] | null;
    ki2_moves?: readonly string[] | null;
    eval_black?: readonly (number | null)[] | null;
    eval_white?: readonly (number | null)[] | null;
    black_player?: string | null;
    black_name?: string | null;
    white_player?: string | null;
    white_name?: string | null;
    time_control_black?: string | null;
    time_control_white?: string | null;
    time_limit?: string | number | null;
    game_result?: WorkerSnapshot['game_result'];
    nodes_values?: readonly (number | null)[] | null;
    depth_values?: readonly (number | null)[] | null;
    seldepth_values?: readonly (number | null)[] | null;
    move_times_ms?: readonly (number | null)[] | null;
    wall_times_ms?: readonly (number | null)[] | null;
    latency_deltas_ms?: readonly (number | null)[] | null;
    total_plies?: number | null;
    sfen?: string | null;
    [key: string]:
        | string
        | number
        | boolean
        | null
        | undefined
        | readonly string[]
        | readonly number[]
        | readonly (number | null)[]
        | readonly (string | number | null)[];
}

export type LiveTimeMsInput = number | string | null | undefined;
export type LiveTimeControlInput = string | LiveTimeControlSpec | null | undefined;

export interface LiveSearchStats {
    nodes: number | null;
    depth: number | null;
    seldepth: number | null;
    time_ms: number | null;
}

export interface LiveCardsApi {
    initializeWorker?: (snapshot: WorkerSnapshotUpdate, index: number, options?: JsonObject) => WorkerSnapshot;
    initializeCards: () => void;
    findWorkerIndexByGameId?: (gameId: string | null | undefined) => number | null;
    setSelectedCard?: (cardId: LiveCardId | null) => void;
    isInteractiveElement?: (node: Element | null | undefined) => boolean;
    getWorkerSnapshot?: (workerIndex: number) => WorkerSnapshotSummary;
    getCardList?: () => LiveCardState[];
    openAllWorkerCards?: () => void;
    currentWorkerGameId?: (workerIndex: number) => string | null;
    workerLatestLabel?: (workerIndex: number) => string;
    updateWorkerOptionLabels: (workerIdx?: number | null) => void;
    resolveCardView?: (
        cardState: LiveCardState,
        snapshot: WorkerSnapshotSummary,
    ) => { viewPly: number; terminal: boolean; isLiveView: boolean };
    computeAutoViewPly: (data: WorkerSnapshotUpdate, includeTerminal?: boolean) => number;
    syncWorkerViewFromCard: (cardState: LiveCardState) => void;
    saveLayout?: () => void;
    applySavedLayout?: () => void;
    updateStateOrderFromDOM?: () => void;
    enableDragAndDropForCard?: (card: HTMLElement | null) => void;
    normalizeSFEN?: (sfen: string | null | undefined) => string;
    getStartingPlyNumber?: (sfen: string | null | undefined) => number;
    createCard?: (cardState: LiveCardState) => HTMLElement;
    addCard?: () => LiveCardState | null;
    createCardForSource?: (source: string, options?: { autoSync?: boolean }) => LiveCardState | null;
    deleteCard?: (cardId: LiveCardId) => void;
    freezeAllWorkerClocks?: () => void;
    ensureSelectOption?: (select: HTMLSelectElement | null, value: string, label: string) => HTMLOptionElement | null;
    ensureArchivedGameCard?: (
        gameId: string | number,
        options?: { forceNew?: boolean },
    ) => Promise<LiveCardState | null>;
    focusGameOnLiveTab: (gameId: string | number, options?: LiveGameNavigationOptions) => Promise<void>;
    populateSourceDropdown: (cardId: LiveCardId) => Promise<void>;
    changeCardSource?: (cardId: LiveCardId, source: string) => Promise<void>;
    updateCardData?: (cardState: LiveCardState, options?: { forceBootstrap?: boolean }) => Promise<void> | void;
    bootstrapVisibleWorkerCards?: () => Promise<void>;
    goToMoveCard?: (
        cardId: LiveCardId,
        move: number | 'first' | 'prev' | 'next' | 'last',
        options?: JsonObject & { data?: WorkerSnapshot },
    ) => Promise<void> | void;
    toggleKifuCard?: (cardId: LiveCardId) => void;
    toggleKifu?: (workerIdx: number) => void;
    closeKifuPopover?: () => void;
    renderKifuLinesCard?: (cardId: LiveCardId, data: WorkerSnapshot) => string | undefined;
    renderKifuLines?: (workerIdx: number) => string | undefined;
    updateKI2MovesCard?: (cardId: LiveCardId, data: WorkerSnapshot) => void;
    updateKI2Moves?: (workerIdx: number) => void;
    updateMoveSummaryCard?: (cardId: LiveCardId, data: WorkerSnapshot) => void;
    updateEvalChartCard?: (cardId: LiveCardId, data: WorkerSnapshot) => void;
    updateMoveSummary?: (workerIdx: number) => void;
    handleEvalChartClickCard?: (cardId: LiveCardId, event: Event) => Promise<void> | void;
    resolveCardData?: (cardState: LiveCardState) => Promise<WorkerSnapshot | null>;
    appendAddCardTile?: () => void;
    fullGameOptionLabel?: (gameId: string) => string;
    savedGameOptionLabel?: (gameId: string) => string;
    ensureKifuLayers?: () => void;
    positionKifuPopover?: (trigger: Element | null) => void;
    scrollKifuToCurrent?: () => void;
    getMovePrefix?: (ply: number) => string;
    getMovePrefixFromData?: (data: WorkerSnapshot, ply: number) => string;
    formatKifuLineSimple?: (data: WorkerSnapshot, index1: number, options?: { includeNumber?: boolean }) => string;
    formatTerminalLineSimple?: (data: WorkerSnapshot, lastPly: number, options?: { includeNumber?: boolean }) => string;
    formatMoveSummaryWithStats?: (
        labelNum: string,
        moveText: string,
        data: WorkerSnapshot,
        ply: number,
        variant?: 'summary' | 'popover',
    ) => string;
    formatTerminalLineWithStats?: (data: WorkerSnapshot, lastPly: number, variant?: 'summary' | 'popover') => string;
    extractSearchStats?: (data: WorkerSnapshot, ply: number) => LiveSearchStats;
    formatNodes?: (nodes: number | null | undefined) => string;
    formatDepth?: (depth: number | null | undefined, seldepth: number | null | undefined) => string;
    formatMs?: (ms: number | null | undefined) => string;
    getGameData?: (gameId: string | number) => Promise<WorkerSnapshot | null>;
    renderCardList?: () => void;
    refreshCard?: (cardId: LiveCardId) => void;
    goToMove?: (workerIdx: number, move: number | 'first' | 'prev' | 'next' | 'last') => void;
    /**
     * Called when the user switches to the Live tab from another internal dashboard tab.
     * Triggers resume burst and deferred refresh to catch up to the latest state.
     */
    onTabActivate?: () => void;
    teardown?: () => void;
    [key: string]: unknown;
}

export interface LiveTimeApi {
    normalizeSFEN(sfen: string | null | undefined): string;
    getStartingPlyNumber(sfen: string | null | undefined): number;
    formatRemain(ms: LiveTimeMsInput): string;
    formatInc(ms: LiveTimeMsInput): string;
    formatCountUp(ms: LiveTimeMsInput): string;
    formatByoyomi(ms: LiveTimeMsInput): string;
    parseTimeControlSpec(spec: string | null | undefined): LiveTimeControlSpec;
    formatTimeControlShort(spec: LiveTimeControlInput): string;
    computeClocksAtPly(data: WorkerSnapshot, ply: number): LiveClockState;
    updateStaticClocksForCard(cardId: LiveCardId, data: WorkerSnapshot): void;
    extractSearchStats?: (data: WorkerSnapshot, ply: number) => LiveSearchStats;
    formatNodes?: (nodes: number | null | undefined) => string;
    formatDepth?: (depth: number | null | undefined, seldepth: number | null | undefined) => string;
    formatMs?: (ms: number | null | undefined) => string;
}

export interface LiveSummaryApi {
    updateSummaryStats: () => void;
    updateStandings: () => void;
    updatePairwiseMatrix: () => void;
    computePairPentanomial: (engineA: string, engineB: string) => Promise<unknown>;
    handleSummaryEvent: (
        payload:
            | SseSummaryPayload
            | JsonObject
            | { summary?: SseSummaryPayload | JsonObject }
            | CustomEvent<{ summary?: SseSummaryPayload | JsonObject }>
            | null,
    ) => void;
    [key: string]: unknown;
}

export interface LiveUpdatesApi {
    setupLiveUpdates: () => void;
    teardownLiveUpdates?: (reason?: 'user' | 'system') => void;
    isLiveUpdatesActive?: () => boolean;
    onWorkerUpdate: (workerIndex: number, payload: WorkerSnapshotUpdate | null) => void;
    onSummaryUpdate: (payload: SseSummaryPayload | null) => void;
    updateSummaryStats?: () => void;
    safeClone?: <T>(value: T) => T;
    /**
     * Called when the user switches to the Live tab from another internal dashboard tab.
     * Triggers snapshot requests to immediately catch up to the latest state without replaying diffs.
     */
    onTabActivate?: () => void;
}

export interface LiveDashboardNamespace {
    cards?: LiveCardsApi;
    time?: LiveTimeApi;
    summary?: LiveSummaryApi;
    updates?: LiveUpdatesApi;
}
