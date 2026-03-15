import type { LiveUpdatesApi } from '@/types/live';
import { createSafeClone } from './common';
import { createLiveUpdatesContext } from './context';
import { createLiveUpdateHandlers } from './handlers';
import { createWsSetup } from './ws';
import { installRecoverableFailureNotices } from './recoverableNotices';
import type { LiveUpdatesWindow } from '@/modules/live/types/updates';
import { ensureLiveNamespace, registerLiveApi } from '@/modules/live/utils/liveNamespace';

const defaultWindow = window as LiveUpdatesWindow;

function createLiveUpdatesApi(owner: LiveUpdatesWindow): LiveUpdatesApi {
    const context = createLiveUpdatesContext(owner);
    installRecoverableFailureNotices(context);
    const safeClone = createSafeClone(context.warnSoftFailure);
    const handlers = createLiveUpdateHandlers(context, safeClone);

    const streamController = createWsSetup(context, handlers);

    const { live } = context;

    const liveApi: LiveUpdatesApi & { safeClone: typeof safeClone } = {
        setupLiveUpdates: () => streamController.start(),
        teardownLiveUpdates: (reason) => streamController.stop(reason ?? 'user'),
        isLiveUpdatesActive: () => streamController.isActive(),
        onWorkerUpdate: handlers.onWorkerUpdate,
        onSummaryUpdate: handlers.onSummaryUpdate,
        safeClone,
        updateSummaryStats: () => {
            live.summary?.updateSummaryStats?.();
        },
        onTabActivate: () => streamController.onTabActivate(),
    };

    return liveApi;
}

export function installLiveUpdatesModule(owner: LiveUpdatesWindow = defaultWindow): LiveUpdatesApi {
    if (!owner.DashboardCore) {
        throw new Error('DashboardCore must be installed before live updates module');
    }

    const namespace = ensureLiveNamespace(owner);
    const existing = namespace.get('updates');
    if (existing) {
        return existing;
    }

    const api = createLiveUpdatesApi(owner);
    registerLiveApi(owner, 'updates', api, { provider: 'live/services/updates' });
    return api;
}
