import type { DashboardGamesApi, DashboardGamesRender, DashboardGamesUtils } from '@/modules/games/types';
import type { DashboardInstancesApi } from '@/modules/instances/types';
import type { TournamentDashboardAPI } from '@/modules/tournament/types';
import type { DashboardNavigationApi } from '@/types/globals';
import { gamesState, type GamesModuleState } from '../state';

export interface GamesWindow extends Window {
    DashboardGames?: DashboardGamesApi & {
        setInstanceFilter?: (instanceId: string | null, options?: { activateTab?: boolean }) => void;
    };
    DashboardGamesUtils?: DashboardGamesUtils;
    DashboardGamesRender?: DashboardGamesRender;
    DashboardNavigation?: DashboardNavigationApi;
    DashboardInstances?: DashboardInstancesApi;
    DashboardTournament?: TournamentDashboardAPI;
}

export interface GamesServiceContext {
    readonly owner: GamesWindow;
    readonly state: GamesModuleState;
    readonly utils: DashboardGamesUtils;
    readonly render: DashboardGamesRender;
}

export function createGamesServiceContext(owner: GamesWindow): GamesServiceContext {
    const utils = owner.DashboardGamesUtils;
    if (!utils) {
        throw new Error('DashboardGamesUtils is unavailable. Install games utils before games module.');
    }

    const render = owner.DashboardGamesRender;
    if (!render) {
        throw new Error('DashboardGamesRender is unavailable. Install games render before games module.');
    }

    return {
        owner,
        state: gamesState,
        utils,
        render,
    } satisfies GamesServiceContext;
}
