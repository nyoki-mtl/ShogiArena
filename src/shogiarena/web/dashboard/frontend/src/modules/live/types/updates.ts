import type { WorkerRuntimeState } from './internal';
import type { DashboardCore } from '@/types/dashboard';

export type { WorkerRuntimeState };

import type {
    LiveCardsApi,
    LiveDashboardNamespace,
    LiveTimeApi,
    LiveUpdatesApi,
    SseSummaryPayload,
    SseWorkerUpdatePayload,
    WorkerSnapshot,
    WorkerSnapshotUpdate,
    WorkerUpdatePayload,
} from '@/modules/live/types/public';
import type { LiveNamespaceOwner } from '@/modules/live/utils/liveNamespace';

export type WorkerSnapshotRecord = WorkerSnapshot & WorkerUpdatePayload;

export interface LiveUpdatesWindow extends LiveNamespaceOwner {
    DashboardCore?: DashboardCore;
}

export interface ClockSnapshotPayload {
    active?: 'black' | 'white' | null;
    black_remain_ms?: number | null;
    white_remain_ms?: number | null;
    started_at_ms?: number | null;
    time_control_black?: string | null;
    time_control_white?: string | null;
    byoyomi_ms_black?: number | null;
    byoyomi_ms_white?: number | null;
}

export interface ClockIncrementPayload {
    side?: 'black' | 'white' | string | null;
    applied_increment_ms?: number | null;
    pre_black_remain_ms?: number | null;
    pre_white_remain_ms?: number | null;
    black_remain_ms?: number | null;
    white_remain_ms?: number | null;
    occurred_at_ms?: number | null;
}

export interface LiveUpdatesContext {
    owner: LiveUpdatesWindow;
    core: DashboardCore;
    live: LiveDashboardNamespace;
    cards: LiveCardsApi;
    timeApi: LiveTimeApi;
    normalizeSFEN: (sfen: string | null | undefined) => string;
    state: DashboardCore['state'];
    events: DashboardCore['events'];
    warnSoftFailure: DashboardCore['warnSoftFailure'];
    notifyDashboardServerStopped: () => void;
    getApiBase: (path?: string) => string;
}

export type { LiveUpdatesApi, SseSummaryPayload, SseWorkerUpdatePayload, WorkerSnapshot, WorkerSnapshotUpdate };
