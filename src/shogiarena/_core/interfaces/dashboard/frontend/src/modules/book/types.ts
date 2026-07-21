import type { DashboardCore } from '@/types/dashboard';

export interface BookWdl {
    games: number;
    wins: number;
    losses: number;
    draws: number;
    win_rate?: number | null;
}

export interface BookOutOfBook {
    samples: number;
    unbounded: number;
    histogram: Record<string, number>;
    mean_ply?: number | null;
    max_ply?: number | null;
    min_ply?: number | null;
}

export interface BookEntry {
    key: string;
    label?: string | null;
    resolved_path?: string | null;
    fingerprint?: Record<string, unknown> | null;
    options?: Record<string, unknown> | null;
    overall: BookWdl;
    by_color: { black?: BookWdl; white?: BookWdl };
    engines: Record<string, BookWdl>;
    out_of_book: BookOutOfBook;
    validation?: string;
}

export interface BookSummaryPayload {
    books: BookEntry[];
    book_count: number;
    note?: string;
}

export type BookPairMeasurementStatus =
    | 'measured'
    | 'partial'
    | 'unmeasured'
    | 'invalid_pair'
    | 'ambiguous'
    | 'unpaired';

export type BookPairSideMeasurementStatus = 'measured' | 'partial' | 'unmeasured';

export type BookPairMeasurementSource = 'db_book_hit' | 'book_lookup' | 'mixed' | 'unmeasured';

export interface BookPairSide {
    game_id: string;
    order: number;
    black: string;
    white: string;
    book_prefix_usi: string[];
    book_prefix_length: number;
    measurement_source: BookPairMeasurementSource;
    measurement_status: BookPairSideMeasurementStatus;
}

export interface BookPairSummary {
    pairs: number;
    measured_pairs: number;
    partial_pairs: number;
    unmeasured_pairs: number;
    invalid_pairs: number;
    ambiguous_pairs: number;
    unpaired_games: number;
    mean_prefix_match_rate?: number | null;
}

export interface BookPairEntry {
    pair_key: string;
    matchup_key: string;
    pair_slot: number;
    orders: number[];
    game_ids: string[];
    same_sfen: boolean;
    initial_sfen?: string | null;
    left: BookPairSide;
    right?: BookPairSide | null;
    measurement_status: BookPairMeasurementStatus;
    matched_prefix_plies: number;
    first_diff_ply?: number | null;
    first_diff_reason: string;
    prefix_match_rate?: number | null;
}

export interface BookPairPayload {
    pairs: BookPairEntry[];
    summary: BookPairSummary;
    note?: string;
}

export type BookModuleView = 'summary' | 'pairs';

export interface BookModuleState {
    active: boolean;
    timerId: number | null;
    view: BookModuleView;
}

export interface DashboardBookApi {
    setActive: (active: boolean) => void;
    refresh: () => void;
    getState?: () => BookModuleState;
}

export interface BookWindow extends Window {
    DashboardCore?: DashboardCore;
    DashboardBook?: DashboardBookApi;
}
