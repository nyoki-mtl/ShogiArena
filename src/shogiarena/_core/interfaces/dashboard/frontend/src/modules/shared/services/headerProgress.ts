import { renderHeaderProgress } from '@/modules/shared/components/progress';
import { getLiveViewSnapshotStore } from '@/modules/shared/stores/liveViewSnapshot';
import type { DashboardCore } from '@/types/dashboard';

interface HeaderProgressWindow extends Window {
    DashboardCore?: DashboardCore;
}

const defaultWindow = window as HeaderProgressWindow;
let installed = false;

export function installHeaderProgress(owner: HeaderProgressWindow = defaultWindow): void {
    if (installed) return;
    const core = owner.DashboardCore;
    if (!core) {
        throw new Error('DashboardCore must be installed before header progress');
    }

    const doc = owner.document;
    const store = getLiveViewSnapshotStore(core);

    const renderSnapshot = (snapshot: { progress?: { [key: string]: unknown } | null } | null): void => {
        const progress = snapshot?.progress ?? null;
        if (!progress || typeof progress !== 'object') {
            renderHeaderProgress(doc, {
                completed: 0,
                total: 0,
                isFinal: false,
                state: 'normal',
                unitLabel: 'games',
            });
            return;
        }
        const record = progress as {
            completed?: number | null;
            total?: number | null;
            cancelled?: number | null;
            isFinal?: boolean | null;
            state?: 'normal' | 'paused' | 'draining' | 'finished' | null;
            unitLabel?: string | null;
        };
        renderHeaderProgress(doc, {
            completed: record.completed,
            total: record.total,
            cancelled: record.cancelled ?? undefined,
            isFinal: record.isFinal ?? undefined,
            state: record.state ?? undefined,
            unitLabel: record.unitLabel ?? undefined,
        });
    };

    renderSnapshot(store.getSnapshot());
    store.subscribe((snapshot) => renderSnapshot(snapshot));
    installed = true;
}
