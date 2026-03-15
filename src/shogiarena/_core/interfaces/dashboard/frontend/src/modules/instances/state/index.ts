import type { InstanceFormElements } from '@/modules/instances/types';
import type { InstanceCpuViewMode, InstanceHistory, InstancesSnapshot } from '@/modules/instances/types';

export interface LatencyCacheEntry {
    avg: number | null;
    recent: number | null;
    samples: number;
    lastUpdated: number;
    seen: boolean;
}

export interface InstancesDashboardState {
    loading: boolean;
    data: InstancesSnapshot | null;
    history: Record<string, InstanceHistory>;
    latencyCache: Record<string, LatencyCacheEntry>;
    metricsOpen: Set<string>;
    infoOpen: Set<string>;
    cpuViewMode: Record<string, InstanceCpuViewMode>;
    pollTimer: number | null;
    active: boolean;
    healthCheckTs: Record<string, number>;
    lastSnapshotAt: number | null;
    formDialog: HTMLDialogElement | null;
    formMode: 'create' | 'edit';
    editingId: string | null;
    formElements: InstanceFormElements | null;
    drainDialog: HTMLDialogElement | null;
    drainInstanceId: string | null;
    highlightQueue: Set<string>;
    activeHighlights: Set<string>;
    highlightTimers: Map<string, ReturnType<typeof setTimeout>>;
    sparklineDirtyIds: Set<string>;
    visibleInstanceIds: Set<string>;
}

function createInitialState(): InstancesDashboardState {
    return {
        loading: false,
        data: null,
        history: {},
        latencyCache: {},
        metricsOpen: new Set(),
        infoOpen: new Set(),
        cpuViewMode: {},
        pollTimer: null,
        active: false,
        healthCheckTs: {},
        lastSnapshotAt: null,
        formDialog: null,
        formMode: 'create',
        editingId: null,
        formElements: null,
        drainDialog: null,
        drainInstanceId: null,
        highlightQueue: new Set(),
        activeHighlights: new Set(),
        highlightTimers: new Map(),
        sparklineDirtyIds: new Set(),
        visibleInstanceIds: new Set(),
    };
}

export const instancesState = createInitialState();
