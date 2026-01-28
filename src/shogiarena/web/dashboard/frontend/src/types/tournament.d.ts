export * from '@/modules/tournament/types/public';

declare global {
    interface Window {
        ARENA_SUMMARY?: import('@/modules/tournament/types').TournamentSummary;
        DashboardTournament?: import('@/modules/tournament/types').TournamentDashboardAPI;
    }
}
