import type { DashboardShared, DashboardSharedDom } from '@/types/dashboard';

type OwnerWindow = Window & typeof globalThis;
type DisplayTarget = Element | string | null | undefined;

function requireOwner(owner?: OwnerWindow): OwnerWindow {
    if (owner) {
        return owner;
    }
    const globalOwner: OwnerWindow | undefined = typeof window !== 'undefined' ? (window as OwnerWindow) : undefined;
    if (globalOwner?.document) {
        return globalOwner;
    }
    throw new Error('Dashboard DOM helpers require a Window with an attached document');
}

function resolveElement(target: DisplayTarget, owner: OwnerWindow): HTMLElement | null {
    if (!target) {
        return null;
    }
    if (typeof target === 'string') {
        const element = owner.document.getElementById(target);
        return (element as HTMLElement | null) ?? null;
    }
    return target as HTMLElement | null;
}

function setDisplay(target: DisplayTarget, displayValue?: string | null, owner?: OwnerWindow): void {
    const resolvedOwner = requireOwner(owner);
    const element = resolveElement(target, resolvedOwner);
    if (!element || !element.style) {
        return;
    }
    const nextDisplay = displayValue ?? '';
    element.style.display = nextDisplay;

    if (displayValue === 'none') {
        element.hidden = true;
    } else if (displayValue !== undefined) {
        element.hidden = false;
    }
}

let installedDom: DashboardSharedDom | null = null;

function attachDomToWindow(owner: OwnerWindow): void {
    const shared: DashboardShared = owner.DashboardShared ?? {};
    owner.DashboardShared = shared;
    shared.dom = installedDom ?? undefined;
}

export function installDashboardDom(owner?: OwnerWindow): DashboardSharedDom {
    const resolvedOwner = requireOwner(owner);
    if (installedDom) {
        attachDomToWindow(resolvedOwner);
        return installedDom;
    }

    const dom: DashboardSharedDom = {
        setDisplay: (target, value) => setDisplay(target, value, resolvedOwner),
    };

    installedDom = dom;
    attachDomToWindow(resolvedOwner);
    return dom;
}
