export interface DashboardEnginesApi {
    setActive: (active: boolean) => void;
    refresh: () => void;
    focusEngine?: (
        engineName: string,
        options?: { detail?: 'options' | 'instance' | 'time'; scroll?: boolean },
    ) => boolean;
}
