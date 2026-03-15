import { formatDurationMs, formatPercentage as formatPercentageShared } from '@/modules/shared/utils/format';
import type {
    AdjudicationConfig,
    InitialPositionsInfo,
    RulesSummary,
    SpsaAlgorithmConfig,
} from '@/modules/rules/types/internal';
import type { NormalizedTournamentSummary } from '@/modules/tournament/types';
import type { JsonObject } from '@/types/shared';

export type ParseTimeControlSpec = (spec: string) => unknown;

function isNormalizedTournamentSummary(value: unknown): value is NormalizedTournamentSummary {
    if (!value || typeof value !== 'object' || Array.isArray(value)) {
        return false;
    }
    const record = value as Record<string, unknown>;
    return 'raw' in record && 'engineTimeControls' in record;
}

function coerceString(value: unknown): string | null {
    if (typeof value === 'string') {
        const trimmed = value.trim();
        return trimmed.length ? trimmed : null;
    }
    if (value == null) {
        return null;
    }
    const str = String(value).trim();
    return str.length ? str : null;
}

function normalizeStringRecord(value: unknown): Record<string, string> {
    if (!value || typeof value !== 'object' || Array.isArray(value)) {
        return {};
    }
    const record: Record<string, string> = {};
    for (const [key, raw] of Object.entries(value as JsonObject)) {
        const normalizedKey = coerceString(key);
        if (!normalizedKey) continue;
        const normalizedValue = coerceString(raw);
        if (normalizedValue) {
            record[normalizedKey] = normalizedValue;
        }
    }
    return record;
}

function extractEngines(value: unknown): string[] {
    if (!value || typeof value !== 'object') {
        return [];
    }
    const enginesRaw = (value as JsonObject).engines;
    if (!Array.isArray(enginesRaw)) {
        return [];
    }
    const engines: string[] = [];
    for (const entry of enginesRaw) {
        const name = coerceString(entry);
        if (name) engines.push(name);
    }
    return engines;
}

export function getRulesSummary(source: NormalizedTournamentSummary | JsonObject | null | undefined): RulesSummary {
    if (!source) {
        throw new Error('Rules summary requires summary payload');
    }

    const isNormalized = isNormalizedTournamentSummary(source);

    const raw: RulesSummary = isNormalized
        ? ((source.raw as RulesSummary | undefined) ?? ({} as RulesSummary))
        : ({ ...(source as JsonObject) } as RulesSummary);

    const summary: RulesSummary = { ...raw };

    if (summary.defaultTimeControl != null) {
        const coerced = coerceString(summary.defaultTimeControl);
        if (coerced) {
            summary.defaultTimeControl = coerced;
        }
    }

    const engines = isNormalized ? source.engines.slice() : extractEngines(source);
    if (!summary.engines || !Array.isArray(summary.engines) || summary.engines.length === 0) {
        summary.engines = engines;
    }

    const mergedTimeControls = {
        ...(summary.engineTimeControls ?? {}),
        ...(isNormalized
            ? source.engineTimeControls
            : normalizeStringRecord((source as JsonObject).engineTimeControls)),
    };
    summary.engineTimeControls = mergedTimeControls;

    const defaultTimeControl = isNormalized
        ? coerceString(source.defaultTimeControl)
        : coerceString((source as JsonObject).defaultTimeControl);
    if (!summary.defaultTimeControl && defaultTimeControl) {
        summary.defaultTimeControl = defaultTimeControl;
    }

    const sprtRaw = isNormalized
        ? source.sprt
            ? source.sprt.raw
            : null
        : ((source as JsonObject).sprt as JsonObject | null | undefined);
    if (!summary.sprt && sprtRaw) {
        summary.sprt = sprtRaw;
    }

    const tournamentType = isNormalized
        ? coerceString(source.tournamentType)
        : coerceString((source as JsonObject).tournamentType);
    if (!summary.tournamentType && tournamentType) {
        summary.tournamentType = tournamentType;
    }

    const spsaConfigRaw = isNormalized
        ? (source.raw as Record<string, unknown> | undefined)?.spsaConfig
        : (source as JsonObject).spsaConfig;
    if (!summary.spsaConfig && spsaConfigRaw && typeof spsaConfigRaw === 'object') {
        summary.spsaConfig = spsaConfigRaw as SpsaAlgorithmConfig;
    }

    return summary;
}

function formatDuration(ms: unknown): string {
    return formatDurationMs(ms);
}

export function formatPercentage(value: unknown): string {
    return formatPercentageShared(value);
}

export function formatTimeControl(spec: unknown, parseSpec: ParseTimeControlSpec | undefined): string {
    if (!spec) return '-';

    let parsed: JsonObject | null = null;
    if (parseSpec) {
        try {
            const maybeParsed = parseSpec(String(spec ?? ''));
            if (maybeParsed && typeof maybeParsed === 'object' && !Array.isArray(maybeParsed)) {
                parsed = maybeParsed as JsonObject;
            }
        } catch (error) {
            const errorMessage = error instanceof Error ? error.message : String(error);
            throw new Error(`Failed to parse time control spec: ${errorMessage}`, { cause: error });
        }
    }

    // If parseSpec was not provided or parsing failed, return raw spec
    if (!parsed) {
        return String(spec);
    }

    const mode = String(parsed.mode ?? '');
    const parts: string[] = [];

    const fixedMs = parsed.fixedMs as number | undefined;
    const initial = parsed.initial as number | undefined;
    const increment = parsed.increment as number | undefined;
    const byoyomi = parsed.byoyomi as number | undefined;
    const nodes = parsed.nodes as number | undefined;
    const depth = parsed.depth as number | undefined;
    const allowTimeout = parsed.allowTimeout as boolean | undefined;

    if (mode === 'fixed' && typeof fixedMs === 'number') {
        parts.push(`Fixed ${formatDuration(fixedMs)}`);
    } else if (mode === 'incremental') {
        if (typeof initial === 'number') parts.push(`Initial ${formatDuration(initial)}`);
        if (typeof increment === 'number') parts.push(`Inc ${formatDuration(increment)}`);
        if (typeof byoyomi === 'number') parts.push(`Byo ${formatDuration(byoyomi)}`);
    }

    if (nodes != null) {
        const nodesValue = Number(nodes);
        parts.push(`Nodes ${Number.isFinite(nodesValue) ? nodesValue.toLocaleString() : nodes}`);
    }
    if (depth != null) {
        parts.push(`Depth ${depth}`);
    }
    if (allowTimeout) {
        parts.push('Allow timeout');
    }

    return parts.length ? parts.join(' · ') : String(spec);
}

export function normalizeAdjudication(summary: RulesSummary): AdjudicationConfig | null {
    const candidates: unknown[] = [];

    if (summary.rules && typeof summary.rules === 'object') {
        const rules = summary.rules;
        if (rules.adjudication != null) {
            candidates.push(rules.adjudication);
        }
    }

    if (summary.adjudication != null) {
        candidates.push(summary.adjudication);
    }

    const tournamentConfig = summary.tournamentConfig;
    if (tournamentConfig && typeof tournamentConfig === 'object') {
        const configRecord = tournamentConfig as JsonObject;
        if (configRecord.adjudication != null) {
            candidates.push(configRecord.adjudication);
        }
    }

    for (const candidate of candidates) {
        if (candidate && typeof candidate === 'object') {
            return candidate as AdjudicationConfig;
        }
    }

    return null;
}

export function normalizeInitialPositions(summary: RulesSummary): InitialPositionsInfo | null {
    const ipRaw = summary.rules?.initial_positions ?? summary.initialPositions;
    if (!ipRaw || typeof ipRaw !== 'object') return null;

    const ip = ipRaw as JsonObject;

    const type = (() => {
        if (typeof ip.type === 'string' && ip.type.trim()) return ip.type;
        throw new Error('normalizeInitialPositions: initial_positions.type is required and must be a non-empty string');
    })();

    const source = ip.source != null ? String(ip.source) : null;

    const flipPolicy = (() => {
        if (typeof ip.flip_policy === 'string') return ip.flip_policy;
        if (typeof summary.flipPolicy === 'string') return summary.flipPolicy;
        return null;
    })();

    const randomize = typeof ip.randomize === 'boolean' ? ip.randomize : Boolean(ip.randomize);

    const count = (() => {
        if (ip.count == null) return null;
        const numeric = Number(ip.count);
        return Number.isFinite(numeric) ? numeric : null;
    })();

    return {
        type,
        source,
        flip_policy: flipPolicy,
        randomize,
        count,
    };
}

export function getRepetitionValue(summary: RulesSummary): number | null {
    const valueRaw = summary.rules?.repetition_occurrences_to_draw ?? summary.repetitionOccurrencesToDraw;
    const value = Number(valueRaw);
    return Number.isFinite(value) && value > 0 ? value : null;
}
