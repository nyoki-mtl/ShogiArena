import type { DashboardCore, DashboardRuntimeMode, DashboardShared } from './dashboard';
import type { DashboardEnginesApi } from './engines';
import type { DashboardGamesApi, DashboardGamesRender, DashboardGamesUtils } from './games';
import type { DashboardRulesApi } from './rules';
import type { DashboardSpsaApi, DashboardSpsaPublicApi, SpsaTabId } from './spsa';
import type { LiveDashboardNamespace } from './live';
import type { DashboardMatchApi } from '@/modules/match/types';
import type { DashboardSprtApi } from '@/modules/sprt/types';
import type { TournamentDashboardAPI } from './tournament';
import type { ShogiBoardAdapter } from '@/modules/shared/components/shogi-board';
import type { JsonObject } from '@/types/shared';

export type DashboardTabId =
    | 'live'
    | 'match'
    | 'sprt'
    | 'tournament'
    | 'generate'
    | 'openings'
    | 'rules'
    | 'spsa'
    | 'engines'
    | 'instances'
    | 'games';

export interface DashboardTabsApi {
    setActive: (tabId: DashboardTabId) => void;
    setVisibility: (tabId: DashboardTabId, visible: boolean) => void;
    setOrder: (tabIds: readonly DashboardTabId[]) => void;
    getActive: () => DashboardTabId;
}

export interface LiveGameNavigationOptions extends JsonObject {
    forceNewArchived?: boolean;
    source?: string;
}

export interface FocusInstanceOptions {
    activateTab?: boolean;
}

export interface FocusEngineOptions {
    tab?: 'tournament' | 'openings' | 'engines';
    scroll?: boolean;
    detail?: 'options' | 'instance' | 'time' | 'matchup';
    opponent?: string;
}

export interface FocusTournamentOptions {
    detail?: 'matchup' | 'options';
    opponent?: string;
    scroll?: boolean;
}

export interface SpsaNavigationOptions {
    tab?: SpsaTabId;
    updateIdx?: number;
    scrollIntoViewSelector?: string;
}

export interface DashboardNavigationApi {
    openTab: (tabId: DashboardTabId, options?: { scrollIntoViewSelector?: string }) => void;
    openGame: (gameId: string, options?: LiveGameNavigationOptions) => Promise<void>;
    focusInstance: (instanceId: string, options?: FocusInstanceOptions) => void;
    focusInstances: (instanceIds: readonly string[], options?: FocusInstanceOptions) => void;
    showInstanceGames: (instanceId: string, options?: FocusInstanceOptions) => void;
    focusEngine: (engineName: string, options?: FocusEngineOptions) => void;
    focusTournamentEngine: (engineName: string, options?: FocusTournamentOptions) => void;
    openSpsa: (options?: SpsaNavigationOptions) => void;
}

export interface DashboardGenerateApi {
    refresh?: () => void;
    setActive?: (active: boolean) => void;
}

export type ArenaDashboardWindow = Window &
    typeof globalThis & {
        DashboardShared?: DashboardShared;
        DashboardCore?: DashboardCore;
        DashboardLive?: LiveDashboardNamespace;
        DashboardGames?: DashboardGamesApi;
        DashboardGamesUtils?: DashboardGamesUtils;
        DashboardGamesRender?: DashboardGamesRender;
        DashboardEngines?: DashboardEnginesApi;
        DashboardRules?: DashboardRulesApi;
        DashboardSpsa?: DashboardSpsaPublicApi | DashboardSpsaApi;
        DashboardMatch?: DashboardMatchApi;
        DashboardSprt?: DashboardSprtApi;
        DashboardTournament?: TournamentDashboardAPI;
        DashboardTabs?: DashboardTabsApi;
        DashboardNavigation?: DashboardNavigationApi;
        DashboardGenerate?: DashboardGenerateApi;
        ShogiBoardAdapter?: new () => ShogiBoardAdapter;
        DashboardShowNotice?: (message: string, variant?: string, options?: JsonObject) => void;
        notifyDashboardServerStopped?: () => void;
        ARENA_API_PORT?: number;
        DASHBOARD_API_BASE?: string;
        ARENA_DASHBOARD_STOPPED?: boolean;
        __ARENA_NUM_WORKERS__?: number;
        __ARENA_RUN_DIR__?: string;
        ARENA_DISABLE_SPSA?: boolean;
        ARENA_RUNTIME_MODE?: DashboardRuntimeMode;
    };

declare global {
    interface Window {
        DashboardShared?: DashboardShared;
        DashboardCore?: DashboardCore;
        DashboardLive?: LiveDashboardNamespace;
        DashboardGames?: DashboardGamesApi;
        DashboardGamesUtils?: DashboardGamesUtils;
        DashboardGamesRender?: DashboardGamesRender;
        DashboardEngines?: DashboardEnginesApi;
        DashboardRules?: DashboardRulesApi;
        DashboardSpsa?: DashboardSpsaPublicApi | DashboardSpsaApi;
        DashboardMatch?: DashboardMatchApi;
        DashboardSprt?: DashboardSprtApi;
        DashboardTournament?: TournamentDashboardAPI;
        DashboardTabs?: DashboardTabsApi;
        DashboardNavigation?: DashboardNavigationApi;
        DashboardGenerate?: DashboardGenerateApi;
        ShogiBoardAdapter?: new () => ShogiBoardAdapter;
        DashboardShowNotice?: (message: string, variant?: string, options?: JsonObject) => void;
        notifyDashboardServerStopped?: () => void;
        ARENA_API_PORT?: number;
        DASHBOARD_API_BASE?: string;
        ARENA_DASHBOARD_STOPPED?: boolean;
        __ARENA_NUM_WORKERS__?: number;
        __ARENA_RUN_DIR__?: string;
    }
}
