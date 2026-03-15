export type {
    DashboardGamesApi,
    DashboardGamesRender,
    DashboardGamesUtils,
    GameOutcomeInfo,
    GameOutcomeKind,
    GamesScheduleEntry,
    GamesScheduleSnapshot,
    GamesSortDirection,
    GamesSortKey,
    LiveViewOptions,
    NormalizedGameRow,
} from '@/modules/games/types';

declare global {
    interface Window {
        DashboardGames?: DashboardGamesApi;
        DashboardGamesRender?: DashboardGamesRender;
        DashboardGamesUtils?: DashboardGamesUtils;
    }
}
