import { escapeHtml } from '@/modules/shared/utils/html';
import type { ParameterInsight } from './parameterShared';
import { formatParameterValue } from './parameterShared';

export type ParameterSortKey = 'name' | 'initial' | 'current' | 'deltaRatio' | 'gradientEffect' | 'driftRatio';
export type ParameterSortDirection = 'asc' | 'desc';

export type ParameterSortState = {
    key: ParameterSortKey;
    direction: ParameterSortDirection;
};

type ParameterTableColumn = {
    label: string;
    sortKey?: ParameterSortKey;
    description?: string;
};

const PARAMETER_TABLE_COLUMNS: readonly ParameterTableColumn[] = [
    { label: 'Parameter', sortKey: 'name' },
    { label: 'Initial', sortKey: 'initial', description: 'Baseline value before SPSA updates.' },
    { label: 'Current', sortKey: 'current', description: 'Most recent value assigned by SPSA.' },
    {
        label: 'Δ normalized',
        sortKey: 'deltaRatio',
        description: 'Change from baseline divided by the effective parameter range.',
    },
    {
        label: 'Gradient impact',
        sortKey: 'gradientEffect',
        description: 'Projected objective change from a +1% range perturbation, based on the latest gradient.',
    },
    {
        label: 'Drift coverage',
        sortKey: 'driftRatio',
        description: 'Span of observed parameter movement relative to the effective range.',
    },
];

export const DEFAULT_PARAMETER_SORT_STATE: ParameterSortState = { key: 'name', direction: 'asc' };

type MetricFormatOptions = {
    signed?: boolean;
};

export function isParameterSortKey(value: string | undefined): value is ParameterSortKey {
    return (
        value === 'name' ||
        value === 'initial' ||
        value === 'current' ||
        value === 'deltaRatio' ||
        value === 'gradientEffect' ||
        value === 'driftRatio'
    );
}

export function applyParameterSort(previous: ParameterSortState, key: ParameterSortKey): ParameterSortState {
    if (previous.key === key) {
        return {
            key,
            direction: previous.direction === 'asc' ? 'desc' : 'asc',
        };
    }
    return { key, direction: 'asc' };
}

export function sortParameterInsights(
    entries: readonly ParameterInsight[],
    sortState: ParameterSortState,
): ParameterInsight[] {
    if (!entries.length) {
        return [];
    }
    const sortable = [...entries];
    sortable.sort((a, b) => {
        const primary = compareParameterEntries(a, b, sortState.key);
        if (primary !== 0) {
            return sortState.direction === 'asc' ? primary : -primary;
        }
        return a.name.localeCompare(b.name);
    });
    return sortable;
}

function compareParameterEntries(a: ParameterInsight, b: ParameterInsight, key: ParameterSortKey): number {
    switch (key) {
        case 'initial':
            return compareNumericValues(a.initialValue, b.initialValue);
        case 'current':
            return compareNumericValues(a.currentValue, b.currentValue);
        case 'deltaRatio':
            return compareNumericValues(a.deltaRatio, b.deltaRatio);
        case 'gradientEffect':
            return compareNumericValues(a.gradientEffect, b.gradientEffect);
        case 'driftRatio':
            return compareNumericValues(a.driftRatio, b.driftRatio);
        default:
            return a.name.localeCompare(b.name);
    }
}

function compareNumericValues(a: number | null, b: number | null): number {
    const aValid = typeof a === 'number' && Number.isFinite(a);
    const bValid = typeof b === 'number' && Number.isFinite(b);
    if (!aValid && !bValid) return 0;
    if (!aValid) return 1;
    if (!bValid) return -1;
    if (a === b) return 0;
    return a < (b as number) ? -1 : 1;
}

export function buildParameterTable(insights: ParameterInsight[], sortState: ParameterSortState): string {
    if (!insights.length) {
        return '<p class="muted">Insufficient data for parameter analysis yet.</p>';
    }

    const rows = insights.map((insight) => renderParameterRow(insight)).join('');
    const header = PARAMETER_TABLE_COLUMNS.map((column) => renderParameterHeaderCell(column, sortState)).join('');

    return `
        <table class="table-simple parameter-table">
            <thead>
                <tr>${header}</tr>
            </thead>
            <tbody>
                ${rows}
            </tbody>
        </table>
    `;
}

function renderParameterHeaderCell(column: ParameterTableColumn, sortState: ParameterSortState): string {
    const labelText = column.label;
    const label = escapeHtml(labelText);
    const description = column.description ?? '';

    if (!column.sortKey) {
        if (description) {
            return `<th scope="col"><span title="${escapeHtml(description)}">${label}</span></th>`;
        }
        return `<th scope="col">${label}</th>`;
    }
    const isActive = column.sortKey === sortState.key;
    const ariaSort = isActive ? (sortState.direction === 'asc' ? 'ascending' : 'descending') : 'none';
    const indicator = isActive ? (sortState.direction === 'asc' ? '↑' : '↓') : '↕';
    const tooltipText = description ? `${description} Click to sort.` : `Click to sort by ${labelText}.`;
    return `
        <th scope="col" aria-sort="${ariaSort}">
            <button type="button" class="table-sort-button${isActive ? ' is-active' : ''}" data-sort-key="${column.sortKey}" title="${escapeHtml(tooltipText)}">
                <span>${label}</span>
                <span class="sort-indicator" aria-hidden="true">${indicator}</span>
            </button>
        </th>
    `;
}

type MetricLabel = {
    label: string;
    className: string;
};

function formatMetricValue(value: number | null, options: MetricFormatOptions = {}): MetricLabel {
    if (value === null || !Number.isFinite(value)) {
        return { label: 'n/a', className: 'metric-neutral' };
    }
    const rounded = value.toFixed(3);
    const signPrefix = options.signed ? (value > 0 ? '+' : '') : '';
    const label = `${signPrefix}${rounded}`;
    if (Math.abs(value) < 1e-6) {
        return { label, className: 'metric-neutral' };
    }
    if (value > 0) {
        return { label, className: 'metric-positive' };
    }
    return { label, className: 'metric-negative' };
}

function formatRatioMetric(value: number | null, options: MetricFormatOptions = {}): MetricLabel {
    if (value === null || !Number.isFinite(value)) {
        return { label: 'n/a', className: 'metric-neutral' };
    }
    const percentage = value * 100;
    const absPercentage = Math.abs(percentage);
    const decimals =
        absPercentage >= 10 ? 1 : absPercentage >= 1 ? 2 : absPercentage >= 0.01 ? 3 : absPercentage >= 0.0001 ? 4 : 5;
    const rounded = percentage.toFixed(decimals);
    const signPrefix = options.signed ? (percentage > 0 ? '+' : '') : '';
    const label = `${signPrefix}${rounded}%`;
    if (absPercentage < 0.05) {
        return { label, className: 'metric-neutral' };
    }
    if (percentage > 0) {
        return { label, className: 'metric-positive' };
    }
    return { label, className: 'metric-negative' };
}

function renderStatusChip(status: ParameterInsight['status']): string {
    void status;
    return '';
}

function renderParameterRow(insight: ParameterInsight): string {
    const deltaMetric = formatRatioMetric(insight.deltaRatio, { signed: true });
    const gradientMetric = formatMetricValue(insight.gradientEffect, { signed: true });
    const driftMetric = formatRatioMetric(insight.driftRatio);
    const statusChip = renderStatusChip(insight.status);

    return `
        <tr data-parameter-row="${escapeHtml(insight.name)}">
            <td>
                <div class="parameter-name-content">
                    <span class="chevron">▶</span>
                    <span class="parameter-name-text">${escapeHtml(insight.name)}</span>
                </div>
                ${statusChip}
            </td>
            <td>${formatParameterValue(insight.initialValue)}</td>
            <td>${formatParameterValue(insight.currentValue)}</td>
            <td><span class="${deltaMetric.className}">${deltaMetric.label}</span></td>
            <td><span class="${gradientMetric.className}">${gradientMetric.label}</span></td>
            <td><span class="${driftMetric.className}">${driftMetric.label}</span></td>
        </tr>
    `;
}
