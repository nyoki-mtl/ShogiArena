import {
    LIVE_DIAGNOSTICS_WINDOW_MS,
    type LiveNamespaceDiagnostics,
    type LiveNamespaceOwner,
} from '@/modules/live/utils/liveNamespace';

export type AlertSeverity = 'ok' | 'warning' | 'critical';

export interface LiveDiagnosticsWatchExtra {
    key: string;
    label: string;
    unit: string;
    warning: number;
    critical: number;
    should_notify?: boolean;
}

export interface LiveDiagnosticsAutoSnapshot {
    interval_seconds: number;
    mode: 'console' | 'clipboard';
    destination: 'console' | 'clipboard' | 'api';
    retention_minutes: number;
}

export interface LiveDiagnosticsGuidelines {
    hydrator: {
        trigger_per_hour: { warning: number; critical: number };
        failure_rate: { warning: number; critical: number };
    };
    watchlist: {
        extras: readonly LiveDiagnosticsWatchExtra[];
        limit: number;
    };
    auto_snapshot?: LiveDiagnosticsAutoSnapshot;
}

export interface HydratorAlertRow {
    metric: string;
    triggered: number;
    failures: number;
    cacheHit: number;
    failureRate: number;
    status: string;
    severity: AlertSeverity;
    loadPerHour: number;
}

export interface PayloadWatchRow {
    metric: string;
    descriptor: LiveDiagnosticsWatchExtra;
    latest: number;
    average: number;
    max: number;
    min: number;
    values: readonly number[];
    severity: AlertSeverity;
}

const MS_PER_HOUR = 60 * 60 * 1000;
const WINDOW_LABEL_MINUTES = Math.round(LIVE_DIAGNOSTICS_WINDOW_MS / 60000);
export const WINDOW_LABEL = `${WINDOW_LABEL_MINUTES}m`;

const DEFAULT_WATCHLIST_EXTRAS = Object.freeze<readonly LiveDiagnosticsWatchExtra[]>([
    { key: 'payload_kb', label: 'Payload (KB)', unit: 'KB', warning: 200, critical: 320, should_notify: false },
    { key: 'latency_ms', label: 'Latency (ms)', unit: 'ms', warning: 500, critical: 1500, should_notify: false },
    { key: 'retries', label: 'Retries', unit: '', warning: 1, critical: 3, should_notify: false },
]);

const DEFAULT_AUTO_SNAPSHOT: LiveDiagnosticsAutoSnapshot = Object.freeze({
    interval_seconds: 0,
    mode: 'console' as const,
    destination: 'console' as const,
    retention_minutes: 0,
});

const DEFAULT_GUIDELINES: LiveDiagnosticsGuidelines = Object.freeze({
    hydrator: {
        trigger_per_hour: { warning: 45, critical: 60 },
        failure_rate: { warning: 0.03, critical: 0.05 },
    },
    watchlist: {
        extras: DEFAULT_WATCHLIST_EXTRAS,
        limit: 6,
    },
    auto_snapshot: DEFAULT_AUTO_SNAPSHOT,
});

export function formatTimestamp(ms: number): string {
    return ms.toFixed(2);
}

export function formatDelta(now: number, startedAt: number): string {
    return (now - startedAt).toFixed(2);
}

export function formatPercent(value: number): string {
    return `${(value * 100).toFixed(1)}%`;
}

export function formatMetricNumber(value: number): string {
    if (!Number.isFinite(value)) {
        return '-';
    }
    return Number.isInteger(value) ? value.toString() : value.toFixed(1);
}

function normalizeWatchExtras(entries: unknown): readonly LiveDiagnosticsWatchExtra[] {
    if (!Array.isArray(entries) || entries.length === 0) {
        return DEFAULT_GUIDELINES.watchlist.extras;
    }
    const normalized = entries
        .map((entry): LiveDiagnosticsWatchExtra | null => {
            if (typeof entry === 'string') {
                const key = entry.trim();
                if (!key) return null;
                if (key === 'payload_kb') {
                    return DEFAULT_GUIDELINES.watchlist.extras[0];
                }
                return { key, label: key, unit: '', warning: 0, critical: 0, should_notify: false };
            }
            if (typeof entry === 'object' && entry) {
                const key =
                    typeof (entry as { key?: unknown }).key === 'string' && (entry as { key: string }).key
                        ? (entry as { key: string }).key
                        : 'payload_kb';
                const base = key === 'payload_kb' ? DEFAULT_GUIDELINES.watchlist.extras[0] : undefined;
                const warning =
                    typeof (entry as { warning?: unknown }).warning === 'number'
                        ? (entry as { warning: number }).warning
                        : (base?.warning ?? 0);
                const criticalValue =
                    typeof (entry as { critical?: unknown }).critical === 'number'
                        ? (entry as { critical: number }).critical
                        : (base?.critical ?? warning);
                const critical = Math.max(criticalValue, warning);
                return {
                    key,
                    label:
                        typeof (entry as { label?: unknown }).label === 'string' && (entry as { label: string }).label
                            ? (entry as { label: string }).label
                            : (base?.label ?? key),
                    unit:
                        typeof (entry as { unit?: unknown }).unit === 'string'
                            ? (entry as { unit: string }).unit
                            : (base?.unit ?? ''),
                    warning,
                    critical,
                    should_notify:
                        typeof (entry as { should_notify?: unknown }).should_notify === 'boolean'
                            ? (entry as { should_notify: boolean }).should_notify
                            : (base?.should_notify ?? false),
                };
            }
            return null;
        })
        .filter((extra): extra is LiveDiagnosticsWatchExtra => Boolean(extra));
    return normalized.length ? Object.freeze(normalized) : DEFAULT_GUIDELINES.watchlist.extras;
}

export function resolveGuidelines(doc: Document): LiveDiagnosticsGuidelines {
    const GUIDELINES_DATA_ATTR = 'data-live-diagnostics-guidelines';
    const GUIDELINES_PLACEHOLDER = '__LIVE_DIAGNOSTICS_CONFIG__';
    const target = doc.body ?? doc.documentElement;
    const attr = target?.getAttribute(GUIDELINES_DATA_ATTR);
    if (!attr || attr === GUIDELINES_PLACEHOLDER) {
        return DEFAULT_GUIDELINES;
    }
    try {
        const parsed = JSON.parse(attr) as Partial<LiveDiagnosticsGuidelines>;
        const watchlistExtras = normalizeWatchExtras(parsed?.watchlist?.extras);
        const watchlistLimit = Math.max(1, parsed?.watchlist?.limit ?? DEFAULT_GUIDELINES.watchlist.limit);
        const autoSnapshotConfig = parsed?.auto_snapshot;
        const intervalSeconds = Math.max(0, Number(autoSnapshotConfig?.interval_seconds ?? 0));
        const mode = autoSnapshotConfig?.mode === 'clipboard' ? 'clipboard' : 'console';
        const destinationRaw = (autoSnapshotConfig?.destination ?? '').toLowerCase();
        const destination: 'console' | 'clipboard' | 'api' =
            destinationRaw === 'api'
                ? 'api'
                : destinationRaw === 'clipboard'
                  ? 'clipboard'
                  : destinationRaw === 'console'
                    ? 'console'
                    : mode === 'clipboard'
                      ? 'clipboard'
                      : 'console';
        const retentionMinutes = Math.max(0, Number(autoSnapshotConfig?.retention_minutes ?? 0));
        return {
            hydrator: {
                trigger_per_hour: {
                    warning:
                        parsed?.hydrator?.trigger_per_hour?.warning ??
                        DEFAULT_GUIDELINES.hydrator.trigger_per_hour.warning,
                    critical:
                        parsed?.hydrator?.trigger_per_hour?.critical ??
                        DEFAULT_GUIDELINES.hydrator.trigger_per_hour.critical,
                },
                failure_rate: {
                    warning:
                        parsed?.hydrator?.failure_rate?.warning ?? DEFAULT_GUIDELINES.hydrator.failure_rate.warning,
                    critical:
                        parsed?.hydrator?.failure_rate?.critical ?? DEFAULT_GUIDELINES.hydrator.failure_rate.critical,
                },
            },
            watchlist: {
                extras: watchlistExtras,
                limit: watchlistLimit,
            },
            auto_snapshot: autoSnapshotConfig
                ? {
                      interval_seconds: intervalSeconds,
                      mode,
                      destination,
                      retention_minutes: retentionMinutes,
                  }
                : DEFAULT_GUIDELINES.auto_snapshot,
        } satisfies LiveDiagnosticsGuidelines;
    } catch {
        return DEFAULT_GUIDELINES;
    }
}

function severityScore(severity: AlertSeverity): number {
    switch (severity) {
        case 'critical':
            return 2;
        case 'warning':
            return 1;
        default:
            return 0;
    }
}

function maxSeverity(a: AlertSeverity, b: AlertSeverity): AlertSeverity {
    return severityScore(a) >= severityScore(b) ? a : b;
}

function evaluateHydratorLoad(perHour: number, guidelines: LiveDiagnosticsGuidelines): AlertSeverity {
    if (perHour >= guidelines.hydrator.trigger_per_hour.critical) {
        return 'critical';
    }
    if (perHour >= guidelines.hydrator.trigger_per_hour.warning) {
        return 'warning';
    }
    return 'ok';
}

function evaluateHydratorFailure(rate: number, guidelines: LiveDiagnosticsGuidelines): AlertSeverity {
    if (rate >= guidelines.hydrator.failure_rate.critical) {
        return 'critical';
    }
    if (rate >= guidelines.hydrator.failure_rate.warning) {
        return 'warning';
    }
    return 'ok';
}

function evaluateExtraSeverity(value: number, descriptor: LiveDiagnosticsWatchExtra): AlertSeverity {
    if (value >= descriptor.critical) {
        return 'critical';
    }
    if (value >= descriptor.warning) {
        return 'warning';
    }
    return 'ok';
}

export function getSeverityColor(severity: AlertSeverity): string {
    switch (severity) {
        case 'critical':
            return '#f87171';
        case 'warning':
            return '#fbbf24';
        default:
            return '#34d399';
    }
}

export function getHeatmapColor(level: number): string {
    const clamped = Math.min(1, Math.max(0, level));
    const hue = 200 - clamped * 160;
    const saturation = 70;
    const lightness = 25 + clamped * 25;
    return `hsl(${hue}, ${saturation}%, ${lightness}%)`;
}

export function collectHydratorAlerts(
    diagnostics: LiveNamespaceDiagnostics,
    guidelines: LiveDiagnosticsGuidelines,
): HydratorAlertRow[] {
    const entries = Object.values(diagnostics.metrics ?? {});
    const alerts: HydratorAlertRow[] = [];
    entries.forEach((metric) => {
        const windowStats = metric.window;
        if (!windowStats || windowStats.durationMs <= 0) {
            return;
        }
        const triggeredPerHour = (windowStats.triggered / windowStats.durationMs) * MS_PER_HOUR;
        const failureRate =
            windowStats.triggered > 0 ? windowStats.failed / windowStats.triggered : windowStats.failed > 0 ? 1 : 0;
        const loadSeverity = evaluateHydratorLoad(triggeredPerHour, guidelines);
        const failureSeverity = evaluateHydratorFailure(failureRate, guidelines);
        const severity = maxSeverity(loadSeverity, failureSeverity);
        if (severity === 'ok') {
            return;
        }
        const status: string[] = [];
        if (loadSeverity !== 'ok') {
            status.push(`High load (${Math.round(triggeredPerHour)} /h)`);
        }
        if (failureSeverity !== 'ok') {
            status.push(`Failure ${formatPercent(failureRate)}`);
        }
        alerts.push({
            metric: metric.name,
            triggered: windowStats.triggered,
            failures: windowStats.failed,
            cacheHit: windowStats.cacheHit,
            failureRate,
            status: status.join(' · '),
            severity,
            loadPerHour: triggeredPerHour,
        });
    });
    return alerts.sort((a, b) => {
        const severityDiff = severityScore(b.severity) - severityScore(a.severity);
        if (severityDiff !== 0) {
            return severityDiff;
        }
        return b.triggered - a.triggered;
    });
}

export function collectPayloadWatchRows(
    diagnostics: LiveNamespaceDiagnostics,
    guidelines: LiveDiagnosticsGuidelines,
): PayloadWatchRow[] {
    const watchlist = guidelines.watchlist.extras.length
        ? guidelines.watchlist.extras
        : DEFAULT_GUIDELINES.watchlist.extras;
    const rows: PayloadWatchRow[] = [];
    Object.values(diagnostics.metrics ?? {}).forEach((metric) => {
        if (!metric.extraWindows) {
            return;
        }
        watchlist.forEach((descriptor) => {
            const summary = metric.extraWindows?.[descriptor.key];
            if (!summary) {
                return;
            }
            rows.push({
                metric: metric.name,
                descriptor,
                latest: summary.latest,
                average: summary.average,
                max: summary.max,
                min: summary.min,
                values: summary.values,
                severity: evaluateExtraSeverity(summary.latest, descriptor),
            });
        });
    });
    return rows
        .sort((a, b) => {
            const severityDiff = severityScore(b.severity) - severityScore(a.severity);
            if (severityDiff !== 0) {
                return severityDiff;
            }
            return b.latest - a.latest;
        })
        .slice(0, guidelines.watchlist.limit);
}

export function notifyWatchAlerts(
    owner: LiveNamespaceOwner,
    diagnostics: LiveNamespaceDiagnostics,
    guidelines: LiveDiagnosticsGuidelines,
    previousSignature: string,
    eventName: string,
): string {
    const rows = collectPayloadWatchRows(diagnostics, guidelines).filter(
        (row) => row.descriptor.should_notify && row.severity !== 'ok',
    );
    const signature = rows.length
        ? JSON.stringify(
              rows
                  .map((row) => ({ metric: row.metric, key: row.descriptor.key, severity: row.severity }))
                  .sort((a, b) => {
                      const metricDiff = a.metric.localeCompare(b.metric);
                      if (metricDiff !== 0) {
                          return metricDiff;
                      }
                      return a.key.localeCompare(b.key);
                  }),
          )
        : '';
    if (signature === previousSignature) {
        return previousSignature;
    }
    if (typeof owner.dispatchEvent !== 'function') {
        return signature;
    }
    owner.dispatchEvent(
        new CustomEvent(eventName, {
            detail: {
                alerts: rows.map((row) => ({
                    metric: row.metric,
                    key: row.descriptor.key,
                    label: row.descriptor.label,
                    severity: row.severity,
                    latest: row.latest,
                })),
                diagnostics,
            },
        }),
    );
    return signature;
}

export function formatWatchValue(value: number, descriptor: LiveDiagnosticsWatchExtra): string {
    if (!Number.isFinite(value)) {
        return '-';
    }
    const precision = descriptor.unit.toLowerCase() === 'ms' ? 0 : 1;
    const formatted = Number.isInteger(value) ? value.toString() : value.toFixed(precision);
    return descriptor.unit ? `${formatted} ${descriptor.unit}` : formatted;
}
