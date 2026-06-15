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

export interface BookModuleState {
    active: boolean;
    timerId: number | null;
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
