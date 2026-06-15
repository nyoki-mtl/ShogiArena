import type {
    NormalizedTournamentEngineMeta,
    StandingsSortDirection,
    StandingsSortKey,
} from '@/modules/tournament/types';
import type { EngineMetaDetail } from '@/modules/engines/types/internal';
import { resolveOptionSourceLabel } from '@/modules/shared/utils/engine-option-sources';
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
    meta: NormalizedTournamentEngineMeta | EngineMetaDetail | undefined;
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

    const metaRecord = meta as Record<string, unknown>;
    const mergedOptionsRaw = metaRecord.merged_options;
    const resolvedOptionsRaw = metaRecord.resolved_options;
    const optionSourcesRaw = metaRecord.option_sources;
    const optionSourceDetailsRaw = metaRecord.option_sources_details;
    const runtimeOptionsRaw = metaRecord.runtime_usi_options;
    const runtimeInfoRaw = metaRecord.runtime_engine_info;
    const rawPayload = metaRecord.raw;

    const normalizedMeta: NormalizedTournamentEngineMeta = {
        name: typeof metaRecord.name === 'string' ? metaRecord.name : '',
        enginePath:
            typeof metaRecord.enginePath === 'string'
                ? metaRecord.enginePath
                : typeof metaRecord.engine_path === 'string'
                  ? metaRecord.engine_path
                  : null,
        merged_options:
            mergedOptionsRaw && typeof mergedOptionsRaw === 'object'
                ? (mergedOptionsRaw as JsonObject)
                : ({} as JsonObject),
        resolved_options:
            resolvedOptionsRaw && typeof resolvedOptionsRaw === 'object'
                ? (resolvedOptionsRaw as JsonObject)
                : ({} as JsonObject),
        option_sources:
            optionSourcesRaw && typeof optionSourcesRaw === 'object'
                ? (optionSourcesRaw as Record<string, string>)
                : {},
        option_sources_details:
            optionSourceDetailsRaw && typeof optionSourceDetailsRaw === 'object'
                ? (optionSourceDetailsRaw as Record<string, string>)
                : {},
        runtime_usi_options:
            runtimeOptionsRaw && typeof runtimeOptionsRaw === 'object'
                ? (runtimeOptionsRaw as Record<string, OptionEntry>)
                : {},
        runtime_engine_info:
            runtimeInfoRaw && typeof runtimeInfoRaw === 'object' ? (runtimeInfoRaw as JsonObject) : null,
        raw: rawPayload && typeof rawPayload === 'object' ? (rawPayload as JsonObject) : ({} as JsonObject),
    };

    const enginePath =
        typeof normalizedMeta.enginePath === 'string' && normalizedMeta.enginePath ? normalizedMeta.enginePath : '-';
    const overrides = normalizedMeta.merged_options ?? ({} as JsonObject);
    const resolved = normalizedMeta.resolved_options ?? ({} as JsonObject);
    const sources = normalizedMeta.option_sources ?? {};
    const sourceDetails = normalizedMeta.option_sources_details ?? {};
    const runtime = (normalizedMeta.runtime_usi_options ?? {}) as Record<string, OptionEntry>;
    const keys = Object.keys(overrides).sort();
    const runtimeInfo = (normalizedMeta.runtime_engine_info as JsonObject | null | undefined) ?? {};
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
            // Highlight against the same value that is actually displayed, otherwise the diff
            // marker can disagree with the shown value (display uses resolvedValue, the diff
            // check previously used runtime currentValue).
            const displayValue = resolvedValue ?? currentValue;
            const valueStr = escapeHtml(formatOptionValue(displayValue));
            const defaultStr =
                defaultValue !== null ? escapeHtml(formatOptionValue(defaultValue)) : '<span class="subtle">-</span>';
            const sourceStr = escapeHtml(resolveOptionSourceLabel(key, sources, sourceDetails));
            const highlight =
                defaultValue !== null && formatOptionValue(defaultValue) !== formatOptionValue(displayValue);
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

export interface FullOptionsTableParams {
    payload: unknown;
    meta: NormalizedTournamentEngineMeta | EngineMetaDetail | undefined;
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
