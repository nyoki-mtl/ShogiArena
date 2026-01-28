export * from '@/modules/spsa/types/public';

declare global {
    interface Window {
        DashboardSpsa?:
            | import('@/modules/spsa/types').DashboardSpsaPublicApi
            | import('@/modules/spsa/types').DashboardSpsaApi;
    }
}
