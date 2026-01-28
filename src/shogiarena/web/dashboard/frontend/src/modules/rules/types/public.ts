export interface DashboardRulesApi {
    setActive: (active: boolean) => void;
    refresh: () => void;
    teardown?: () => void;
}
