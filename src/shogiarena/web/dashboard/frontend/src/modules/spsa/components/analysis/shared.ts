import type { DashboardSpsaPublicApi } from '@/modules/spsa/types';

type DashboardAwareWindow = Window & { DashboardSpsa?: DashboardSpsaPublicApi };

export function getDashboardSpsaApi(): DashboardSpsaPublicApi | null {
    const owner = window as DashboardAwareWindow;
    const api = owner.DashboardSpsa;
    return api ?? null;
}

export function resolveNumeric(value: unknown): number | null {
    if (typeof value === 'number') {
        return Number.isFinite(value) ? value : null;
    }
    if (typeof value === 'string') {
        const trimmed = value.trim();
        if (!trimmed) {
            return null;
        }
        const parsed = Number(trimmed);
        return Number.isFinite(parsed) ? parsed : null;
    }
    return null;
}
