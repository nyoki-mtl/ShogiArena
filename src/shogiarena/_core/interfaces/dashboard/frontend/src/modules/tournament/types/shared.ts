import type { JsonObject } from '@/types/shared';

export interface TournamentBTDRatingEntry {
    se?: number;
    elo?: number;
}

export interface TournamentBTDSummary {
    ratings?: Record<string, TournamentBTDRatingEntry>;
    anchor?: string;
    rating_cov?: Record<string, Record<string, number>>;
}

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

export interface TournamentSprtSummary {
    llr?: number;
    lower?: number;
    upper?: number;
    decision?: string;
    games?: number;
}
