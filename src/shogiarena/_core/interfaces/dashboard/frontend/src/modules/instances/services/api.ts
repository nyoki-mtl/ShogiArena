import type { InstancesDashboardState } from '@/modules/instances/state';
import type { InstanceActionResult, InstanceProvisionMode, ScheduleCancelResult } from '@/modules/instances/types';
import { assertDashboardCoreGetApiBase } from '@/modules/shared/utils/dashboard-core';
import { requestJson as sharedRequestJson } from '@/modules/shared/services/api';

export const requestJson = sharedRequestJson;

let cachedGetApiBase: (() => string) | null = null;

function ensureApiBaseResolver(): () => string {
    if (cachedGetApiBase) {
        return cachedGetApiBase;
    }
    const resolver = assertDashboardCoreGetApiBase();
    cachedGetApiBase = resolver;
    return resolver;
}

export function apiBase(): string {
    return ensureApiBaseResolver()();
}

type InstanceActionExtra =
    | undefined
    | {
          readonly local_path: string;
          readonly remote_path: string;
          readonly mode?: InstanceProvisionMode;
      };

export async function postInstanceAction<T extends InstanceActionResult = InstanceActionResult>(
    state: InstancesDashboardState,
    id: string,
    action: string,
    extra: InstanceActionExtra = undefined,
): Promise<T> {
    const body = extra ? { action, ...extra } : { action };
    const payload = await requestJson<T>(`${apiBase()}/api/instances/${encodeURIComponent(id)}/actions`, {
        method: 'POST',
        body,
    });
    if (action === 'health_check') {
        state.healthCheckTs[id] = Date.now();
    }
    return payload;
}

export async function cancelPendingGames(): Promise<ScheduleCancelResult> {
    return requestJson<ScheduleCancelResult>(`${apiBase()}/api/schedule/cancel`, {
        method: 'POST',
    });
}
