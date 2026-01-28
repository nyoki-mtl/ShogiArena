import type { LiveUpdatesContext, LiveUpdatesWindow } from '@/modules/live/types/updates';
import { ensureLiveNamespace, requireLiveApi } from '@/modules/live/utils/liveNamespace';
import { assertDashboardCoreGetApiBase } from '@/modules/shared/utils/dashboardCore';

export function createLiveUpdatesContext(owner: LiveUpdatesWindow): LiveUpdatesContext {
    const core = owner.DashboardCore;
    if (!core) {
        throw new Error('DashboardCore must be loaded before live_updates module');
    }

    const namespace = ensureLiveNamespace(owner);
    const live = namespace.live;

    const cards = requireLiveApi(owner, 'cards', 'DashboardLiveCards must be loaded before live_updates module');
    const timeApi = requireLiveApi(owner, 'time', 'DashboardLiveTime must be loaded before live_updates module');
    namespace.set('cards', cards);
    namespace.set('time', timeApi);

    if (typeof cards.updateWorkerOptionLabels !== 'function') {
        throw new Error('DashboardLiveCards.updateWorkerOptionLabels must be a function');
    }
    if (typeof cards.computeAutoViewPly !== 'function') {
        throw new Error('DashboardLiveCards.computeAutoViewPly must be a function');
    }
    if (typeof cards.syncWorkerViewFromCard !== 'function') {
        throw new Error('DashboardLiveCards.syncWorkerViewFromCard must be a function');
    }
    if (typeof cards.populateSourceDropdown !== 'function') {
        throw new Error('DashboardLiveCards.populateSourceDropdown must be a function');
    }

    if (typeof timeApi.normalizeSFEN !== 'function') {
        throw new Error('DashboardLiveTime.normalizeSFEN must be defined before live_updates module');
    }

    const normalizeSFENFn = timeApi.normalizeSFEN.bind(timeApi);

    // Merge Worker 利用フラグ（cards/event 側と共有）
    if (core.state && typeof core.state === 'object') {
        const state = core.state as unknown as Record<string, unknown>;
        if (typeof state.mergeWorkerActive !== 'boolean') {
            state.mergeWorkerActive = false;
        }
    }

    if (typeof core.warnSoftFailure !== 'function') {
        throw new Error('DashboardCore.warnSoftFailure must be defined before live updates module');
    }
    if (typeof core.notifyDashboardServerStopped !== 'function') {
        throw new Error('DashboardCore.notifyDashboardServerStopped must be defined before live updates module');
    }
    return {
        owner,
        core,
        live,
        cards,
        timeApi,
        normalizeSFEN: normalizeSFENFn,
        state: core.state,
        events: core.events,
        warnSoftFailure: core.warnSoftFailure.bind(core),
        notifyDashboardServerStopped: core.notifyDashboardServerStopped.bind(core),
        getApiBase: assertDashboardCoreGetApiBase(owner),
    };
}
