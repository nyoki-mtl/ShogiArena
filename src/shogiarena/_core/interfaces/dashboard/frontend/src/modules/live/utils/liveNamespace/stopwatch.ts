import {
    getTimestamp,
    type LiveDiagnosticsMetricUpdate,
    type LiveDiagnosticsStopwatch,
    type LiveNamespaceOwner,
} from './core';
import { recordLiveDiagnosticsMetric } from './metrics';

const noopDiagnosticsStopwatch: LiveDiagnosticsStopwatch = {
    succeed: () => {},
    fail: () => {},
    cancel: () => {},
    markRetry: () => {},
};

export function startDiagnosticsStopwatch(
    metric: string,
    owner: LiveNamespaceOwner = window as LiveNamespaceOwner,
): LiveDiagnosticsStopwatch {
    if (!metric) {
        return noopDiagnosticsStopwatch;
    }
    const startedAt = getTimestamp();
    let retries = 0;
    let finished = false;
    recordLiveDiagnosticsMetric(metric, { triggered: 1 }, owner);

    const finalize = (failed: boolean, extra?: LiveDiagnosticsMetricUpdate): void => {
        if (finished) {
            return;
        }
        finished = true;
        const latencyMs = Math.max(0, getTimestamp() - startedAt);
        const payload: LiveDiagnosticsMetricUpdate = { latency_ms: latencyMs };
        if (extra) {
            for (const [key, value] of Object.entries(extra)) {
                if (typeof value === 'number' && Number.isFinite(value)) {
                    payload[key] = value;
                }
            }
        }
        if (typeof payload.retries !== 'number' && retries > 0) {
            payload.retries = retries;
        }
        if (failed) {
            payload.failed = (payload.failed ?? 0) + 1;
        }
        recordLiveDiagnosticsMetric(metric, payload, owner);
    };

    return {
        succeed: (extra) => finalize(false, extra),
        fail: (extra) => finalize(true, extra),
        cancel: () => {
            finished = true;
        },
        markRetry: () => {
            if (!finished) {
                retries += 1;
            }
        },
    };
}
