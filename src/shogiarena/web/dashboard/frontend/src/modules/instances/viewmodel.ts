import type { InstancesDashboardState } from '@/modules/instances/state';

export type CpuViewMode = NonNullable<InstancesDashboardState['cpuViewMode'][string]>;

export function formatLatency(value: number | null | undefined): string {
    if (value === null || value === undefined || Number.isNaN(value)) return '-';
    const ms = Number(value);
    if (!Number.isFinite(ms)) return '-';
    if (Math.abs(ms) >= 1000) {
        return `${(ms / 1000).toFixed(2)} s`;
    }
    return `${Math.round(ms)} ms`;
}

export function resolveCpuViewMode(current: CpuViewMode | undefined, hasPerCore: boolean): CpuViewMode {
    if (hasPerCore) return current === 'threads' ? 'threads' : 'overall';
    return 'overall';
}

export function buildSlotText(inUse: number | null | undefined, total: number | null | undefined): string {
    const used = Number.isFinite(inUse) ? Number(inUse) : 0;
    const cap = Number.isFinite(total) ? Number(total) : null;
    if (cap === null || cap <= 0) {
        return `${used}/?`;
    }
    return `${used}/${cap}`;
}

export function buildCpuSubLine(perCoreCount: number, cpuModel: string): string {
    if (perCoreCount > 0) return `${perCoreCount} threads reported`;
    return cpuModel ? cpuModel : '';
}

export function buildMemBreakdown(pct: number | null, used: string, total: string): string {
    const pctLabel = typeof pct === 'number' && Number.isFinite(pct) ? `${pct.toFixed(1)}%` : '-';
    return `${pctLabel} (${used} MiB / ${total} MiB)`;
}

export function filterTags(tags: readonly unknown[] | undefined, normalizedType: string): string[] {
    return (tags || [])
        .map((tag) => (typeof tag === 'string' ? tag.trim() : ''))
        .filter((tag) => tag && tag.toLowerCase() !== normalizedType);
}

export function normalizeType(type: unknown): string {
    return typeof type === 'string' ? type.trim().toLowerCase() : '';
}

export function buildHostLabel(host?: string | null, user?: string | null): string {
    const hostname = host ? host.trim() : '';
    const username = user ? user.trim() : '';
    if (!hostname) return '';
    return username ? `${username}@${hostname}` : hostname;
}
