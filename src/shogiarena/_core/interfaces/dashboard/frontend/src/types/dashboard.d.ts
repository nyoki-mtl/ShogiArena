import type { LiveBoardAdapter, LiveCardId, LiveViewSnapshot, WorkerRuntimeState, WorkerSnapshot } from './live';
import type { NormalizedTournamentGame } from '@/modules/tournament/types';
import type { RatingDeltaInfo } from './tournament';

// Derived from the mode catalogue, not written twice. See that module for why.
export type { DashboardRuntimeMode } from '@/modules/shared/services/runtime-mode-catalog';

export type DashboardNoticeVariant = 'info' | 'success' | 'warn' | 'error';

export interface DashboardNoticeOptions {
    readonly timeout?: number;
}

export interface DashboardSharedApi {
    readonly getDefaultApiPort: () => number;
    readonly resolveApiBase: () => string;
    readonly getApiBase: () => string;
}

export interface DashboardSharedDom {
    readonly setDisplay: (target: Element | string, displayValue?: string | null) => void;
}

export interface DashboardSharedNotices {
    readonly showNotice: (message: string, variant?: DashboardNoticeVariant, options?: DashboardNoticeOptions) => void;
}

export interface DashboardShared {
    api?: DashboardSharedApi;
    dom?: DashboardSharedDom;
    notices?: DashboardSharedNotices;
}

export type DashboardEventHandler<T = unknown> = (payload: T) => void;

export type DashboardFetcher<T = unknown> = () => Promise<T> | T;

export interface DashboardCoreState {
    cards: unknown[];
    workers: Map<number, WorkerRuntimeState>;
    boardAdapters: Map<LiveCardId, LiveBoardAdapter>;
    workerSnapshots: Map<number, WorkerSnapshot>;
    numWorkers: number;
    /**
     * Latest `csa_runs` from the CSA summary. CSA profile only.
     *
     * A bridge run that has not been paired yet owns no game, so it publishes no
     * worker snapshot and the status panel would have nothing to read for the
     * stretch that makes up most of a floodgate hour. The summary lists such a
     * run regardless, so the panel falls back to this.
     */
    csaRuns?: unknown[];
    maxLiveBoards: number;
    nextCardId: number;
    gameDataCache: Map<string, unknown>;
    gamesSignature: string;
    gamesSignatureByCard: Map<string, string>;
    gameMetadata: Map<
        string,
        { variantId?: string | null; phase?: string | null; blackPlayer?: string | null; whitePlayer?: string | null }
    >;
    offlineNotified: boolean;
    liveStreamingEnabled: boolean;
    engineFinalRatings: Map<string, number>;
    highlightEngine: string | null;
    prevRatings: Map<string, number>;
    ratingDeltas: Map<string, RatingDeltaInfo>;
    ratingDeltaTimerId: number | ReturnType<typeof setTimeout> | null;
    pendingDeltaHint: Map<string, number>;
    pentaCache: Map<string, unknown>;
    gamesList: NormalizedTournamentGame[] | null;
    popoverTimer: number | ReturnType<typeof setTimeout> | null;
    popoverPinned: boolean;
    popoverInvokerEl: HTMLElement | null;
    expanded: unknown;
    standingsTCMap: Map<string, string>;
    runtimeMode: DashboardRuntimeMode;
    liveViewSnapshot?: LiveViewSnapshot | null;
    spsaMode?: boolean;
    spsaSummary?: unknown;
    spsaParams?: unknown;
    spsaEvents?: unknown;
    spsaTimerId?: number | ReturnType<typeof setTimeout> | null;
    spsaSort?: string;
    selectedCardId?: string | number | null;
    gamesListCache?: NormalizedTournamentGame[] | null;
    openingStatsRendered?: boolean;
    lastOpeningStatsCompletedGames?: number | null;
    standingsSort?: { key: string | null; direction: string | null };
    openingStatsSort?: { key: string | null; direction: string | null };
    openingStatsData?: unknown;
    engineFullOptions?: Map<string, unknown>;
    openingBoardAdapter?: unknown;
    openingBoardElement?: HTMLElement | null;
    openingBoardInfo?: unknown;
    openingSelectedRow?: unknown;
    openingDetailRow?: unknown;
    selectedOpeningKey?: string | null;
    openingDetailMode?: unknown;
    /** Sync tempo setting for LiveView updates: 'off' | 'auto' | 2 | 4 | 8 | 'unlimited' (session-only, not persisted).
     *  'off' = live updates stopped (equivalent to old Toggle OFF) */
    syncTempo?: 'off' | 'auto' | 2 | 4 | 8 | 'unlimited';
}

export type DashboardCoreStateKey = keyof DashboardCoreState;

export type DashboardStateUpdater<K extends DashboardCoreStateKey> =
    | DashboardCoreState[K]
    | ((current: DashboardCoreState[K]) => DashboardCoreState[K]);

export interface DashboardCore {
    readonly state: DashboardCoreState;
    readonly events: {
        readonly on: <T = unknown>(eventName: string, handler: DashboardEventHandler<T>) => () => void;
        readonly off: <T = unknown>(eventName: string, handler: DashboardEventHandler<T>) => void;
        readonly emit: <T = unknown>(eventName: string, payload?: T) => void;
    };
    readonly mutateState: <K extends DashboardCoreStateKey>(
        key: K,
        updater: DashboardStateUpdater<K>,
    ) => DashboardCoreState[K];
    readonly getStateSlice: <K extends DashboardCoreStateKey>(keys: readonly K[]) => Pick<DashboardCoreState, K>;
    readonly registerFetcher: (name: string, fn: DashboardFetcher) => void;
    readonly getFetcher: (name: string) => DashboardFetcher | null;
    readonly warnSoftFailure: (context: string, error: unknown) => never;
    readonly showApiError: (title: string, detail: unknown) => void;
    readonly showSpsaDetailStreamError: (title: string, detail: unknown) => void;
    readonly showNotice: (message: string, variant?: DashboardNoticeVariant, options?: DashboardNoticeOptions) => void;
    readonly notifyDashboardServerStopped: () => void;
    readonly setDisplay: (target: Element | string, displayValue?: string | null) => void;
    readonly getApiBase: () => string;
    readonly updateElement: (id: string, value: unknown) => void;
}
