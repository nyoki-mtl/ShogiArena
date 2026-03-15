// Tournament module internal types

import type { TournamentBTDSummary, TournamentEngineMeta, TournamentSprtSummary } from './shared';
import type { JsonObject, MutableJsonObject } from '@/types/shared';
import type { LiveViewSnapshot } from '@/modules/live/types';

export type StandingsSortKey = 'engine' | 'games' | 'wins' | 'draws' | 'losses' | 'winrate' | 'rating';

export type StandingsSortDirection = 'asc' | 'desc' | null;

export interface StandingsSortState {
    key: StandingsSortKey | null;
    direction: StandingsSortDirection;
}

export interface RatingDeltaInfo {
    delta: number;
    until: number;
}

export interface NormalizedTournamentEngineStats {
    wins: number;
    draws: number;
    losses: number;
    games: number;
    rating: number | null;
    score: number | null;
}

export interface NormalizedTournamentPairResult {
    wins: number;
    losses: number;
    draws: number;
}

export interface NormalizedTournamentPlayerInfo {
    name: string | null;
    timeControl: string | null;
}

export interface NormalizedTournamentGameScores {
    black: number | null;
    white: number | null;
}

export interface NormalizedTournamentEngineMeta {
    name: string;
    enginePath: string | null;
    merged_options: MutableJsonObject;
    resolved_options: MutableJsonObject;
    option_sources: Record<string, string>;
    option_sources_details: Record<string, string>;
    runtime_usi_options: Record<string, { current?: unknown; default?: unknown }>;
    runtime_engine_info: MutableJsonObject | null;
    raw: TournamentEngineMeta;
}

export interface NormalizedBTDRatingEntry {
    mean: number | null;
    standardError: number | null;
}

export interface NormalizedBTDSummary {
    anchor: string | null;
    ratings: Record<string, NormalizedBTDRatingEntry>;
    raw: TournamentBTDSummary;
}

export interface NormalizedSprtSummary {
    llr: number | null;
    lower: number | null;
    upper: number | null;
    decision: string | null;
    games: number | null;
    raw: TournamentSprtSummary;
}

export interface NormalizedTournamentGame {
    raw: JsonObject;
    gameId: string | null;
    initialSfen: string;
    players: {
        black: NormalizedTournamentPlayerInfo;
        white: NormalizedTournamentPlayerInfo;
    };
    gameResult: string | null;
    resultCategory: 0 | 1 | 2 | 3 | null;
    winner: 'black' | 'white' | 'draw' | 'error' | 'unknown';
    scores: NormalizedTournamentGameScores;
    totalPlies: number | null;
    startTime: string | null;
    endTime: string | null;
    adjudicationReason: string | null;
}

export interface NormalizedTournamentSummary {
    raw: JsonObject;
    engines: string[];
    engineStats: Record<string, NormalizedTournamentEngineStats>;
    engineTimeControls: Record<string, string>;
    defaultTimeControl: string | null;
    ratingInitial: number;
    completedGames: number;
    totalGames: number;
    originalTotalGames: number;
    cancelledGames: number;
    runDir: string | null;
    tournamentFinished: boolean;
    sprtConclusion: string | null;
    sprt: NormalizedSprtSummary | null;
    tournamentType: string | null;
    pairResults: Record<string, Record<string, NormalizedTournamentPairResult>>;
    engineMeta: Record<string, NormalizedTournamentEngineMeta>;
    engineInstances: Record<string, string | null>;
    btd: NormalizedBTDSummary | null;
}

export interface TournamentExpandedState {
    name: string;
    mode: 'matchup' | 'options';
    opponent?: string;
}

export interface WorkerDomainState {
    cards: unknown[];
    workers: Map<number, unknown>;
    boardAdapters: Map<unknown, unknown>;
    numWorkers: number;
    maxLiveBoards: number;
    nextCardId: number;
    gameDataCache: Map<string, unknown>;
    gamesSignature: string;
    gamesSignatureByCard: Map<string, string>;
    offlineNotified: boolean;
    engineFinalRatings: Map<string, number>;
    highlightEngine: string | null;
    prevRatings: Map<string, number> | null;
    ratingDeltas: Map<string, RatingDeltaInfo> | null;
    ratingDeltaTimerId: ReturnType<typeof setTimeout> | null;
    pendingDeltaHint: Map<string, number> | null;
    pentaCache: Map<string, unknown>;
}

export interface StatsDomainState {
    normalizedSummary: NormalizedTournamentSummary | null;
    gamesList: NormalizedTournamentGame[] | null;
    gamesListCache: NormalizedTournamentGame[] | null;
    matchupCache: Map<string, unknown>;
    openingStatsRendered: boolean;
    lastOpeningStatsCompletedGames: number | null;
    standingsSort: StandingsSortState | null;
    openingStatsSort: { key: string | null; direction: string | null };
    openingStatsData: unknown;
    engineFullOptions: Map<string, unknown>;
    liveViewSnapshot: LiveViewSnapshot | null;
}

export interface LayoutUiState {
    popoverTimer: number | ReturnType<typeof setTimeout> | null;
    popoverPinned: boolean;
    popoverInvokerEl: HTMLElement | null;
    expanded: TournamentExpandedState | null;
    openingBoardAdapter: unknown;
    openingBoardElement: HTMLElement | null;
    openingBoardInfo: unknown;
    openingSelectedRow: unknown;
    openingDetailRow: unknown;
    selectedOpeningKey: string | null;
    openingDetailMode: unknown;
    openingRowLookup: unknown;
    pendingOpeningFocus: unknown;
}

export interface TournamentState extends WorkerDomainState, StatsDomainState, LayoutUiState {}

export interface OpeningPlyHistogramBin {
    bucket: number;
    bucketValue: number;
    count: number;
}
