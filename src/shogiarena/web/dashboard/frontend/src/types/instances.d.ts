import type { DashboardInstancesApi } from '@/modules/instances/types';

export type {
    DashboardInstancesApi,
    InstanceActionKind,
    InstanceActionResult,
    InstanceConfig,
    InstanceCpuViewMode,
    InstanceGameRecord,
    InstanceHistory,
    InstanceHistoryPoint,
    InstanceMetrics,
    InstanceRecord,
    InstanceRole,
    InstanceRoleKind,
    InstanceStatus,
    InstanceTimeControlSummary,
    InstancesSnapshot,
    InstancesStats,
    ScheduleCancelResult,
} from '@/modules/instances/types';

declare global {
    interface Window {
        DashboardInstances?: DashboardInstancesApi;
    }
}
