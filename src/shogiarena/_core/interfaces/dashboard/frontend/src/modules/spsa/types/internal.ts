// SPSA module internal types

import type {
    SpsaParameterEntry,
    SpsaParamsResponse,
    SpsaSummaryResponse,
    SpsaUpdateDetailResponse,
    SpsaUpdateEntry,
    SpsaUpdateProgress,
    SpsaRefreshOptions,
} from './shared';
import type { JsonObject } from '@/types/shared';
import type { SpsaDetailInclude, SpsaDetailResolvedWindow, SpsaDetailViewMode } from '@/modules/spsa/constants';

export const INITIAL_UPDATE_IDX = -1 as const;

export interface NormalizedSpsaParameter {
    raw: SpsaParameterEntry;
    name: string;
    type: string | null;
    value: number | null;
    min: number | null;
    max: number | null;
    step: number | null;
    delta: number | null;
    comment: string | null;
    notUsed: boolean;
}

export interface NormalizedSpsaParams {
    raw: SpsaParamsResponse;
    variantId: string | null;
    numParams: number;
    numUsed: number;
    numClamped: number;
    clampedRatio: number | null;
    params: NormalizedSpsaParameter[];
    initialParams: Record<string, number>;
    diffs: Record<string, number>;
    initialVariantId: string | null;
}

export interface SpsaPhaseWdlEntry {
    wins: number;
    losses: number;
    draws: number;
}

export type SpsaPhaseWdlMap = Readonly<Record<string, SpsaPhaseWdlEntry>>;

export interface NormalizedSpsaPerturbations {
    plus: Record<string, number>;
    minus: Record<string, number>;
}

export interface NormalizedSpsaGame {
    gameId: string;
    blackPlayer: string | null;
    whitePlayer: string | null;
    gameResult: string | null;
    numMoves: number | null;
    phase: string | null;
    status: string | null;
    assignedInstance: string | null;
    round: number | null;
    startTime: string | null;
    endTime: string | null;
}

export interface NormalizedSpsaLtcRegression {
    status: string | null;
    winrate: number | null;
    elo: number | null;
    tunedWins: number;
    baselineWins: number;
    draws: number;
    totalGames: number;
    totalPairs: number | null;
    pairsPlayed: number | null;
    accepted: boolean | null;
    baselineUpdateIdx: number | null;
    baselineVariantToken: string | null;
    tunedVariantToken: string | null;
    startedAt: number | null;
    completedAt: number | null;
    failReasons: readonly string[];
    sprt: JsonObject | null;
    sprtDecision: string | null;
}

export interface NormalizedSpsaUpdateEntry {
    raw: SpsaUpdateEntry;
    updateIdx: number;
    timestamp: number | null;
    startedAt: number | null;
    endedAt: number | null;
    deltaNorm: number | null;
    deltaStep: number | null;
    step: number | null;
    sPlus: number | null;
    sMinus: number | null;
    gamesCompleted: number | null;
    gradients: Record<string, number>;
    params: Record<string, number>;
    deltas: Record<string, number>;
    payload: JsonObject;
    variantId: string | null;
    perturbations: NormalizedSpsaPerturbations;
    isPending: boolean;
    gainCk: number | null;
    gainAk: number | null;
    wins: number;
    losses: number;
    draws: number;
    btdElo: number | null;
    isInitial: boolean;
    phaseWdl: SpsaPhaseWdlMap;
    hasLtcRegression: boolean;
    ltcRegression: NormalizedSpsaLtcRegression | null;
    ltcRejected: boolean;
    ltcRevertedTo: number | null;
}

export interface NormalizedSpsaUpdateDetail {
    raw: SpsaUpdateDetailResponse;
    updateIdx: number;
    engines: { baseline: string | null; tuned: string | null };
    wdl: { wins: number; losses: number; draws: number };
    variantId: string | null;
    params: Record<string, number>;
    gradients: Record<string, number>;
    deltas: Record<string, number>;
    sPlus: number | null;
    sMinus: number | null;
    step: number | null;
    perturbations: NormalizedSpsaPerturbations;
    gainCk: number | null;
    gainAk: number | null;
    isPending: boolean;
    games: NormalizedSpsaGame[];
    gamesCount: number;
    ltcGames: NormalizedSpsaGame[];
    ltcGamesCount: number;
    payload: JsonObject;
    isInitial: boolean;
    phaseWdl: SpsaPhaseWdlMap;
    ltcRegression: NormalizedSpsaLtcRegression | null;
    hasLtcRegression: boolean;
    view: SpsaDetailViewMode;
    detailWindow: SpsaDetailResolvedWindow;
    loadedIncludes: readonly SpsaDetailInclude[];
    availableIncludes: readonly SpsaDetailInclude[];
}

export interface NormalizedSpsaLtcSummary {
    enabled: boolean;
    status: string;
    config: JsonObject | null;
    lastUpdateIdx: number | null;
    winrate: number | null;
    elo: number | null;
    pairsPlayed: number | null;
    sprt: JsonObject | null;
    sprtDecision: string | null;
    historySize: number;
    baselineVariantToken: string | null;
    tunedVariantToken: string | null;
    bestEstimate: {
        mean: number | null;
        variance: number | null;
        sigma: number | null;
        lower: number | null;
        upper: number | null;
        source: string | null;
        compositionDepth: number | null;
    } | null;
}

export interface NormalizedSpsaSummary {
    raw: SpsaSummaryResponse;
    mode: string | null;
    experimentName: string | null;
    wins: number;
    losses: number;
    draws: number;
    tunedBlackWins: number;
    tunedBlackLosses: number;
    tunedWhiteWins: number;
    tunedWhiteLosses: number;
    btdElo: number | null;
    elo: number | null;
    btdSe: number | null;
    eloSe: number | null;
    btdLos: number | null;
    los: number | null;
    numUpdates: number;
    updatesCompleted: number;
    etaSeconds: number | null;
    drawRate: number | null;
    deltaNormLast: number | null;
    variantId: string | null;
    ltcRegression: NormalizedSpsaLtcSummary | null;
    engineTimeControls: Record<string, string>;
    defaultTimeControl: string | null;
    engines: string[];
    engineInstances: Record<string, string | null | undefined>;
    engineStats: Record<string, { wins?: number; losses?: number; draws?: number; games?: number }>;
    engineMeta: Record<string, JsonObject>;
}

export type SpsaDataStatus = 'idle' | 'loading' | 'ready' | 'error';

export interface SpsaTrendSeries {
    deltaNorm: number[];
    deltaStep: number[];
    gainAk: number[];
    timestamps: number[];
    variantIndices: number[];
}

export interface SpsaConvergenceMetricsSnapshot {
    recentAvgDeltaNorm: number | null;
    recentMeanVectorDeltaNorm: number | null;
    recentAvgMeanVectorDeltaNorm: number | null;
    sampleSize: number | null;
    updatedAt: number | null;
}

export interface SpsaUpdateStore {
    entries: NormalizedSpsaUpdateEntry[];
    total: number;
    limit: number;
    offset: number;
    hasMore: boolean;
    lastUpdateIdx: number;
    cache: Map<number, NormalizedSpsaUpdateEntry>;
    initialEntry: NormalizedSpsaUpdateEntry | null;
    initialDetail: NormalizedSpsaUpdateDetail | null;
    detailCache: Map<number, NormalizedSpsaUpdateDetail>;
    expanded: Set<number>;
    progress: SpsaUpdateProgress | null;
}

export interface SpsaDataNode<T> {
    data: T | null;
    status: SpsaDataStatus;
    error: string | null;
    lastFetched: number | null;
}

export interface SpsaCacheEntry<T> {
    value: T;
    expiresAt: number;
}

export interface SpsaConnectionState {
    eventSource: EventSource | null;
    eventSourceStatus: 'idle' | 'connecting' | 'open' | 'closed';
    abortControllers: Set<AbortController>;
    refreshInFlight: boolean;
    refreshPromise: Promise<void> | null;
    queuedRefresh: SpsaRefreshOptions | null;
}

export interface SpsaVisibilityState {
    pageHidden: boolean;
    refreshPaused: boolean;
}

export interface SpsaDashboardState {
    active: boolean;
    root: HTMLElement | null;
    summary: SpsaDataNode<NormalizedSpsaSummary>;
    summaryCache: SpsaCacheEntry<NormalizedSpsaSummary> | null;
    params: SpsaDataNode<NormalizedSpsaParams>;
    paramsCache: SpsaCacheEntry<NormalizedSpsaParams> | null;
    updates: SpsaUpdateStore;
    trend: SpsaTrendSeries;
    convergenceMetrics: SpsaConvergenceMetricsSnapshot;
    connection: SpsaConnectionState;
    visibility: SpsaVisibilityState;
    errorMessage: string | null;
    lastErrorAt: number | null;
}
