export * from '@/modules/tournament/types/public';

declare global {
    interface Window {
        DashboardTournament?: import('@/modules/tournament/types').TournamentDashboardAPI;
    }
}
