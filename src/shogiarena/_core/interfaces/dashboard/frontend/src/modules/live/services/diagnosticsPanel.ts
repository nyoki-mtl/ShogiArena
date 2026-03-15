import {
    DIAGNOSTICS_EVENT,
    getLiveNamespaceDiagnostics,
    type LiveNamespaceDiagnostics,
    type LiveNamespaceOwner,
    type LiveNamespaceMetric,
} from '@/modules/live/utils/liveNamespace';
import { DASHBOARD_RECOVERABLE_FAILURE_EVENT, formatErrorMessage } from '@/modules/shared/utils/errors';
import {
    type AlertSeverity,
    WINDOW_LABEL,
    collectHydratorAlerts,
    collectPayloadWatchRows,
    formatDelta,
    formatMetricNumber,
    formatPercent,
    formatTimestamp,
    formatWatchValue,
    getHeatmapColor,
    getSeverityColor,
    notifyWatchAlerts,
    resolveGuidelines,
    type LiveDiagnosticsGuidelines,
} from './diagnostics/helpers';
import { ensureDiagnosticsStyles } from './diagnostics/styles';
import { recordLiveDiagnosticsMetric } from '@/modules/live/utils/liveNamespace/metrics';
import { LIVE_DIAGNOSTICS_PANEL_ID as PANEL_ID } from './diagnostics/constants';

const TIMELINE_TABLE_ID = `${PANEL_ID}Timeline`;
const PROVIDERS_TABLE_ID = `${PANEL_ID}Providers`;
const METRICS_TABLE_ID = `${PANEL_ID}Metrics`;
const ALERTS_TABLE_ID = `${PANEL_ID}Alerts`;
const PAYLOAD_TABLE_ID = `${PANEL_ID}Payload`;
const BACKFILL_TABLE_ID = `${PANEL_ID}Backfill`;
const FAILURES_TABLE_ID = `${PANEL_ID}Failures`;
const WS_TABLE_ID = `${PANEL_ID}Ws`;
const SVG_NS = 'http://www.w3.org/2000/svg';
const DIAGNOSTICS_ALERT_EVENT = 'DashboardDiagnosticsUpdate';

function renderProviders(doc: Document, diagnostics: LiveNamespaceDiagnostics): void {
    const table = doc.getElementById(PROVIDERS_TABLE_ID) as HTMLTableElement | null;
    if (!table) return;

    const tbody = table.tBodies[0] ?? table.createTBody();
    tbody.innerHTML = '';

    diagnostics.order.forEach((key) => {
        const row = doc.createElement('tr');
        const apiCell = doc.createElement('th');
        apiCell.scope = 'row';
        apiCell.textContent = key;
        const providerCell = doc.createElement('td');
        providerCell.textContent = diagnostics.providers[key] ?? '(unknown)';
        row.appendChild(apiCell);
        row.appendChild(providerCell);
        tbody.appendChild(row);
    });
}

function renderTimeline(doc: Document, diagnostics: LiveNamespaceDiagnostics): void {
    const table = doc.getElementById(TIMELINE_TABLE_ID) as HTMLTableElement | null;
    if (!table) return;

    const tbody = table.tBodies[0] ?? table.createTBody();
    tbody.innerHTML = '';

    diagnostics.timeline.forEach((entry) => {
        const row = doc.createElement('tr');
        const apiCell = doc.createElement('th');
        apiCell.scope = 'row';
        apiCell.textContent = entry.key;

        const providerCell = doc.createElement('td');
        providerCell.textContent = entry.provider ?? '(unknown)';

        const sinceStartCell = doc.createElement('td');
        sinceStartCell.textContent = formatTimestamp(entry.sinceStart);

        const sincePrevCell = doc.createElement('td');
        sincePrevCell.textContent = formatTimestamp(entry.sincePrevious);

        row.appendChild(apiCell);
        row.appendChild(providerCell);
        row.appendChild(sinceStartCell);
        row.appendChild(sincePrevCell);

        tbody.appendChild(row);
    });
}

function renderMetadata(doc: Document, diagnostics: LiveNamespaceDiagnostics): void {
    const started = doc.getElementById(`${PANEL_ID}StartedAt`);
    if (started) {
        started.textContent = formatTimestamp(diagnostics.startedAt);
    }

    const updated = doc.getElementById(`${PANEL_ID}UpdatedAt`);
    if (updated) {
        updated.textContent = formatTimestamp(diagnostics.lastUpdated);
    }

    const duration = doc.getElementById(`${PANEL_ID}Duration`);
    if (duration) {
        duration.textContent = formatDelta(diagnostics.lastUpdated, diagnostics.startedAt);
    }
}

function renderDiagnostics(
    doc: Document,
    diagnostics: LiveNamespaceDiagnostics,
    guidelines: LiveDiagnosticsGuidelines,
): void {
    renderProviders(doc, diagnostics);
    renderTimeline(doc, diagnostics);
    renderPayloadWatchlist(doc, diagnostics, guidelines);
    renderBackfillDiagnostics(doc, diagnostics);
    renderMetrics(doc, diagnostics);
    renderAlerts(doc, diagnostics, guidelines);
    renderMetadata(doc, diagnostics);
}

function formatMetricExtras(metric: LiveNamespaceMetric): string {
    const entries = metric.extras ? Object.entries(metric.extras) : [];
    if (!entries.length) {
        return '—';
    }
    return entries
        .map(([key, stats]) => {
            const avg = stats.samples > 0 ? stats.total / stats.samples : 0;
            const summary: string[] = [];
            summary.push(`last=${formatMetricNumber(stats.last)}`);
            summary.push(`max=${formatMetricNumber(stats.max)}`);
            if (stats.samples > 1) {
                summary.push(`avg=${formatMetricNumber(avg)}`);
            }
            summary.push(`samples=${formatMetricNumber(stats.samples)}`);
            return `${key}: ${summary.join(', ')}`;
        })
        .join(' · ');
}

function renderMetrics(doc: Document, diagnostics: LiveNamespaceDiagnostics): void {
    const table = doc.getElementById(METRICS_TABLE_ID) as HTMLTableElement | null;
    if (!table) return;
    const tbody = table.tBodies[0] ?? table.createTBody();
    tbody.innerHTML = '';
    const entries = Object.values(diagnostics.metrics ?? {});
    if (!entries.length) {
        const row = doc.createElement('tr');
        const cell = doc.createElement('td');
        cell.colSpan = 6;
        cell.textContent = 'No hydration metrics recorded yet.';
        row.appendChild(cell);
        tbody.appendChild(row);
        return;
    }
    entries
        .sort((a, b) => b.updatedAt - a.updatedAt)
        .forEach((metric) => {
            const row = doc.createElement('tr');
            const nameCell = doc.createElement('th');
            nameCell.scope = 'row';
            nameCell.textContent = metric.name;
            const triggeredCell = doc.createElement('td');
            triggeredCell.textContent = metric.triggered.toString();
            const failedCell = doc.createElement('td');
            failedCell.textContent = metric.failed.toString();
            const cacheCell = doc.createElement('td');
            cacheCell.textContent = metric.cacheHit.toString();
            const extrasCell = doc.createElement('td');
            extrasCell.textContent = formatMetricExtras(metric);
            const updatedCell = doc.createElement('td');
            updatedCell.textContent = metric.updatedAt.toFixed(2);
            row.append(nameCell, triggeredCell, failedCell, cacheCell, extrasCell, updatedCell);
            tbody.appendChild(row);
        });
}

function renderPayloadWatchlist(
    doc: Document,
    diagnostics: LiveNamespaceDiagnostics,
    guidelines: LiveDiagnosticsGuidelines,
): void {
    const table = doc.getElementById(PAYLOAD_TABLE_ID) as HTMLTableElement | null;
    if (!table) return;
    const tbody = table.tBodies[0] ?? table.createTBody();
    tbody.innerHTML = '';
    const rows = collectPayloadWatchRows(diagnostics, guidelines);
    if (!rows.length) {
        const row = doc.createElement('tr');
        const cell = doc.createElement('td');
        cell.colSpan = 8;
        cell.textContent = `No payload diagnostics recorded in the last ${WINDOW_LABEL}.`;
        row.appendChild(cell);
        tbody.appendChild(row);
        return;
    }
    rows.forEach((entry) => {
        const row = doc.createElement('tr');
        row.classList.add('is-alert', `is-${entry.severity}`);
        const metricCell = doc.createElement('th');
        metricCell.scope = 'row';
        metricCell.textContent = entry.metric;
        const fieldCell = doc.createElement('td');
        fieldCell.textContent = entry.descriptor.label;
        const latestCell = doc.createElement('td');
        latestCell.textContent = formatWatchValue(entry.latest, entry.descriptor);
        const avgCell = doc.createElement('td');
        avgCell.textContent = formatWatchValue(entry.average, entry.descriptor);
        const maxCell = doc.createElement('td');
        maxCell.textContent = formatWatchValue(entry.max, entry.descriptor);
        const heatmapCell = doc.createElement('td');
        heatmapCell.appendChild(createHeatmap(doc, entry.values, entry.min, entry.max));
        const sparklineCell = doc.createElement('td');
        sparklineCell.appendChild(createSparkline(doc, entry.values, entry.min, entry.max, entry.severity));
        const statusCell = doc.createElement('td');
        statusCell.textContent =
            entry.severity === 'critical' ? 'Critical' : entry.severity === 'warning' ? 'Warning' : 'Stable';
        row.append(metricCell, fieldCell, latestCell, avgCell, maxCell, heatmapCell, sparklineCell, statusCell);
        tbody.appendChild(row);
    });
}

function renderBackfillDiagnostics(doc: Document, diagnostics: LiveNamespaceDiagnostics): void {
    const table = doc.getElementById(BACKFILL_TABLE_ID) as HTMLTableElement | null;
    if (!table) return;
    const tbody = table.tBodies[0] ?? table.createTBody();
    tbody.innerHTML = '';
    const metric = diagnostics.metrics?.['spsa:ltc:backfill'];
    if (!metric || !metric.extras) {
        const row = doc.createElement('tr');
        const cell = doc.createElement('td');
        cell.colSpan = 4;
        cell.textContent = 'No REST backfill events recorded.';
        row.appendChild(cell);
        tbody.appendChild(row);
        return;
    }
    const gap = metric.extras.gapDetected?.total ?? 0;
    const inactive = metric.extras.streamInactive?.total ?? 0;
    const stale = metric.extras.streamStale?.total ?? 0;
    type BackfillRow = { reason: string; count: number; latest: number };
    const rows: BackfillRow[] = [
        {
            reason: 'Gap detected',
            count: gap,
            latest: metric.extras.gapDetected?.last ?? 0,
        },
        {
            reason: 'Stream inactive',
            count: inactive,
            latest: metric.extras.streamInactive?.last ?? 0,
        },
        {
            reason: 'Stream stale',
            count: stale,
            latest: metric.extras.streamStale?.last ?? 0,
        },
    ];
    rows.forEach((entry) => {
        const row = doc.createElement('tr');
        const reasonCell = doc.createElement('th');
        reasonCell.scope = 'row';
        reasonCell.textContent = entry.reason;
        const countCell = doc.createElement('td');
        countCell.textContent = formatMetricNumber(entry.count);
        const lastCell = doc.createElement('td');
        lastCell.textContent = formatMetricNumber(entry.latest);
        const updatedCell = doc.createElement('td');
        updatedCell.textContent = metric.updatedAt.toFixed(2);
        row.append(reasonCell, countCell, lastCell, updatedCell);
        tbody.appendChild(row);
    });
}

function createHeatmap(doc: Document, values: readonly number[], min: number, max: number): HTMLElement {
    const container = doc.createElement('div');
    container.className = 'live-diagnostics-heatmap';
    if (!values.length) {
        return container;
    }
    const range = Math.max(1e-6, max - min);
    values.forEach((value) => {
        const normalized = Math.min(1, Math.max(0, (value - min) / range));
        const cell = doc.createElement('span');
        cell.className = 'live-diagnostics-heatmap__cell';
        cell.style.backgroundColor = getHeatmapColor(normalized);
        container.appendChild(cell);
    });
    return container;
}

function createSparkline(
    doc: Document,
    values: readonly number[],
    min: number,
    max: number,
    severity: AlertSeverity,
): SVGSVGElement {
    const svg = doc.createElementNS(SVG_NS, 'svg');
    svg.setAttribute('viewBox', '0 0 80 24');
    svg.setAttribute('preserveAspectRatio', 'none');
    svg.classList.add('live-diagnostics-sparkline');
    if (!values.length) {
        return svg;
    }
    const range = Math.max(1e-6, max - min);
    const denominator = Math.max(values.length - 1, 1);
    const points = values
        .map((value, idx) => {
            const x = (idx / denominator) * 80;
            const normalized = 1 - (value - min) / range;
            const y = normalized * 24;
            return `${x.toFixed(2)},${y.toFixed(2)}`;
        })
        .join(' ');
    const polyline = doc.createElementNS(SVG_NS, 'polyline');
    polyline.setAttribute('fill', 'none');
    polyline.setAttribute('stroke', getSeverityColor(severity));
    polyline.setAttribute('stroke-width', '2');
    polyline.setAttribute('points', points);
    svg.appendChild(polyline);
    return svg;
}

function renderAlerts(
    doc: Document,
    diagnostics: LiveNamespaceDiagnostics,
    guidelines: LiveDiagnosticsGuidelines,
): void {
    const table = doc.getElementById(ALERTS_TABLE_ID) as HTMLTableElement | null;
    if (!table) return;
    const tbody = table.tBodies[0] ?? table.createTBody();
    tbody.innerHTML = '';
    const alerts = collectHydratorAlerts(diagnostics, guidelines);
    if (!alerts.length) {
        const row = doc.createElement('tr');
        const cell = doc.createElement('td');
        cell.colSpan = 6;
        cell.textContent = `No hydrator alerts in the last ${WINDOW_LABEL}.`;
        row.appendChild(cell);
        tbody.appendChild(row);
        return;
    }
    alerts.forEach((alert) => {
        const row = doc.createElement('tr');
        row.classList.add('is-alert', `is-${alert.severity}`);
        const nameCell = doc.createElement('th');
        nameCell.scope = 'row';
        nameCell.textContent = alert.metric;
        const triggeredCell = doc.createElement('td');
        triggeredCell.textContent = alert.triggered.toString();
        const failedCell = doc.createElement('td');
        failedCell.textContent = alert.failures.toString();
        const rateCell = doc.createElement('td');
        rateCell.textContent = formatPercent(alert.failureRate);
        const cacheCell = doc.createElement('td');
        cacheCell.textContent = alert.cacheHit.toString();
        const statusCell = doc.createElement('td');
        statusCell.textContent = alert.status;
        row.append(nameCell, triggeredCell, failedCell, rateCell, cacheCell, statusCell);
        tbody.appendChild(row);
    });
}

type RecoverableFailurePayload = {
    scope: string;
    message: string;
    userMessage: string | null;
    error: unknown;
    ts: number;
};

function renderRecoverableFailures(doc: Document, failures: readonly RecoverableFailurePayload[]): void {
    const table = doc.getElementById(FAILURES_TABLE_ID) as HTMLTableElement | null;
    if (!table) return;
    const tbody = table.tBodies[0] ?? table.createTBody();
    tbody.innerHTML = '';
    if (!failures.length) {
        const row = doc.createElement('tr');
        const cell = doc.createElement('td');
        cell.colSpan = 4;
        cell.textContent = 'No recoverable failures reported in this session.';
        row.appendChild(cell);
        tbody.appendChild(row);
        return;
    }
    failures
        .slice()
        .sort((a, b) => b.ts - a.ts)
        .forEach((entry) => {
            const row = doc.createElement('tr');
            const scopeCell = doc.createElement('th');
            scopeCell.scope = 'row';
            scopeCell.textContent = entry.scope;
            const timeCell = doc.createElement('td');
            timeCell.textContent = new Date(entry.ts).toISOString();
            const userCell = doc.createElement('td');
            userCell.textContent = entry.userMessage ?? '—';
            const detailCell = doc.createElement('td');
            detailCell.textContent = entry.message || formatErrorMessage(entry.error);
            row.append(scopeCell, timeCell, userCell, detailCell);
            tbody.appendChild(row);
        });
}

type WsDiagnosticsSnapshot = {
    clients: number;
    analysis_buffer: number;
    drop_counts: Record<string, number>;
    drop_total?: number;
    drops_by_reason?: Record<string, number>;
    drops_by_topic?: Record<string, number>;
    started_at_ms?: number;
    ts_ms?: number;
    uptime_ms?: number;
    topics: Record<string, { seq: number; ring_size: number }>;
};

function renderWsDiagnostics(doc: Document, wsDiag: WsDiagnosticsSnapshot | null): void {
    const table = doc.getElementById(WS_TABLE_ID) as HTMLTableElement | null;
    if (!table) return;
    const tbody = table.tBodies[0] ?? table.createTBody();
    tbody.innerHTML = '';

    const dropsList = doc.getElementById('wsDropsList');
    if (dropsList) {
        dropsList.innerHTML = '';
    }

    if (!wsDiag) {
        const row = doc.createElement('tr');
        const cell = doc.createElement('td');
        cell.colSpan = 5;
        cell.textContent = 'No WebSocket diagnostics available.';
        row.appendChild(cell);
        tbody.appendChild(row);
        return;
    }

    const topics = Object.entries(wsDiag.topics ?? {});
    if (!topics.length) {
        const row = doc.createElement('tr');
        const cell = doc.createElement('td');
        cell.colSpan = 5;
        cell.textContent = 'No topics recorded yet.';
        row.appendChild(cell);
        tbody.appendChild(row);
    } else {
        topics
            .sort(([a], [b]) => a.localeCompare(b))
            .forEach(([topic, info]) => {
                const row = doc.createElement('tr');
                const nameCell = doc.createElement('th');
                nameCell.scope = 'row';
                nameCell.textContent = topic;
                const seqCell = doc.createElement('td');
                seqCell.textContent = String(info?.seq ?? '-');
                const ringCell = doc.createElement('td');
                ringCell.textContent = String(info?.ring_size ?? '-');
                const dropCell = doc.createElement('td');
                const dropKeys = Object.entries(wsDiag.drop_counts ?? {}).filter(([key]) => key.endsWith(`:${topic}`));
                const dropTotal = dropKeys.reduce((sum, [, count]) => sum + (count ?? 0), 0);
                dropCell.textContent = dropTotal ? dropTotal.toString() : '0';
                const bufferCell = doc.createElement('td');
                bufferCell.textContent = topic.includes('analysis') ? String(wsDiag.analysis_buffer ?? 0) : '—';
                row.append(nameCell, seqCell, ringCell, dropCell, bufferCell);
                tbody.appendChild(row);
            });
    }

    // Footer row for totals
    const footer = doc.createElement('tr');
    footer.classList.add('is-summary');
    const label = doc.createElement('th');
    label.scope = 'row';
    label.textContent = 'Totals';
    const seq = doc.createElement('td');
    seq.textContent = `clients: ${wsDiag.clients ?? 0}`;
    const ring = doc.createElement('td');
    ring.textContent = `topics: ${topics.length}`;
    const drops = doc.createElement('td');
    const dropSum = Object.values(wsDiag.drop_counts ?? {}).reduce((a, b) => a + b, 0);
    drops.textContent = `drops: ${dropSum}`;
    const buffer = doc.createElement('td');
    buffer.textContent = `analysis buffer: ${wsDiag.analysis_buffer ?? 0}`;
    footer.append(label, seq, ring, drops, buffer);
    tbody.appendChild(footer);

    // Detailed drop reasons
    if (dropsList && dropSum > 0) {
        const entries = Object.entries(wsDiag.drop_counts ?? {}).sort((a, b) => b[1] - a[1]);
        for (const [reasonTopic, count] of entries) {
            const li = doc.createElement('li');
            li.textContent = `${reasonTopic}: ${count}`;
            dropsList.appendChild(li);
        }
    }
}

function createDiagnosticsTable(doc: Document, id: string, headers: string[]): HTMLTableElement {
    const table = doc.createElement('table');
    table.id = id;
    table.className = 'live-diagnostics-table';

    const thead = doc.createElement('thead');
    const headerRow = doc.createElement('tr');
    headers.forEach((title, index) => {
        const cell = doc.createElement(index === 0 ? 'th' : 'td');
        if (index === 0) {
            (cell as HTMLTableCellElement).scope = 'col';
        }
        cell.textContent = title;
        headerRow.appendChild(cell);
    });
    thead.appendChild(headerRow);
    table.appendChild(thead);

    table.createTBody();
    return table;
}

function ensurePanelContainer(doc: Document): HTMLElement {
    const existing = doc.getElementById(PANEL_ID);
    if (existing) {
        return existing;
    }

    const wrapper = doc.createElement('details');
    wrapper.id = PANEL_ID;
    wrapper.className = 'live-diagnostics-panel';
    wrapper.open = false;

    const summary = doc.createElement('summary');
    summary.textContent = 'Live Diagnostics';
    wrapper.appendChild(summary);

    const content = doc.createElement('div');
    content.className = 'live-diagnostics-content';
    wrapper.appendChild(content);

    doc.body.appendChild(wrapper);

    return wrapper;
}

function wrapDiagnosticsSection(
    doc: Document,
    node: HTMLElement,
    title: string,
    options: { open?: boolean } = {},
): HTMLElement {
    const wrapper = doc.createElement('details');
    wrapper.className = 'live-diagnostics-section';
    wrapper.open = options.open ?? false;

    const summary = doc.createElement('summary');
    summary.textContent = title;
    wrapper.appendChild(summary);

    const scroll = doc.createElement('div');
    scroll.className = 'live-diagnostics-scroll';
    scroll.appendChild(node);
    wrapper.appendChild(scroll);

    return wrapper;
}

function buildPanel(doc: Document): {
    readonly wrapper: HTMLElement;
    readonly content: HTMLElement;
} {
    const wrapper = ensurePanelContainer(doc);
    ensureDiagnosticsStyles(doc);
    const content = wrapper.querySelector<HTMLElement>('.live-diagnostics-content');
    if (!content) {
        throw new Error('live diagnostics content node was not created');
    }
    content.innerHTML = '';

    const meta = doc.createElement('dl');
    meta.className = 'live-diagnostics-meta';

    const startedDt = doc.createElement('dt');
    startedDt.textContent = 'Started (ms)';
    const startedDd = doc.createElement('dd');
    startedDd.id = `${PANEL_ID}StartedAt`;
    startedDd.textContent = '-';

    const updatedDt = doc.createElement('dt');
    updatedDt.textContent = 'Last update (ms)';
    const updatedDd = doc.createElement('dd');
    updatedDd.id = `${PANEL_ID}UpdatedAt`;
    updatedDd.textContent = '-';

    const durationDt = doc.createElement('dt');
    durationDt.textContent = 'Elapsed (ms)';
    const durationDd = doc.createElement('dd');
    durationDd.id = `${PANEL_ID}Duration`;
    durationDd.textContent = '-';

    meta.append(startedDt, startedDd, updatedDt, updatedDd, durationDt, durationDd);

    const actions = doc.createElement('div');
    actions.className = 'live-diagnostics-actions';
    const refreshButton = doc.createElement('button');
    refreshButton.type = 'button';
    refreshButton.textContent = 'Refresh now';
    refreshButton.dataset.action = 'refresh';
    actions.appendChild(refreshButton);
    const exportButton = doc.createElement('button');
    exportButton.type = 'button';
    exportButton.textContent = 'Export snapshot';
    exportButton.dataset.action = 'export';
    actions.appendChild(exportButton);

    const providersTable = createDiagnosticsTable(doc, PROVIDERS_TABLE_ID, ['API', 'Provider']);
    const timelineTable = createDiagnosticsTable(doc, TIMELINE_TABLE_ID, [
        'API',
        'Provider',
        'Δ since start (ms)',
        'Δ since previous (ms)',
    ]);
    const payloadTable = createDiagnosticsTable(doc, PAYLOAD_TABLE_ID, [
        'Metric',
        'Field',
        'Latest',
        `Avg (${WINDOW_LABEL})`,
        `Max (${WINDOW_LABEL})`,
        `Heatmap (${WINDOW_LABEL})`,
        `Sparkline (${WINDOW_LABEL})`,
        'Status',
    ]);
    const backfillTable = createDiagnosticsTable(doc, BACKFILL_TABLE_ID, [
        'Reason',
        'Count (total)',
        'Last value',
        'Last updated (ms)',
    ]);
    const metricsTable = createDiagnosticsTable(doc, METRICS_TABLE_ID, [
        'Metric',
        'Triggered (total)',
        'Failures (total)',
        'Cache hits (total)',
        'Extras',
        'Last updated (ms)',
    ]);
    const wsTable = createDiagnosticsTable(doc, WS_TABLE_ID, ['WS topic', 'seq', 'ring', 'drops', 'analysis buffer']);
    const wsDropWrapper = doc.createElement('div');
    wsDropWrapper.className = 'ws-drop-summary';
    const wsDropTitle = doc.createElement('h5');
    wsDropTitle.textContent = 'Drop counts (reason:topic)';
    const wsDropList = doc.createElement('ul');
    wsDropList.id = 'wsDropsList';
    wsDropList.className = 'ws-drops-list';
    wsDropWrapper.append(wsDropTitle, wsDropList);
    const alertsTable = createDiagnosticsTable(doc, ALERTS_TABLE_ID, [
        'Hydrator',
        `Triggered (${WINDOW_LABEL})`,
        `Failures (${WINDOW_LABEL})`,
        'Failure rate',
        `Cache hits (${WINDOW_LABEL})`,
        'Status',
    ]);
    const failuresTable = createDiagnosticsTable(doc, FAILURES_TABLE_ID, [
        'Scope',
        'Timestamp',
        'User message',
        'Detail',
    ]);

    content.append(
        meta,
        actions,
        wrapDiagnosticsSection(doc, payloadTable, 'Live metrics (window)', { open: true }),
        wrapDiagnosticsSection(doc, providersTable, 'Providers'),
        wrapDiagnosticsSection(doc, timelineTable, 'Timeline'),
        wrapDiagnosticsSection(doc, backfillTable, 'REST backfill (SPSA LTC)'),
        wrapDiagnosticsSection(
            doc,
            (() => {
                const section = doc.createElement('div');
                section.append(wsTable, wsDropWrapper);
                return section;
            })(),
            'WebSocket hub',
            { open: true },
        ),
        wrapDiagnosticsSection(doc, alertsTable, 'Alerts', { open: true }),
        wrapDiagnosticsSection(doc, failuresTable, 'Recoverable failures'),
        wrapDiagnosticsSection(doc, metricsTable, 'Totals (since start)'),
    );

    return { wrapper, content };
}

async function copyDiagnosticsSnapshot(owner: LiveNamespaceOwner, button?: HTMLButtonElement): Promise<boolean> {
    const doc = button?.ownerDocument ?? owner.document ?? document;
    const diagnostics = getLiveNamespaceDiagnostics(owner);
    const payload = JSON.stringify(diagnostics, null, 2);
    const originalLabel = button?.textContent ?? '';
    if (button) {
        button.disabled = true;
    }
    try {
        const copied = await writeClipboardText(payload, doc);
        if (!copied) {
            logDiagnosticsSnapshot(owner, diagnostics);
            if (button) {
                button.textContent = 'Logged to console';
            }
        } else if (button) {
            button.textContent = 'Copied!';
        }
        return copied;
    } catch (error) {
        console.warn?.('[DashboardLive] Failed to copy diagnostics snapshot', error);
        logDiagnosticsSnapshot(owner, diagnostics);
        if (button) {
            button.textContent = 'Logged to console';
        }
        return false;
    } finally {
        if (button) {
            setTimeout(() => {
                button.textContent = originalLabel || 'Export snapshot';
                button.disabled = false;
            }, 2000);
        }
    }
}

function logDiagnosticsSnapshot(owner: LiveNamespaceOwner, diagnostics?: LiveNamespaceDiagnostics): void {
    const snapshot = diagnostics ?? getLiveNamespaceDiagnostics(owner);
    const timestamp = new Date().toISOString();
    if (typeof console.groupCollapsed === 'function') {
        console.groupCollapsed(`[DashboardLive] Diagnostics snapshot @ ${timestamp}`);
    }
    console.info?.('[DashboardLive] Diagnostics snapshot', snapshot);
    if (typeof console.groupEnd === 'function') {
        console.groupEnd();
    }
}

async function sendDiagnosticsSnapshot(owner: LiveNamespaceOwner): Promise<boolean> {
    const diagnostics = getLiveNamespaceDiagnostics(owner);
    if (!diagnostics) {
        return false;
    }
    const payload = {
        snapshot: diagnostics,
        source: 'hud',
        recordedAt: new Date().toISOString(),
    };
    const serialized = JSON.stringify(payload);
    if (typeof navigator !== 'undefined' && typeof navigator.sendBeacon === 'function') {
        const blob = new Blob([serialized], { type: 'application/json' });
        return navigator.sendBeacon('/api/diagnostics/snapshots', blob);
    }
    const fetcher: typeof fetch | undefined = typeof owner.fetch === 'function' ? owner.fetch.bind(owner) : fetch;
    if (typeof fetcher !== 'function') {
        return false;
    }
    try {
        const response = await fetcher('/api/diagnostics/snapshots', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: serialized,
            keepalive: true,
        });
        return response.ok;
    } catch (error) {
        console.warn?.('[DashboardLive] Failed to upload diagnostics snapshot', error);
        return false;
    }
}

async function writeClipboardText(text: string, doc: Document | null): Promise<boolean> {
    if (typeof navigator !== 'undefined') {
        const writer = navigator.clipboard?.writeText;
        if (writer) {
            await writer.call(navigator.clipboard, text);
            return true;
        }
    }
    if (!doc || !doc.body) {
        return false;
    }
    const textarea = doc.createElement('textarea');
    textarea.value = text;
    textarea.setAttribute('readonly', 'true');
    textarea.style.position = 'absolute';
    textarea.style.left = '-9999px';
    doc.body.appendChild(textarea);
    textarea.select();
    let copied = false;
    try {
        copied = !!doc.execCommand?.('copy');
    } catch (error) {
        console.warn?.('[DashboardLive] Clipboard fallback failed', error);
        copied = false;
    }
    textarea.remove();
    return copied;
}

export function installLiveDiagnosticsPanel(owner: LiveNamespaceOwner = window as LiveNamespaceOwner): void {
    const doc = owner.document;
    if (!doc) return;

    const { wrapper } = buildPanel(doc);
    const guidelines = resolveGuidelines(doc);
    let currentStamp = 0;
    let alertSignature = '';
    const failures: RecoverableFailurePayload[] = [];
    let wsDiagnostics: WsDiagnosticsSnapshot | null = null;
    let wsDiagnosticsPrev: WsDiagnosticsSnapshot | null = null;
    let wsDiagnosticsPrevAt: number | null = null;
    let wsTimer: number | null = null;

    const updatePanel = (diagnostics: LiveNamespaceDiagnostics): void => {
        if (!diagnostics || diagnostics.lastUpdated === currentStamp) {
            return;
        }
        currentStamp = diagnostics.lastUpdated;
        renderDiagnostics(doc, diagnostics, guidelines);
        renderWsDiagnostics(doc, wsDiagnostics);
        renderRecoverableFailures(doc, failures);
        alertSignature = notifyWatchAlerts(owner, diagnostics, guidelines, alertSignature, DIAGNOSTICS_ALERT_EVENT);
    };

    const refresh = (): void => {
        const diagnostics = getLiveNamespaceDiagnostics(owner);
        updatePanel(diagnostics);
    };

    const fetchWsDiagnostics = async (): Promise<void> => {
        if (typeof owner.fetch !== 'function') {
            return;
        }
        try {
            const res = await owner.fetch('/api/ws/diagnostics', { cache: 'no-store' });
            if (!res.ok) return;
            const json = (await res.json()) as WsDiagnosticsSnapshot;
            const fetchedAt =
                typeof performance !== 'undefined' && typeof performance.now === 'function'
                    ? performance.now()
                    : Date.now();

            const nextTotal =
                typeof json.drop_total === 'number'
                    ? json.drop_total
                    : Object.values(json.drop_counts ?? {}).reduce((a, b) => a + b, 0);
            const prevTotal =
                wsDiagnosticsPrev && typeof wsDiagnosticsPrev.drop_total === 'number'
                    ? wsDiagnosticsPrev.drop_total
                    : wsDiagnosticsPrev
                      ? Object.values(wsDiagnosticsPrev.drop_counts ?? {}).reduce((a, b) => a + b, 0)
                      : null;
            const deltaTotal =
                typeof prevTotal === 'number' && nextTotal >= prevTotal ? Math.max(0, nextTotal - prevTotal) : 0;

            const deltaByReason = (reason: string): number => {
                const next =
                    typeof json.drops_by_reason?.[reason] === 'number'
                        ? json.drops_by_reason[reason]
                        : Object.entries(json.drop_counts ?? {})
                              .filter(([key]) => key.startsWith(`${reason}:`))
                              .reduce((sum, [, count]) => sum + (count ?? 0), 0);
                const prev =
                    wsDiagnosticsPrev && typeof wsDiagnosticsPrev.drops_by_reason?.[reason] === 'number'
                        ? wsDiagnosticsPrev.drops_by_reason[reason]
                        : wsDiagnosticsPrev
                          ? Object.entries(wsDiagnosticsPrev.drop_counts ?? {})
                                .filter(([key]) => key.startsWith(`${reason}:`))
                                .reduce((sum, [, count]) => sum + (count ?? 0), 0)
                          : null;
                if (typeof prev !== 'number') return 0;
                return next >= prev ? next - prev : 0;
            };

            const prevAt = wsDiagnosticsPrevAt;
            const windowMs = typeof prevAt === 'number' ? Math.max(1, fetchedAt - prevAt) : 0;
            recordLiveDiagnosticsMetric('live.ws.drop', {
                triggered: 1,
                delta_total: deltaTotal,
                total: nextTotal,
                window_ms: windowMs,
                clients: typeof json.clients === 'number' ? json.clients : 0,
                analysis_buffer: typeof json.analysis_buffer === 'number' ? json.analysis_buffer : 0,
                delta_queue_full: deltaByReason('queue_full'),
                delta_low_priority_queue_full: deltaByReason('low_priority_queue_full'),
                delta_high_priority_queue_full: deltaByReason('high_priority_queue_full'),
                delta_queue_full_bootstrap: deltaByReason('queue_full_bootstrap'),
                delta_compacted_low_priority: deltaByReason('compacted_low_priority'),
                delta_compacted_worker_latest: deltaByReason('compacted_worker_latest'),
                delta_compacted_trim: deltaByReason('compacted_trim'),
                delta_compacted_trim_disconnect: deltaByReason('compacted_trim_disconnect'),
                delta_compacted_trim_disconnect_moves_backlog: deltaByReason('compacted_trim_disconnect_moves_backlog'),
            });

            wsDiagnosticsPrev = json;
            wsDiagnosticsPrevAt = fetchedAt;
            wsDiagnostics = json;
            renderWsDiagnostics(doc, wsDiagnostics);
        } catch (error) {
            console.warn?.('[DashboardLive] Failed to fetch WS diagnostics', error);
        }
    };

    const refreshButton = wrapper.querySelector<HTMLButtonElement>('[data-action="refresh"]');
    if (refreshButton) {
        refreshButton.addEventListener('click', refresh, { passive: true });
    }
    const exportButton = wrapper.querySelector<HTMLButtonElement>('[data-action="export"]');
    if (exportButton) {
        exportButton.addEventListener(
            'click',
            () => {
                void copyDiagnosticsSnapshot(owner, exportButton);
            },
            { passive: true },
        );
    }
    scheduleAutoSnapshot(owner, guidelines);

    const listener = (event: Event): void => {
        const detail = (event as CustomEvent<LiveNamespaceDiagnostics>).detail;
        if (detail) {
            updatePanel(detail);
        } else {
            refresh();
        }
    };

    const recoverableListener = (event: Event): void => {
        const detail = (event as CustomEvent<RecoverableFailurePayload>).detail;
        if (!detail || !detail.scope) {
            return;
        }
        failures.push(detail);
        renderRecoverableFailures(doc, failures);
    };

    if (typeof owner.addEventListener === 'function') {
        owner.addEventListener(DIAGNOSTICS_EVENT, listener as EventListener);
        owner.addEventListener(DASHBOARD_RECOVERABLE_FAILURE_EVENT, recoverableListener as EventListener);
        owner.addEventListener('beforeunload', () => {
            owner.removeEventListener?.(DIAGNOSTICS_EVENT, listener as EventListener);
            owner.removeEventListener?.(DASHBOARD_RECOVERABLE_FAILURE_EVENT, recoverableListener as EventListener);
            if (wsTimer) {
                owner.clearInterval?.(wsTimer);
            }
        });
    }

    // poll WS diagnostics (10s)
    void fetchWsDiagnostics().catch((error) => {
        console.warn?.('[DashboardLive] Failed to fetch initial WS diagnostics', error);
    });
    if (typeof owner.setInterval === 'function') {
        wsTimer = owner.setInterval(() => {
            void fetchWsDiagnostics();
        }, 10_000) as unknown as number;
    }

    refresh();
}

function scheduleAutoSnapshot(owner: LiveNamespaceOwner, guidelines: LiveDiagnosticsGuidelines): void {
    const config = guidelines.autoSnapshot;
    if (!config || config.intervalSeconds <= 0) {
        return;
    }
    const intervalMs = Math.max(10000, config.intervalSeconds * 1000);
    const destination = config.destination ?? config.mode ?? 'console';
    const fallbackMode = config.mode ?? 'console';
    const timer = owner.setInterval?.(() => {
        if (destination === 'api') {
            void sendDiagnosticsSnapshot(owner).then((uploaded) => {
                if (uploaded) {
                    return;
                }
                if (fallbackMode === 'clipboard') {
                    void copyDiagnosticsSnapshot(owner).catch((error) => {
                        console.warn?.('[DashboardLive] Clipboard snapshot failed', error);
                        logDiagnosticsSnapshot(owner);
                    });
                    return;
                }
                logDiagnosticsSnapshot(owner);
            });
            return;
        }
        if (destination === 'clipboard') {
            void copyDiagnosticsSnapshot(owner).catch((error) => {
                console.warn?.('[DashboardLive] Clipboard snapshot failed', error);
                logDiagnosticsSnapshot(owner);
            });
            return;
        }
        logDiagnosticsSnapshot(owner);
    }, intervalMs);
    if (timer && owner.addEventListener) {
        owner.addEventListener('beforeunload', () => {
            owner.clearInterval?.(timer);
        });
    }
}
