export type { DashboardRulesApi } from '@/modules/rules/types';

declare global {
    interface Window {
        DashboardRules?: DashboardRulesApi;
    }
}
