import type { DashboardCore } from '@/types/dashboard';
import type { DashboardEnginesApi } from './public';
import type {
    TournamentDashboardAPI,
    TournamentEngineMeta,
    TournamentEngineStats,
    TournamentSummary,
} from '@/modules/tournament/types';
import type { NormalizedTournamentEngineMeta } from '@/modules/tournament/types';
import type { JsonObject } from '@/types/shared';
import type { DashboardSpsaPublicApi } from '@/modules/spsa/types';

export type EngineDetailMode = 'options' | 'instance' | 'time';

export interface EngineDetailContext {
    engine: string;
    mode: EngineDetailMode;
    instanceId?: string;
    tcSpec?: string;
}

export interface EngineMetaDetail extends TournamentEngineMeta {
    merged_options?: JsonObject;
    resolved_options?: JsonObject;
    runtime_usi_options?: Record<string, { current?: unknown; default?: unknown }>;
    option_sources?: Record<string, string>;
    option_sources_details?: Record<string, string>;
}

export interface EnginesSummary extends TournamentSummary {
    engines?: string[];
    enginesMeta?: EngineMetaDetail[];
    engineTimeControls?: Record<string, string>;
    engineInstances?: Record<string, string | null | undefined>;
    engineStats?: Record<string, TournamentEngineStats>;
}

export interface EngineSummaryMaps {
    metaMap: Map<string, EngineMetaDetail>;
    tcMap: Map<string, string>;
    instanceMap: Map<string, string | null | undefined>;
    defaultTime: string | null;
}

export interface InstanceConfig {
    host?: string;
    user?: string;
    port?: number;
    project_root?: string;
    slots?: number;
    tags?: string[];
    [key: string]: unknown;
}

export interface InstanceMetrics {
    reachable?: boolean;
    resp_time_ms?: number;
    [key: string]: unknown;
}

export interface EngineInstanceDetail {
    id?: string;
    type?: string;
    available_slots?: number;
    can_accept_job?: boolean;
    config?: InstanceConfig;
    metrics?: InstanceMetrics;
    [key: string]: unknown;
}

export interface TournamentFullOptionsApi extends TournamentDashboardAPI {
    loadFullOptions?: (engineName: string) => PromiseLike<unknown> | unknown;
    renderFullOptionsTable?: (
        element: Element,
        payload: unknown,
        meta: EngineMetaDetail | NormalizedTournamentEngineMeta | undefined,
    ) => void;
}

export interface EnginesWindow extends Window {
    DashboardCore?: DashboardCore;
    DashboardEngines?: DashboardEnginesApi;
    DashboardTournament?: TournamentFullOptionsApi;
    DashboardSpsa?: DashboardSpsaPublicApi;
    ARENA_SUMMARY?: EnginesSummary | JsonObject;
}
