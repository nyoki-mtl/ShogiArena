import type { DashboardNoticeOptions } from '@/types/dashboard';
import type { JsonRequestOptions } from '@/types/shared';

export type GameOutcomeKind =
    | 'pending'
    | 'black-win'
    | 'white-win'
    | 'draw'
    | 'cancelled'
    | 'paused'
    | 'error'
    | 'running';

export interface GameOutcomeInfo {
    readonly outcome: GameOutcomeKind;
    readonly blackClass: string | null;
    readonly whiteClass: string | null;
    readonly showRunner?: boolean;
}

export type GamesSortKey = 'order' | 'game' | 'black' | 'white' | 'result' | 'status' | 'moves' | 'started' | 'ended';
export type GamesSortDirection = 'asc' | 'desc';

export type AssignmentMode = 'auto' | 'shared' | 'per_color';

export interface NormalizedGameRow {
    readonly game_id: string | null;
    readonly server_game_id?: string | null;
    readonly status: string;
    readonly round_index: number | null;
    readonly order_index: number | null;
    readonly display_order: number | null;
    readonly black: string;
    readonly white: string;
    readonly black_instance: string | null;
    readonly white_instance: string | null;
    readonly black_instance_kind: string | null;
    readonly white_instance_kind: string | null;
    readonly initial_sfen: string;
    readonly instances: readonly string[];
    readonly assigned_instance: string | null;
    readonly assigned_override: string | null;
    readonly assigned_override_black: string | null;
    readonly assigned_override_white: string | null;
    readonly assigned_mode: AssignmentMode;
    readonly resolved_instance_black: string | null;
    readonly resolved_instance_white: string | null;
    readonly should_require_install: boolean;
    readonly result_abbr: string;
    readonly result_label: string;
    readonly result_detail: string;
    readonly game_result: string | null;
    readonly total_plies: number | null;
    readonly started_at: string | null;
    readonly ended_at: string | null;
}

export interface GamesScheduleEntry {
    readonly game_id?: string | null;
    readonly server_game_id?: string | null;
    readonly status?: string | null;
    readonly order?: number | null;
    readonly round?: number | null;
    readonly black?: string | null;
    readonly white?: string | null;
    readonly black_instance?: string | null;
    readonly white_instance?: string | null;
    readonly black_instance_kind?: string | null;
    readonly white_instance_kind?: string | null;
    readonly initial_sfen?: string | null;
    readonly assigned_instance?: string | null;
    readonly assigned_override?: string | null;
    readonly assigned_override_black?: string | null;
    readonly assigned_override_white?: string | null;
    readonly assigned_mode?: string | null;
    readonly resolved_instance_black?: string | null;
    readonly resolved_instance_white?: string | null;
    readonly should_require_install?: boolean | null;
    readonly result_abbr?: string | null;
    readonly result_label?: string | null;
    readonly result_detail?: string | null;
    readonly game_result?: string | null;
    readonly total_plies?: number | null;
    readonly start_time?: string | null;
    readonly end_time?: string | null;
}

export interface GamesScheduleSnapshot {
    readonly schedule?: readonly GamesScheduleEntry[];
    readonly revision?: number | null;
    readonly base_revision?: number | null;
    readonly total_games?: number;
    readonly completed_games?: number;
    readonly running_games?: number;
    readonly pending_games?: number;
    readonly cancelled_games?: number;
    readonly seed?: string | number | null;
    readonly session_state?: string;
    readonly is_running?: boolean;
}

export interface DashboardGamesUtils {
    readonly STATUS_PRIORITY: readonly string[];
    readonly RESULT_LABELS: Readonly<Record<GameOutcomeKind, string>>;
    readonly apiBase: () => string;
    readonly showNotice: (message: string, variant?: string, options?: DashboardNoticeOptions) => void;
    readonly canOpenLiveView: (status: unknown) => boolean;
    readonly evaluateOutcome: (status: unknown, gameResult: unknown) => GameOutcomeInfo;
    readonly requestJson: <T = unknown>(url: string, options?: JsonRequestOptions) => Promise<T>;
}

export interface DashboardGamesRender {
    readonly buildRow: (normalized: NormalizedGameRow) => HTMLTableRowElement;
    readonly sortRows: (rows: NormalizedGameRow[], sortKey: GamesSortKey, sortDirection: GamesSortDirection) => void;
    readonly updateSortHeaders: (sortKey: GamesSortKey, sortDirection: GamesSortDirection) => void;
}

export interface LiveViewOptions {
    readonly source?: string;
    readonly status?: string;
    readonly preferArchived?: boolean;
}

export interface DashboardGamesApi {
    readonly setActive: (active: boolean) => void;
    readonly setInstanceFilter: (instanceId: string | null, options?: { activateTab?: boolean }) => void;
    readonly refresh: () => void;
    readonly navigateToLiveView?: (gameId: string, options?: LiveViewOptions) => void;
    readonly clearInstanceFilter?: () => void;
    readonly showGame?: (gameId: string) => void;
}
