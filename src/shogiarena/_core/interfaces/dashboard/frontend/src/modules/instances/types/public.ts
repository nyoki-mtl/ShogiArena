import type { JsonObject } from '@/types/shared';

export type InstanceRoleKind = 'black' | 'white';

export interface InstanceRole {
    readonly role: InstanceRoleKind;
    readonly engine_name: string | null;
    readonly engine_display_name?: string | null;
    readonly pool_key?: string | null;
    readonly local?: boolean;
    readonly started_at?: string | null;
    readonly completed_at?: string | null;
    readonly binary_path?: string | null;
    readonly build_flags?: Readonly<JsonObject> | null;
    readonly engine_artifact_id?: string | null;
    readonly extra?: Readonly<JsonObject> | null;
}

export type InstanceStatus = 'active' | 'completed';

export interface InstanceTimeControlSummary {
    readonly black?: string | null;
    readonly white?: string | null;
}

export interface InstanceGameRecord {
    readonly status: InstanceStatus;
    readonly game_id: string | null;
    readonly game_name?: string | null;
    readonly numeric_game_id?: number | null;
    readonly black_engine?: string | null;
    readonly white_engine?: string | null;
    readonly initial_sfen?: string | null;
    readonly round_index?: number | null;
    readonly time_control?: InstanceTimeControlSummary | null;
    readonly time_control_black?: string | null;
    readonly time_control_white?: string | null;
    readonly started_at?: string | number | null;
    readonly completed_at?: string | number | null;
    readonly run_id?: string | null;
    readonly roles?: readonly InstanceRole[];
}

export interface InstanceMetrics {
    readonly cpu_usage_pct?: number;
    readonly cpu_usage_pct_per_core?: readonly number[];
    readonly cpu_model?: string | null;
    readonly cpu_count?: number | null;
    readonly load_avg_1?: number | null;
    readonly load_avg_5?: number | null;
    readonly load_avg_15?: number | null;
    readonly mem_used_pct?: number;
    readonly mem_used_mb?: number;
    readonly mem_free_mb?: number;
    readonly mem_total_mb?: number;
    readonly reachable?: boolean;
    readonly in_use_slots?: number;
    readonly in_use_engines?: number;
    readonly engine_processes?: number;
    readonly timestamp?: number;
    readonly latency_avg_ms?: number | null;
    readonly latency_recent_ms?: number | null;
    readonly latency_samples?: number;
    readonly latency_alert?: boolean;
    readonly latency_threshold_ms?: number | null;
    readonly network_rtt_avg_ms?: number | null;
    readonly network_rtt_recent_ms?: number | null;
    readonly network_rtt_samples?: number;
}

export interface InstanceConfig {
    readonly name?: string;
    readonly host?: string;
    readonly user?: string;
    readonly port?: number;
    readonly slots?: number;
    readonly max_engines?: number | null;
    readonly identity_file?: string;
    readonly project_root?: string;
    readonly engine_dir?: string;
    readonly is_strict_host_key_checking?: boolean;
    readonly tags?: readonly string[];
    readonly should_install_requirements?: boolean;
}

export interface InstanceRecord {
    readonly id: string;
    readonly type?: string;
    readonly config?: InstanceConfig;
    readonly config_path?: string | null;
    readonly metrics?: InstanceMetrics;
    readonly active_games?: readonly InstanceGameRecord[];
    readonly drain?: boolean;
    readonly is_local?: boolean;
    readonly is_ssh?: boolean;
    readonly available_slots?: number;
    readonly can_accept_job?: boolean;
    readonly last_seen?: number;
    readonly slot_capacity?: number;
    readonly engine_limit?: number;
    readonly available_engines?: number;
}

export interface InstancesStats {
    readonly total_instances?: number;
    readonly reachable_instances?: number;
    readonly draining_instances?: number;
    readonly active_instances?: number;
    readonly in_use_slots?: number;
    readonly total_slots?: number;
    readonly available_slots?: number;
    readonly unknown_slots_instances?: number;
    readonly active_games?: number;
}

export interface InstancesSnapshot {
    readonly instances: readonly InstanceRecord[];
    readonly stats?: InstancesStats;
    readonly timestamp?: number;
}

export interface InstanceHistoryPoint {
    readonly ts: number;
    readonly value: number;
}

export interface InstanceHistory {
    readonly cpu: InstanceHistoryPoint[];
    readonly mem: InstanceHistoryPoint[];
    readonly cores: Record<string, InstanceHistoryPoint[]>;
}

export type InstanceCpuViewMode = 'overall' | 'threads';

export type InstanceActionKind = 'health_check' | 'drain' | 'undrain' | 'provision' | string;

export type InstanceProvisionMode = 'dir' | 'file';

export interface InstanceActionResult {
    readonly action?: InstanceActionKind;
    readonly instance_id?: string;
    readonly success?: boolean;
    readonly error?: string;
    readonly timestamp?: number;
    readonly reachable?: boolean;
    readonly metrics?: InstanceMetrics;
    readonly old_drain?: boolean;
    readonly new_drain?: boolean;
    readonly local_path?: string;
    readonly remote_path?: string;
    readonly mode?: InstanceProvisionMode;
}

export interface ScheduleCancelResult {
    readonly status?: 'cancelled' | string;
    readonly cancelled_games?: readonly string[];
    readonly cancelled_count?: number;
    readonly running_games?: readonly string[];
    readonly total_games?: number;
}

export interface DashboardInstancesApi {
    refresh: () => Promise<void> | void;
    setActive: (active: boolean) => void;
    focusInstances: (ids: readonly string[] | string) => void;
    focusInstance: (id: string) => void;
}
