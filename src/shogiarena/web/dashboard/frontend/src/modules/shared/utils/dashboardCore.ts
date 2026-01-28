import type { DashboardCore, DashboardNoticeOptions, DashboardNoticeVariant } from '@/types/dashboard';

type DashboardCoreOwner = Window & { DashboardCore?: DashboardCore };

type DashboardCoreFunctionKey = {
    [K in keyof DashboardCore]: DashboardCore[K] extends (...args: unknown[]) => unknown ? K : never;
}[keyof DashboardCore];

function requireDashboardCore(owner: DashboardCoreOwner = window): DashboardCore {
    const core = owner.DashboardCore;
    if (!core) {
        throw new Error('DashboardCore must be installed before accessing shared dashboard helpers.');
    }
    return core;
}

function requireDashboardCoreFunction<K extends DashboardCoreFunctionKey>(
    key: K,
    owner: DashboardCoreOwner = window,
): DashboardCore[K] {
    const core = requireDashboardCore(owner);
    const candidate = core[key];
    if (typeof candidate !== 'function') {
        throw new Error(`DashboardCore.${String(key)} is unavailable. Ensure shared/core module loads first.`);
    }
    return candidate.bind(core) as DashboardCore[K];
}

export function assertDashboardCoreGetApiBase(owner: DashboardCoreOwner = window): () => string {
    return requireDashboardCoreFunction('getApiBase', owner) as () => string;
}

export function assertDashboardCoreShowNotice(
    owner: DashboardCoreOwner = window,
): (message: string, variant?: DashboardNoticeVariant, options?: DashboardNoticeOptions) => void {
    return requireDashboardCoreFunction('showNotice', owner) as (
        message: string,
        variant?: DashboardNoticeVariant,
        options?: DashboardNoticeOptions,
    ) => void;
}
