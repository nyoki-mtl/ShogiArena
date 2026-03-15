export type {
    LiveBoardAdapter,
    LiveCardId,
    LiveCardState,
    LiveCardsApi,
    LiveClockState,
    LiveDashboardNamespace,
    LiveGameRecord,
    LiveSearchStats,
    LiveSummaryApi,
    LiveTimeApi,
    LiveTimeControlInput,
    LiveTimeControlMode,
    LiveTimeControlSpec,
    LiveTimeMsInput,
    LiveUpdatesApi,
    SseSummaryPayload,
    SseWorkerUpdatePayload,
    WorkerRuntimeState,
    WorkerSnapshot,
    WorkerSnapshotSummary,
    WorkerSnapshotUpdate,
    WorkerUpdatePayload,
} from '@/modules/live/types';

declare global {
    interface Window {
        DashboardLive?: import('@/modules/live/types').LiveDashboardNamespace;
        DashboardLiveCards?: import('@/modules/live/types').LiveCardsApi;
        DashboardLiveTime?: import('@/modules/live/types').LiveTimeApi;
        DashboardLiveSummary?: import('@/modules/live/types').LiveSummaryApi;
        DashboardLiveUpdates?: import('@/modules/live/types').LiveUpdatesApi;
        DashboardLiveDiagnostics?: import('@/modules/live/utils/liveNamespace').LiveNamespaceDiagnostics;
    }
}
