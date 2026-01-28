import type {
    NormalizedTournamentEngineMeta,
    ParsedTimeControlSpec,
    StandingsSortDirection,
    StandingsSortKey,
} from '@/modules/tournament/types';
import { resolveOptionSourceLabel } from '@/modules/shared/utils/engineOptionSources';
import type { JsonObject } from '@/types/shared';
import type { BaseStandingsRow } from '@/modules/shared/types/standings';

export interface StandingsRowView extends BaseStandingsRow {
    wins: number;
    draws: number;
    losses: number;
    score: number;
}

export interface StandingsTableRenderContext {
    rows: readonly StandingsRowView[];
    previousOrder: readonly string[];
    anchorName: string | null;
    usingBTD: boolean;
    ciMap: ReadonlyMap<string, number>;
    sortState: {
        key: StandingsSortKey | null;
        direction: StandingsSortDirection | null;
    };
    escapeHtml(value: unknown): string;
    formatRatingDisplay(rating: number, isAnchor: boolean): string;
    engineTitle: string;
    matchupTitle: string;
}

type OptionEntry = {
    current?: unknown;
    default?: unknown;
    min?: unknown;
    max?: unknown;
    var?: unknown;
    [key: string]: unknown;
};

export function renderStandingsTableMarkup({
    rows,
    previousOrder: _previousOrder,
    anchorName,
    usingBTD,
    ciMap,
    sortState,
    escapeHtml,
    formatRatingDisplay,
    engineTitle,
    matchupTitle,
}: StandingsTableRenderContext): { headerHtml: string; rowsHtml: string } {
    const buildHeaderCell = (key: StandingsSortKey, label: string, extraClass = ''): string => {
        const activeSort = sortState.key === key && sortState.direction;
        const direction = activeSort ? sortState.direction : 'none';
        const ariaSort = direction === 'asc' ? 'ascending' : direction === 'desc' ? 'descending' : 'none';
        const cls = ['sortable', extraClass].filter(Boolean).join(' ');
        return `<th data-sort-key="${key}" aria-sort="${ariaSort}" data-sort-direction="${direction}" class="${cls}">${label}</th>`;
    };

    const rowsHtml = rows
        .map((row, idx) => {
            const key = row.name;
            const isAnchor = usingBTD && anchorName === key;
            const ratingDisplay = formatRatingDisplay(row.rating, isAnchor);
            const scoreValue = Number.isFinite(row.score) ? row.score.toFixed(3) : '-';
            const placement = idx + 1;
            const placementClass = placement <= 3 ? `placement placement-${placement}` : 'placement';
            const placementCellClass = `${placementClass} standings-rank`;
            const rowClass = 'standings-row';

            const buildMatchCell = (value: string, className: string): string =>
                `<td class="${className} numeric group-stats" data-action="matchup" role="button" tabindex="0" title="${matchupTitle}">${escapeHtml(value)}</td>`;

            const placementCell = `<td class="${placementCellClass} group-engine">${placement}</td>`;
            const nameCell = `<td class="standings-name group-engine" data-action="options" role="button" tabindex="0" title="${engineTitle}">${escapeHtml(key)}</td>`;
            const gamesCell = buildMatchCell(String(row.games), 'standings-games');
            const winsCell = buildMatchCell(String(row.wins), 'standings-wins');
            const drawsCell = buildMatchCell(String(row.draws), 'standings-draws');
            const lossesCell = buildMatchCell(String(row.losses), 'standings-losses');
            const winrateCell = buildMatchCell(scoreValue, 'standings-winrate');
            const ratingLabel = escapeHtml(ratingDisplay);
            const anchorBadge =
                anchorName && anchorName === key
                    ? '<span class="anchor-badge" title="Anchor (reference)">⚓</span>'
                    : '';
            const ratingExtra = ciMap.has(key)
                ? `<span class="rating-ci">±${Math.round(Number(ciMap.get(key)))}</span>`
                : '';
            const ratingSegments = [ratingLabel, ratingExtra, anchorBadge].filter(Boolean).join('');
            const ratingCell = `<td class="standings-rating group-stats rating-left" data-action="matchup" role="button" tabindex="0" title="${matchupTitle}">${ratingSegments}</td>`;

            return (
                `<tr class="${rowClass}" data-key="${escapeHtml(key)}">` +
                placementCell +
                nameCell +
                gamesCell +
                winsCell +
                drawsCell +
                lossesCell +
                winrateCell +
                ratingCell +
                `</tr>`
            );
        })
        .join('');

    const headerHtml =
        '<tr>' +
        '<th class="placement group-engine standings-rank">#</th>' +
        buildHeaderCell('engine', 'Engine', 'standings-engine group-engine') +
        buildHeaderCell('games', 'Games', 'numeric group-stats') +
        buildHeaderCell('wins', 'Wins', 'numeric group-stats') +
        buildHeaderCell('draws', 'Draws', 'numeric group-stats') +
        buildHeaderCell('losses', 'Losses', 'numeric group-stats') +
        buildHeaderCell('winrate', 'Win Ratio', 'numeric group-stats') +
        buildHeaderCell('rating', 'Rating', 'numeric group-stats') +
        '</tr>';

    return { headerHtml, rowsHtml };
}

export interface EngineOptionsPaneParams {
    engineName: string;
    meta: NormalizedTournamentEngineMeta | undefined;
    escapeHtml(value: unknown): string;
    formatOptionValue(value: unknown): string;
}

export function renderEngineOptionsPane({
    engineName,
    meta,
    escapeHtml,
    formatOptionValue,
}: EngineOptionsPaneParams): string {
    if (!meta) {
        throw new Error(`Missing normalized engine metadata for ${engineName}`);
    }

    const enginePath = typeof meta.enginePath === 'string' && meta.enginePath ? meta.enginePath : '-';
    const overrides = meta.merged_options ?? ({} as JsonObject);
    const resolved = meta.resolved_options ?? ({} as JsonObject);
    const sources = meta.option_sources ?? {};
    const sourceDetails = meta.option_sources_details ?? {};
    const runtime = (meta.runtime_usi_options ?? {}) as Record<string, OptionEntry>;
    const keys = Object.keys(overrides).sort();
    const runtimeInfo = (meta.runtime_engine_info as JsonObject | null | undefined) ?? {};
    const infoLines: string[] = [];
    if (runtimeInfo.name) infoLines.push(`name: ${escapeHtml(runtimeInfo.name)}`);
    if (runtimeInfo.author) infoLines.push(`author: ${escapeHtml(runtimeInfo.author)}`);

    let overridesHtml = '<div class="subtle">No overrides configured.</div>';
    if (keys.length) {
        overridesHtml =
            '<table class="opt-table w-full">' +
            '<thead><tr><th>Option</th><th>Value</th><th>Default</th><th class="subtle">Source</th></tr></thead><tbody>';
        keys.forEach((key) => {
            const rawValue = overrides[key];
            const resolvedValue = Object.hasOwn(resolved, key) ? resolved[key] : rawValue;
            const runtimeEntry = runtime?.[key];
            const currentValue =
                runtimeEntry && runtimeEntry.current !== undefined && runtimeEntry.current !== null
                    ? runtimeEntry.current
                    : resolvedValue;
            const defaultValue =
                runtimeEntry && runtimeEntry.default !== undefined && runtimeEntry.default !== null
                    ? runtimeEntry.default
                    : null;
            const valueStr = escapeHtml(formatOptionValue(resolvedValue ?? currentValue));
            const defaultStr =
                defaultValue !== null ? escapeHtml(formatOptionValue(defaultValue)) : '<span class="subtle">-</span>';
            const sourceStr = escapeHtml(resolveOptionSourceLabel(key, sources, sourceDetails));
            const highlight =
                defaultValue !== null && formatOptionValue(defaultValue) !== formatOptionValue(currentValue);
            overridesHtml += `<tr${highlight ? ' class="option-diff"' : ''}>`;
            overridesHtml += `<td>${escapeHtml(key)}</td>`;
            overridesHtml += `<td>${valueStr}</td>`;
            overridesHtml += `<td>${defaultStr}</td>`;
            overridesHtml += `<td class="subtle">${sourceStr}</td>`;
            overridesHtml += '</tr>';
        });
        overridesHtml += '</tbody></table>';
    }

    return `
        <div class="engine-options-container" data-engine="${escapeHtml(engineName)}" data-expanded="0">
            <div class="path"><b>engine_path:</b> <span class="path-value">${escapeHtml(enginePath)}</span></div>
            ${infoLines.length ? `<div class="engine-info">${infoLines.join('<br />')}</div>` : ''}
            <div class="mt-2">${overridesHtml}</div>
            <div class="options-actions mt-2">
                <button type="button" class="js-toggle-full-options" data-action="toggle_full_options" data-engine="${escapeHtml(engineName)}">Show full USI options</button>
            </div>
            <div class="engine-options-full hidden mt-2" aria-live="polite"></div>
        </div>
    `;
}

export interface TimeControlDetailsParams {
    engineName: string;
    spec: string | null | undefined;
    escapeHtml(value: unknown): string;
    parseTimeControlSpec(spec: string): ParsedTimeControlSpec;
    formatDuration(value: unknown): string;
    formatNodesCountDetail(value: unknown): string;
}

export function renderTimeControlDetails({
    engineName,
    spec,
    escapeHtml,
    parseTimeControlSpec,
    formatDuration,
    formatNodesCountDetail,
}: TimeControlDetailsParams): string {
    const normalizedSpec = spec ? String(spec) : '';
    if (!normalizedSpec) {
        return '<div class="details-pane theme-time"><div class="subtle">No time control configured for this engine.</div></div>';
    }
    const tc = parseTimeControlSpec(normalizedSpec);
    const timeRows: Array<[string, string]> = [];
    if (tc.mode === 'fixed' && tc.fixedMs > 0) {
        timeRows.push(['Fixed time', formatDuration(tc.fixedMs)]);
    } else {
        if (tc.initial > 0) timeRows.push(['Main time', formatDuration(tc.initial)]);
        if (tc.increment > 0) timeRows.push(['Increment', formatDuration(tc.increment)]);
        if (tc.byoyomi > 0) timeRows.push(['Byoyomi', formatDuration(tc.byoyomi)]);
    }
    const searchRows: Array<[string, string]> = [];
    if (tc.depth != null) searchRows.push(['Depth limit', `${tc.depth}`]);
    if (tc.nodes != null) searchRows.push(['Nodes limit', `${formatNodesCountDetail(tc.nodes)} nodes`]);

    const hasTimeBudget =
        (tc.mode === 'fixed' && tc.fixedMs > 0) || tc.initial > 0 || tc.increment > 0 || tc.byoyomi > 0;

    const extraRows: Array<[string, string]> = [];
    if (tc.allowTimeout) extraRows.push(['Allow timeout', 'Yes']);
    if (hasTimeBudget && tc.marginMs != null && tc.marginMs > 0) {
        extraRows.push(['Margin', formatDuration(tc.marginMs)]);
    }

    const rows = [...timeRows, ...searchRows, ...extraRows];
    if (!rows.length) {
        rows.push(['Spec', 'No limits provided']);
    }

    const rowsHtml = rows
        .map(([label, value]) => `<tr><th scope="row">${escapeHtml(label)}</th><td>${escapeHtml(value)}</td></tr>`)
        .join('');

    const rawSpec = escapeHtml(normalizedSpec);
    return `
        <div class="details-pane time-control-details theme-time">
            <div class="title">${escapeHtml(engineName)} — Time Control</div>
            <table class="opt-table w-full table-auto">
                <tbody>${rowsHtml}</tbody>
            </table>
            <div class="subtle mt-2">Raw spec: <code>${rawSpec}</code></div>
        </div>
    `;
}

export interface FullOptionsTableParams {
    payload: unknown;
    meta: NormalizedTournamentEngineMeta | undefined;
    escapeHtml(value: unknown): string;
    formatOptionValue(value: unknown): string;
}

export function renderFullOptionsTable({
    payload,
    meta,
    escapeHtml,
    formatOptionValue,
}: FullOptionsTableParams): string {
    if (!meta) {
        throw new Error('renderFullOptionsTable requires normalized engine metadata');
    }

    const rawOptions =
        payload &&
        typeof payload === 'object' &&
        !Array.isArray(payload) &&
        (payload as JsonObject).options &&
        typeof (payload as JsonObject).options === 'object'
            ? ((payload as JsonObject).options as JsonObject)
            : {};

    const options: Record<string, OptionEntry> = Object.fromEntries(
        Object.entries(rawOptions).map(([key, value]) => [
            key,
            value && typeof value === 'object' ? (value as OptionEntry) : {},
        ]),
    );
    const overrideLookup = meta?.merged_options ?? {};
    const resolvedOverrides = meta?.resolved_options ?? {};
    const overrideKeys = new Set(Object.keys(overrideLookup));
    const keys = Object.keys(options).sort();
    if (!keys.length) {
        return '<div class="subtle">No USI option metadata available.</div>';
    }

    let html = '<table class="opt-table w-full">';
    html += '<thead><tr><th>Option</th><th>Value</th></tr></thead><tbody>';
    keys.forEach((key) => {
        if (overrideKeys.has(key)) return;
        const entry = options[key] || {};
        let current = entry.current !== undefined && entry.current !== null ? entry.current : entry.default;
        if (Object.hasOwn(resolvedOverrides, key)) {
            current = resolvedOverrides[key];
        }
        const valueStr = formatOptionValue(current);
        const rangeStr =
            entry.min !== undefined || entry.max !== undefined || entry.var
                ? formatOptionRange(entry, formatOptionValue)
                : '';
        const escapedKey = escapeHtml(key);
        const valueHtml = `${escapeHtml(valueStr)}${
            rangeStr ? ` <span class="subtle">(${escapeHtml(rangeStr)})</span>` : ''
        }`;
        html += `<tr><td>${escapedKey}</td><td>${valueHtml}</td></tr>`;
    });
    html += '</tbody></table>';
    return html;
}

function formatOptionRange(entry: OptionEntry, formatOptionValue: (value: unknown) => string): string {
    if (!entry || typeof entry !== 'object') return '-';
    const choices = entry.var;
    if (Array.isArray(choices) && choices.length) {
        return choices.map((item) => formatOptionValue(item)).join(', ');
    }
    const hasMin = entry.min !== undefined && entry.min !== null;
    const hasMax = entry.max !== undefined && entry.max !== null;
    if (hasMin || hasMax) {
        const minStr = hasMin ? formatOptionValue(entry.min) : '';
        const maxStr = hasMax ? formatOptionValue(entry.max) : '';
        if (hasMin && hasMax) return `${minStr} - ${maxStr}`;
        if (hasMin) return `>= ${minStr}`;
        return `<= ${maxStr}`;
    }
    return '-';
}
