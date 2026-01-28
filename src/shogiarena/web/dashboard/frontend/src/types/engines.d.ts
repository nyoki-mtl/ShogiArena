export type { DashboardEnginesApi } from '@/modules/engines/types';

declare global {
    interface Window {
        DashboardEngines?: DashboardEnginesApi;
    }
}
