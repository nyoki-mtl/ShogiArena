import type {
    NormalizedTournamentEngineMeta,
    NormalizedTournamentGame,
    NormalizedTournamentSummary,
    TournamentState,
} from './internal';
import type { TournamentBTDSummary, TournamentSprtSummary } from './shared';
import type { DashboardTabId } from '@/types/globals';
import type { JsonObject } from '@/types/shared';
import type { HydrationTrigger } from '@/modules/shared/utils/hydration';
import type { EngineMetaDetail } from '@/modules/engines/types/internal';

export interface TournamentEngineStats {
    wins?: number;
    draws?: number;
    losses?: number;
    games?: number;
    rating?: number;
    score?: number;
}

export interface TournamentAdjudicationRules {
    maxMovesToDraw?: number;
    [key: string]: unknown;
}

export interface TournamentRulesConfig {
    maxMovesToDraw?: number;
    adjudication?: TournamentAdjudicationRules;
    [key: string]: unknown;
}

export interface TournamentConfig {
    rules?: TournamentRulesConfig;
    [key: string]: unknown;
}

export type { TournamentBTDRatingEntry, TournamentBTDSummary, TournamentSprtSummary } from './shared';

export interface TournamentEngineMeta {
    name?: string;
    engine_path?: string;
    merged_options?: JsonObject;
    resolved_options?: JsonObject;
    extra_options?: JsonObject;
    option_sources?: Record<string, string>;
    option_sources_details?: Record<string, string>;
    runtime_usi_options?: Record<
        string,
        {
            current?: unknown;
            default?: unknown;
        }
    >;
    runtime_engine_info?: {
        name?: string;
        author?: string;
    };
}

export interface TournamentSummary {
    ratingInitial?: number;
    engines?: string[];
    enginesMeta?: TournamentEngineMeta[];
    engineStats?: Record<string, TournamentEngineStats>;
    engineTimeControls?: Record<string, string>;
    defaultTimeControl?: string;
    maxMovesToDraw?: number;
    rules?: TournamentRulesConfig;
    adjudication?: TournamentAdjudicationRules;
    tournamentConfig?: TournamentConfig;
    btd?: TournamentBTDSummary;
    sprt?: TournamentSprtSummary | null;
    games?: {
        completed?: number;
        total?: number;
        cancelled?: number;
    };
    runDir?: string;
    pairResults?: Record<string, JsonObject>;
    sprtConclusion?: string;
    tournamentType?: string;
    tournamentFinished?: boolean;
    [key: string]: unknown;
}

export interface ParsedTimeControlSpec {
    mode: 'time' | 'fixed' | 'search';
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

export interface TournamentDashboardAPI {
    state: TournamentState;
    abbreviateName?: (value: unknown, maxLen?: number) => string;
    escapeHtml(value: unknown): string;
    formatOptionValue(value: unknown): string;
    formatDuration(ms: unknown): string;
    formatNodesCountShort?: (value: unknown) => string;
    formatNodesCountDetail(value: unknown): string;
    formatTimeControlShort(spec: string): string;
    parseTimeControlSpec(spec: string): ParsedTimeControlSpec;
    hashString?: (value: string) => number;
    getApiBase(): string;
    getFinalRatingsFromSummary(): Map<string, number>;
    normalCdf?: (value: number) => number;
    formatWinRatio?: (value: number | null | undefined, digits?: number) => string;
    formatRecordTriplet?: (stats: TournamentEngineStats | null | undefined) => string;
    gaussianDensity?: (x: number, mean: number, stdev: number) => number;
    renderMatchupInline?: (
        engineName: string,
        host: Element | null,
        options?: { preferredOpponent?: string },
    ) => Promise<void> | void;
    hydrateMatchupPanel?: (
        panelRoot: Element | null,
        engineName: string,
        options?: { preferredOpponent?: string },
    ) => void;
    refreshMatchupInline?: () => void;
    fetchGamesList?: (trigger?: HydrationTrigger) => Promise<NormalizedTournamentGame[]> | NormalizedTournamentGame[];
    refreshGamesList?: () => void;
    DEFAULT_MAX_MOVES_TO_DRAW?: number;
    showApiError?: (title: string, detail: unknown) => void;
    updateSummaryStats?: () => void;
    resolveMaxMovesToDraw?: () => number;
    getNormalizedSummary?: () => NormalizedTournamentSummary | null;
    onSummaryUpdate?: (payload: unknown) => void;
    setupLiveUpdates?: () => void;
    initializeTournament?: () => Promise<void> | void;
    updateStandings?: () => void;
    renderOpeningStats?: (options: {
        force?: boolean;
        completedGames?: number | null;
        trigger?: HydrationTrigger;
    }) => Promise<void> | void;
    renderOpeningStatsTable?: () => void;
    showOpeningEngineStats?: (row: unknown) => void;
    showOpeningPlyStats?: (row: unknown) => void;
    showOpeningPreview?: (row: unknown) => void;
    setOpeningsActive?: (active: boolean) => void;
    setActive?: (active: boolean) => void;
    notifyTabChange?: (tab: DashboardTabId) => void;
    refresh?: () => void;
    clearOpeningSelection?: () => void;
    removeOpeningDetailRow?: () => void;
    focusEngineMatchups?: (
        engineName: string,
        options?: { scroll?: boolean; detail?: 'matchup' | 'options'; preferredOpponent?: string },
    ) => void;
    focusMatchupOpponent?: (engineName: string, host: Element | null, opponent: string | null) => Promise<void> | void;
    focusOpeningBySfen?: (sfen: string) => void;
    loadFullOptions?: (engineName: string) => Promise<unknown> | unknown;
    renderFullOptionsTable?: (
        element: Element,
        payload: unknown,
        meta: NormalizedTournamentEngineMeta | EngineMetaDetail | undefined,
    ) => void;
    formatOpeningLabel?: (value: unknown) => string;
}
