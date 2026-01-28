import type {
    DashboardNoticeOptions,
    DashboardNoticeVariant,
    DashboardShared,
    DashboardSharedNotices,
} from '@/types/dashboard';

declare const window: Window & typeof globalThis;

type NoticeContainer = HTMLElement & { remove: () => void };

type NoticeElement = HTMLElement & { remove: () => void };

const NOTICE_VARIANTS: readonly DashboardNoticeVariant[] = ['info', 'success', 'warn', 'error'] as const;

function ensureNoticeArea(): NoticeContainer {
    const doc = window.document;
    const container = doc.querySelector('header') ?? doc.body;
    let area = doc.getElementById('dashboardNoticeArea') as NoticeContainer | null;
    if (!area) {
        area = doc.createElement('div') as NoticeContainer;
        area.id = 'dashboardNoticeArea';
        area.className = 'notice-area';
        container.insertBefore(area, container.firstChild ?? null);
    }
    return area;
}

export function showNotice(
    message: unknown,
    variant: DashboardNoticeVariant = 'info',
    options: DashboardNoticeOptions = {},
): void {
    const area = ensureNoticeArea();
    const doc = window.document;
    const notice = doc.createElement('div') as NoticeElement;
    const type = NOTICE_VARIANTS.includes(variant) ? variant : 'info';
    notice.className = `notice notice-${type}`;
    notice.textContent = String(message ?? '');
    area.appendChild(notice);

    const timeout = typeof options.timeout === 'number' ? options.timeout : 8000;
    if (timeout > 0) {
        window.setTimeout(() => {
            notice.remove();
            if (area.childElementCount === 0) {
                area.remove();
            }
        }, timeout);
    }
}

let installedNotices: DashboardSharedNotices | null = null;

function attachNotices(owner: Window & typeof globalThis): void {
    const shared: DashboardShared = owner.DashboardShared ?? {};
    owner.DashboardShared = shared;
    shared.notices = installedNotices ?? undefined;
}

export function installDashboardNotices(owner: Window & typeof globalThis = window): DashboardSharedNotices {
    if (installedNotices) {
        attachNotices(owner);
        return installedNotices;
    }

    const notices: DashboardSharedNotices = {
        showNotice,
    };

    installedNotices = notices;
    attachNotices(owner);
    return notices;
}
