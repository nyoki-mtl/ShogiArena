import type { EngineInstanceDetail, EngineMetaDetail, InstanceConfig, InstanceMetrics } from '../types/internal';
import { resolveOptionSourceLabel } from '@/modules/shared/utils/engineOptionSources';
import { escapeHtml } from '@/modules/shared/utils/html';
import { formatDuration, formatNodesCountDetail, parseTimeControlSpec } from '@/modules/shared/utils/timeControl';
import { formatOptionValue } from '@/modules/tournament/utils/formatters';
import type { DashboardCore } from '@/types/dashboard';

export type OptionsRenderDeps = Record<string, never>;

export interface TimeControlRenderDeps {
    warnSoftFailure: DashboardCore['warnSoftFailure'];
}

function renderOptionsTable(meta: EngineMetaDetail | undefined, _deps: OptionsRenderDeps): string {
    const merged = meta?.merged_options ?? {};
    const resolved = meta?.resolved_options ?? {};
    const runtime = meta?.runtime_usi_options ?? {};
    const sources = meta?.option_sources ?? {};
    const sourceDetails = meta?.option_sources_details ?? {};
    const formatValue = formatOptionValue;

    const keys = Object.keys(merged).sort((a, b) => a.localeCompare(b));

    if (!keys.length) {
        return '<div class="subtle">No USI options configured.</div>';
    }

    let rows = '';
    for (const key of keys) {
        const rawValue = merged[key];
        const resolvedValue = Object.hasOwn(resolved, key) ? resolved[key] : rawValue;
        const runtimeEntry = runtime[key];
        const currentValue =
            runtimeEntry && runtimeEntry.current !== undefined && runtimeEntry.current !== null
                ? runtimeEntry.current
                : resolvedValue;
        const defaultValue =
            runtimeEntry && runtimeEntry.default !== undefined && runtimeEntry.default !== null
                ? runtimeEntry.default
                : null;
        const isDifferent = defaultValue !== null && formatValue(defaultValue) !== formatValue(currentValue);
        const sourceLabel = resolveOptionSourceLabel(key, sources, sourceDetails);
        rows += `<tr${isDifferent ? ' class="option-diff"' : ''}>`;
        rows += `<td>${escapeHtml(key)}</td>`;
        rows += `<td>${escapeHtml(formatValue(resolvedValue ?? currentValue))}</td>`;
        rows += `<td>${
            defaultValue !== null ? escapeHtml(formatValue(defaultValue)) : '<span class="subtle">-</span>'
        }</td>`;
        rows += `<td class="subtle">${escapeHtml(sourceLabel)}</td>`;
        rows += '</tr>';
    }

    return (
        '<table class="opt-table" aria-label="USI options"><thead><tr>' +
        '<th scope="col">Option</th>' +
        '<th scope="col">Value</th>' +
        '<th scope="col">Default</th>' +
        '<th scope="col">Source</th>' +
        '</tr></thead><tbody>' +
        rows +
        '</tbody></table>'
    );
}

export function renderOptionsDetail(name: string, meta: EngineMetaDetail | undefined, deps: OptionsRenderDeps): string {
    const optionsHtml = renderOptionsTable(meta, deps);
    return `
        <div class="details-pane theme-options engines-options-pane" data-engine="${escapeHtml(name)}">
            <div class="title">${escapeHtml(name)} — USI Options</div>
            <div class="engine-options-container" data-engine="${escapeHtml(name)}" data-expanded="0">
                ${optionsHtml}
                <div class="options-actions mt-2">
                    <button type="button" class="btn btn-link js-engines-full-options" data-engine="${escapeHtml(name)}">
                        Show full USI options
                    </button>
                </div>
                <div class="engine-options-full hidden" aria-live="polite"></div>
            </div>
        </div>
    `;
}

export function renderInstanceDetail(name: string, instanceId: string): string {
    return `
        <div class="details-pane theme-instance engines-instance-pane" data-instance="${escapeHtml(instanceId)}">
            <div class="title">${escapeHtml(name)} — Instance</div>
            <div class="engine-instance-body" data-state="loading">
                <div class="subtle">Loading instance details…</div>
            </div>
            <div class="subtle mt-2">Manage instances from the Instances tab.</div>
        </div>
    `;
}

export function renderTimeControlDetail(name: string, spec: string | undefined, _deps?: TimeControlRenderDeps): string {
    const safeSpec = typeof spec === 'string' ? spec : '';
    if (!safeSpec) {
        return `
            <div class="details-pane theme-time">
                <div class="subtle">No time control configured for this engine.</div>
            </div>
        `;
    }

    const parseSpec = parseTimeControlSpec;
    const formatNodesDetail = formatNodesCountDetail;

    let rows: Array<[string, string]> = [];

    try {
        const tc = parseSpec(safeSpec) as {
            mode?: string;
            fixedMs?: number;
            initial?: number;
            increment?: number;
            byoyomi?: number;
            nodes?: number;
            depth?: number;
            allowTimeout?: boolean;
            marginMs?: number;
        };
        const timeRows: Array<[string, string]> = [];
        if (tc.mode === 'fixed' && tc.fixedMs && tc.fixedMs > 0) {
            timeRows.push(['Fixed time', formatDuration(tc.fixedMs)]);
        } else {
            if (tc.initial && tc.initial > 0) timeRows.push(['Main time', formatDuration(tc.initial)]);
            if (tc.increment && tc.increment > 0) timeRows.push(['Increment', formatDuration(tc.increment)]);
            if (tc.byoyomi && tc.byoyomi > 0) timeRows.push(['Byoyomi', formatDuration(tc.byoyomi)]);
        }
        const searchRows: Array<[string, string]> = [];
        if (tc.depth != null) searchRows.push(['Depth limit', `${tc.depth}`]);
        if (tc.nodes != null) searchRows.push(['Nodes limit', `${formatNodesDetail(Number(tc.nodes))} nodes`]);
        const extraRows: Array<[string, string]> = [];
        if (tc.allowTimeout) extraRows.push(['Allow timeout', 'Yes']);
        if (tc.marginMs != null && tc.marginMs > 0) {
            extraRows.push(['Margin', formatDuration(tc.marginMs)]);
        }
        rows = [...timeRows, ...searchRows, ...extraRows];
    } catch (error) {
        _deps?.warnSoftFailure?.('Failed to parse time control spec for engines tab', error);
    }

    if (!rows.length) {
        rows.push(['Spec', 'No detailed limits available']);
    }

    const rowsHtml = rows
        .map(([label, value]) => `<tr><th scope="row">${escapeHtml(label)}</th><td>${escapeHtml(value)}</td></tr>`)
        .join('');

    return `
        <div class="details-pane time-control-details theme-time" data-spec="${escapeHtml(safeSpec)}">
            <div class="title">${escapeHtml(name)} — Time Control</div>
            <table class="opt-table w-full table-auto">
                <tbody>${rowsHtml}</tbody>
            </table>
            <div class="subtle mt-2">Raw spec: <code>${escapeHtml(safeSpec)}</code></div>
        </div>
    `;
}

export function renderInstanceInfo(data: EngineInstanceDetail | null | undefined): string {
    if (!data || typeof data !== 'object') {
        return '<div class="subtle">Instance information unavailable.</div>';
    }

    const rows: Array<[string, string]> = [];
    const config = (data.config ?? {}) as InstanceConfig;
    const metrics = (data.metrics ?? {}) as InstanceMetrics;

    if (data.id) rows.push(['Name', String(data.id)]);
    if (data.type) rows.push(['Type', String(data.type)]);
    if (config.host) rows.push(['Host', String(config.host)]);
    if (config.user) rows.push(['User', String(config.user)]);
    if (config.port != null) rows.push(['Port', String(config.port)]);
    if (config.project_root) rows.push(['Project Root', String(config.project_root)]);
    if (config.slots != null) rows.push(['Configured Slots', String(config.slots)]);
    if (data.available_slots != null) rows.push(['Available Slots', String(data.available_slots)]);
    if (typeof data.can_accept_job === 'boolean') rows.push(['Accepting Jobs', data.can_accept_job ? 'Yes' : 'No']);
    if (Array.isArray(config.tags) && config.tags.length) rows.push(['Tags', config.tags.join(', ')]);
    if (metrics.reachable !== undefined) rows.push(['Reachable', metrics.reachable ? 'Yes' : 'No']);
    if (metrics.resp_time_ms !== undefined) rows.push(['Response Time', `${metrics.resp_time_ms} ms`]);

    if (!rows.length) {
        return '<div class="subtle">No metadata recorded for this instance.</div>';
    }

    const html = rows
        .map(([label, value]) => `<tr><th scope="row">${escapeHtml(label)}</th><td>${escapeHtml(value)}</td></tr>`)
        .join('');
    return `<table class="opt-table w-full table-auto"><tbody>${html}</tbody></table>`;
}
