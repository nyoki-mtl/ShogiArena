import type { DashboardCore } from '@/types/dashboard';
import type { LiveTimeApi } from '@/types/live';
import { computeClocksAtPly, createStaticClockUpdater } from './clocks';
import { formatByoyomi, formatCountUp, formatInc, formatRemain, formatTimeControlShort } from './format';
import { parseTimeControlSpec } from './parse';
import { getStartingPlyNumber, normalizeSFEN } from './sfen';
import type { EngineClockSnapshot } from '@/modules/live/types/time';
import { ensureLiveNamespace, registerLiveApi, type LiveNamespaceOwner } from '@/modules/live/utils/liveNamespace';

interface LiveTimeWindow extends LiveNamespaceOwner {
    DashboardCore?: DashboardCore;
}

const defaultWindow = window as LiveTimeWindow;

function createLiveTimeApi(owner: LiveTimeWindow): LiveTimeApi {
    const core = owner.DashboardCore;
    if (!core) {
        throw new Error('DashboardCore must be loaded before live time module');
    }

    const { state } = core;
    const updateStaticClocksForCard = createStaticClockUpdater({ state });

    const timeApi: LiveTimeApi = {
        normalizeSFEN,
        getStartingPlyNumber,
        formatRemain,
        formatInc,
        formatCountUp,
        formatByoyomi,
        parseTimeControlSpec,
        formatTimeControlShort,
        computeClocksAtPly,
        updateStaticClocksForCard,
    };

    return timeApi;
}

export type { EngineClockSnapshot };

export function installLiveTimeModule(owner: LiveTimeWindow = defaultWindow): LiveTimeApi {
    if (!owner.DashboardCore) {
        throw new Error('DashboardCore must be installed before live time module');
    }

    const namespace = ensureLiveNamespace(owner);
    const existing = namespace.get('time');
    if (existing) {
        return existing;
    }

    const api = createLiveTimeApi(owner);
    registerLiveApi(owner, 'time', api, { provider: 'live/services/time' });
    return api;
}
