import type { TournamentDashboardAPI } from '@/modules/tournament/types';

type ArenaWindow = Window & {
    DashboardTournament?: TournamentDashboardAPI;
};

const defaultWindow = window as ArenaWindow;
const bootstrappedWindows: WeakSet<ArenaWindow> = new WeakSet();

export function installTournamentBootstrap(owner: ArenaWindow = defaultWindow): void {
    if (bootstrappedWindows.has(owner)) {
        return;
    }

    const { document } = owner;

    const bootTournamentDashboard = (): void => {
        const dashboard = owner.DashboardTournament;
        if (dashboard && typeof dashboard.initializeTournament === 'function') {
            const result = dashboard.initializeTournament();
            if (result && typeof result.catch === 'function') {
                result.catch((error: unknown) => {
                    if (typeof dashboard.showApiError === 'function') {
                        dashboard.showApiError('Failed to initialise tournament dashboard', error);
                    }
                    queueMicrotask(() => {
                        throw error instanceof Error ? error : new Error(String(error));
                    });
                });
            }
        }
    };

    if (document.readyState === 'loading') {
        document.addEventListener(
            'DOMContentLoaded',
            () => {
                bootTournamentDashboard();
            },
            { once: true },
        );
    } else {
        bootTournamentDashboard();
    }

    bootstrappedWindows.add(owner);
}
