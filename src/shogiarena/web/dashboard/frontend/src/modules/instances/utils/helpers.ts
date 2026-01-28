import type { DashboardNoticeVariant } from '@/types/dashboard';
import type { InstanceGameRecord, InstanceRecord } from '@/modules/instances/types';
import { assertDashboardCoreShowNotice } from '@/modules/shared/utils/dashboardCore';

type ShowNoticeFn = (message: string, type?: DashboardNoticeVariant) => void;

export function showNotice(message: string, type: DashboardNoticeVariant = 'info'): void {
    const notify = assertDashboardCoreShowNotice();
    notify(message, type);
}

export function handleApiError(error: unknown, context?: string, noticeFn: ShowNoticeFn = showNotice): void {
    const err = error as { message?: string; error?: string } | undefined;
    const errorMessage = err?.message || err?.error;

    if (!errorMessage) {
        const baseMessage = context ? `${context}: unknown error occurred` : 'Unknown error occurred';
        noticeFn(baseMessage, 'error');
        return;
    }

    const fullMessage = context ? `${context}: ${errorMessage}` : errorMessage;
    noticeFn(fullMessage, 'error');
}

export function findInstanceById(
    instances: readonly InstanceRecord[] | undefined,
    id: string,
): InstanceRecord | undefined {
    return instances?.find((inst) => inst.id === id);
}

export function truncate(value: unknown, max: number): string {
    if (!value) return '';
    const str = String(value);
    return str.length > max ? `${str.slice(0, max - 3)}...` : str;
}

export function formatInt(value: unknown): string {
    if (typeof value !== 'number' || Number.isNaN(value)) return '-';
    return value.toLocaleString();
}

export interface ActiveGameSummary {
    readonly status: InstanceGameRecord['status'];
    readonly game_id: InstanceGameRecord['game_id'];
    readonly black_engine: InstanceGameRecord['black_engine'];
    readonly white_engine: InstanceGameRecord['white_engine'];
    readonly started_at: InstanceGameRecord['started_at'];
    readonly completed_at: InstanceGameRecord['completed_at'];
    readonly roles: readonly NonNullable<InstanceGameRecord['roles']>[number][];
}

function normalizeTimestamp(value: InstanceGameRecord['started_at']): string | number | null {
    if (typeof value === 'number') {
        return new Date(value * 1000).toISOString();
    }
    if (typeof value === 'string') {
        return value;
    }
    return null;
}

function coerceInstanceStatus(status: InstanceGameRecord['status']): InstanceGameRecord['status'] {
    if (status === 'completed') {
        return 'completed';
    }
    return 'active';
}

export function normalizeActiveGames(activeGames: InstanceRecord['active_games']): ActiveGameSummary[] {
    if (!Array.isArray(activeGames)) return [];

    return activeGames.map((game) => {
        const roles = Array.isArray(game.roles)
            ? game.roles
                  .filter((role): role is NonNullable<InstanceGameRecord['roles']>[number] => Boolean(role))
                  .map((role) => ({
                      role: role.role,
                      engine_name: role.engine_name ?? null,
                      engine_display_name: role.engine_display_name ?? null,
                      pool_key: role.pool_key ?? null,
                      local: role.local ?? false,
                      started_at: normalizeTimestamp(role.started_at ?? null),
                      completed_at: normalizeTimestamp(role.completed_at ?? null),
                      binary_path: role.binary_path ?? null,
                      build_flags: role.build_flags ?? null,
                      engine_artifact_id: role.engine_artifact_id ?? null,
                      extra: role.extra ?? null,
                  }))
            : [];

        return {
            status: coerceInstanceStatus(game.status),
            game_id: game.game_id ?? null,
            black_engine: game.black_engine ?? null,
            white_engine: game.white_engine ?? null,
            started_at: normalizeTimestamp(game.started_at ?? null),
            completed_at: normalizeTimestamp(game.completed_at ?? null),
            roles,
        };
    });
}
