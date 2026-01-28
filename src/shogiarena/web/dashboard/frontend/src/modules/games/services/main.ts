import type { DashboardGamesApi, LiveViewOptions } from '@/modules/games/types';
import { createAssignmentController } from './assignments';
import type { GamesServiceContext, GamesWindow } from './context';
import { createGamesServiceContext } from './context';
import { createDomBindings } from './domBindings';
import { createGameDialogController } from './gameDialog';
import { createScheduleController } from './schedule';

const defaultWindow = window as GamesWindow;

let installedGamesApi: DashboardGamesApi | null = null;
let context: GamesServiceContext | null = null;
let scheduleController = null as ReturnType<typeof createScheduleController> | null;
let assignmentController = null as ReturnType<typeof createAssignmentController> | null;
let dialogController = null as ReturnType<typeof createGameDialogController> | null;
let domBindingsController = null as ReturnType<typeof createDomBindings> | null;

function ensureControllers(owner: GamesWindow): void {
    if (context && scheduleController && assignmentController && dialogController && domBindingsController) {
        return;
    }

    context = createGamesServiceContext(owner);
    scheduleController = createScheduleController({ context });
    assignmentController = createAssignmentController({ context, schedule: scheduleController });
    dialogController = createGameDialogController({ context, schedule: scheduleController });
    domBindingsController = createDomBindings({
        context,
        schedule: scheduleController,
        assignment: assignmentController,
        dialog: dialogController,
    });
}

export function installGamesModule(owner: GamesWindow = defaultWindow): DashboardGamesApi {
    ensureControllers(owner);

    if (!context || !scheduleController || !assignmentController || !dialogController || !domBindingsController) {
        throw new Error('Failed to initialize games module controllers');
    }

    if (installedGamesApi) {
        owner.DashboardGames = installedGamesApi;
        const docExisting = owner.document;
        if (docExisting.readyState === 'loading') {
            docExisting.addEventListener(
                'DOMContentLoaded',
                () => {
                    domBindingsController?.initialize(installedGamesApi as DashboardGamesApi);
                },
                { once: true },
            );
        } else {
            domBindingsController.initialize(installedGamesApi);
        }
        return installedGamesApi;
    }

    const { state } = scheduleController;

    const dashboardGamesApi: DashboardGamesApi & {
        showGame: (gameId: string) => void;
        clearInstanceFilter: () => void;
    } = {
        refresh: () => {
            void scheduleController.fetchGames(true);
        },
        setActive(active: boolean) {
            state.active = !!active;
            if (state.active) {
                if (state.lastSnapshot) {
                    scheduleController.renderGamesTable(state.lastSnapshot);
                } else {
                    void scheduleController.fetchGames(true);
                }
                scheduleController.startRealtime();
            } else {
                scheduleController.stopRealtime();
            }
        },
        showGame(gameId: string) {
            dialogController.openGameDialog(gameId);
        },
        setInstanceFilter(instanceId: string | null, options?: { activateTab?: boolean }) {
            scheduleController.applyInstanceFilter(instanceId, { activateTab: true, ...(options ?? {}) });
        },
        clearInstanceFilter() {
            scheduleController.clearInstanceFilter({ activateTab: false });
        },
        navigateToLiveView(gameId: string, options?: LiveViewOptions) {
            if (options) {
                scheduleController.openGameInLiveView(gameId, options);
            } else {
                scheduleController.openGameInLiveView(gameId);
            }
        },
    };

    scheduleController.setApi(dashboardGamesApi);
    installedGamesApi = dashboardGamesApi;
    owner.DashboardGames = dashboardGamesApi;

    const doc = owner.document;
    if (doc.readyState === 'loading') {
        doc.addEventListener(
            'DOMContentLoaded',
            () => {
                domBindingsController.initialize(dashboardGamesApi);
            },
            { once: true },
        );
    } else {
        domBindingsController.initialize(dashboardGamesApi);
    }

    return dashboardGamesApi;
}
