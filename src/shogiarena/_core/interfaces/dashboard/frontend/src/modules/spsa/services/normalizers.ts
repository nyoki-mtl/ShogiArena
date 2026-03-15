import type {
    NormalizedSpsaParameter,
    NormalizedSpsaParams,
    NormalizedSpsaSummary,
    NormalizedSpsaLtcSummary,
    NormalizedSpsaUpdateDetail,
    NormalizedSpsaUpdateEntry,
    NormalizedSpsaPerturbations,
    NormalizedSpsaLtcRegression,
    SpsaDetailInclude,
    SpsaDetailViewMode,
    SpsaDetailWindowMode,
    SpsaPhaseWdlEntry,
    SpsaParameterEntry,
    SpsaUpdateEntry,
    SpsaUpdateProgress,
} from '@/modules/spsa/types';
import type {
    NormalizedTournamentSummary,
    NormalizedTournamentEngineMeta,
    TournamentEngineMeta,
} from '@/modules/tournament/types';
import type { JsonObject, MutableJsonObject } from '@/types/shared';
import {
    SPSA_DETAIL_DEFAULT_WINDOW,
    SPSA_DETAIL_SUPPORTED_INCLUDES,
    SPSA_DETAIL_SUPPORTED_WINDOWS,
} from '@/modules/spsa/constants';
import { SpsaConsistencyError } from './consistency';
import {
    parseSpsaSummaryResponse,
    parseSpsaParamsResponse,
    parseSpsaUpdateEntries,
    parseSpsaUpdateDetailResponse,
} from '@/contracts/parsers/spsa';
import { isGameResultName } from '@/modules/shared/utils/gameResult';

const DETAIL_INCLUDE_SET = new Set<string>(SPSA_DETAIL_SUPPORTED_INCLUDES);
const DETAIL_WINDOW_SET = new Set<string>(SPSA_DETAIL_SUPPORTED_WINDOWS);

type LooseObject = MutableJsonObject;

function toNumber(value: unknown): number {
    if (typeof value === 'number' && Number.isFinite(value)) {
        return value;
    }
    const num = Number(value);
    return Number.isFinite(num) ? num : 0;
}

function toNullableNumber(value: unknown): number | null {
    if (value === null || value === undefined) {
        return null;
    }
    if (typeof value === 'number' && Number.isFinite(value)) {
        return value;
    }
    if (typeof value === 'string') {
        const trimmed = value.trim();
        if (!trimmed) {
            return null;
        }
        const numFromString = Number(trimmed);
        return Number.isFinite(numFromString) ? numFromString : null;
    }
    const num = Number(value);
    return Number.isFinite(num) ? num : null;
}

function toNullableTimestamp(value: unknown): number | null {
    if (typeof value === 'number' && Number.isFinite(value)) {
        return value;
    }
    if (typeof value === 'string') {
        const trimmed = value.trim();
        if (!trimmed) {
            return null;
        }
        const numeric = Number(trimmed);
        if (Number.isFinite(numeric)) {
            return numeric;
        }
        const parsed = Date.parse(trimmed);
        if (!Number.isNaN(parsed)) {
            return parsed;
        }
    }
    return null;
}

function coerceString(value: unknown): string | null {
    if (typeof value === 'string') {
        const trimmed = value.trim();
        return trimmed.length > 0 ? trimmed : null;
    }
    return null;
}

function coerceObject(value: unknown): LooseObject {
    if (value && typeof value === 'object' && !Array.isArray(value)) {
        return value as LooseObject;
    }
    return {};
}

function coerceNumericRecord(value: unknown): Record<string, number> {
    const source = coerceObject(value);
    const result: Record<string, number> = {};
    for (const [key, candidate] of Object.entries(source)) {
        const num = toNullableNumber(candidate);
        if (num !== null) {
            result[key] = num;
        }
    }
    return result;
}

function normalizeDetailView(value: unknown, hasMeta: boolean): SpsaDetailViewMode {
    if (typeof value === 'string') {
        const trimmed = value.trim().toLowerCase();
        if (trimmed === 'slim' || trimmed === 'full') {
            return trimmed;
        }
    }
    return hasMeta ? 'slim' : 'full';
}

function normalizeDetailWindow(value: unknown, view: SpsaDetailViewMode): SpsaDetailWindowMode | 'full' {
    if (view === 'full') {
        return 'full';
    }
    if (typeof value === 'string') {
        const trimmed = value.trim().toLowerCase();
        if (DETAIL_WINDOW_SET.has(trimmed)) {
            return trimmed as SpsaDetailWindowMode;
        }
    }
    return SPSA_DETAIL_DEFAULT_WINDOW;
}

/**
 * サーバから送られてくる detail includes を「緩く」正規化します。
 *
 * 未知の include 名や大文字・小文字違いは静かに捨てられます。
 */
function normalizeDetailIncludes(value: unknown): SpsaDetailInclude[] {
    const tokens: string[] = [];
    if (Array.isArray(value)) {
        for (const entry of value) {
            if (entry === null || entry === undefined) continue;
            tokens.push(String(entry));
        }
    } else if (typeof value === 'string') {
        for (const token of value.split(',')) {
            tokens.push(token);
        }
    }
    const seen = new Set<string>();
    const normalized: SpsaDetailInclude[] = [];
    for (const token of tokens) {
        const trimmed = token.trim().toLowerCase();
        if (!trimmed || seen.has(trimmed) || !DETAIL_INCLUDE_SET.has(trimmed)) {
            continue;
        }
        seen.add(trimmed);
        normalized.push(trimmed as SpsaDetailInclude);
    }
    return normalized;
}

export function normalizePhaseWdlMap(value: unknown): Record<string, SpsaPhaseWdlEntry> {
    if (!value || typeof value !== 'object') {
        return {};
    }
    const source = value as Record<string, unknown>;
    const result: Record<string, SpsaPhaseWdlEntry> = {};
    for (const [rawKey, rawStats] of Object.entries(source)) {
        const key = rawKey?.trim();
        if (!key) continue;
        const stats = coerceObject(rawStats);
        const wins = toNumber(stats.wins);
        const losses = toNumber(stats.losses);
        const draws = toNumber(stats.draws);
        result[key] = {
            wins: Math.max(0, wins),
            losses: Math.max(0, losses),
            draws: Math.max(0, draws),
        } satisfies SpsaPhaseWdlEntry;
    }
    return result;
}

function normalizeParameterEntries(entries: unknown): NormalizedSpsaParameter[] {
    if (!Array.isArray(entries)) return [];
    const normalized: NormalizedSpsaParameter[] = [];
    for (const entry of entries) {
        if (!entry || typeof entry !== 'object') {
            continue;
        }
        const raw = entry as SpsaParameterEntry;
        const loose = entry as unknown as LooseObject;
        const name = coerceString(loose.name) ?? '';
        if (!name) {
            continue;
        }
        normalized.push({
            raw,
            name,
            type: coerceString(loose.type),
            value: toNullableNumber(loose.v),
            min: toNullableNumber(loose.min),
            max: toNullableNumber(loose.max),
            step: toNullableNumber(loose.step),
            delta: toNullableNumber(loose.delta),
            comment: coerceString(loose.comment),
            notUsed: Boolean(loose.is_not_used),
        });
    }
    return normalized;
}

function normalizeParamsRecord(source: unknown): Record<string, number> {
    if (Array.isArray(source)) {
        const result: Record<string, number> = {};
        for (const entry of source) {
            if (!entry || typeof entry !== 'object') continue;
            const loose = entry as unknown as LooseObject;
            const name = coerceString(loose.name);
            const value = toNullableNumber(loose.v);
            if (name && value !== null) {
                result[name] = value;
            }
        }
        return result;
    }
    return coerceNumericRecord(source);
}

function normalizePerturbations(source: unknown): NormalizedSpsaPerturbations {
    const loose = coerceObject(source);
    const plus = coerceNumericRecord(loose.plus);
    const minus = coerceNumericRecord(loose.minus);
    return {
        plus,
        minus,
    };
}

function normalizeLtcRegression(source: unknown): NormalizedSpsaLtcRegression | null {
    if (!source || typeof source !== 'object') {
        return null;
    }
    const loose = source as LooseObject;
    const status = coerceString(loose.status) ?? null;
    const winrate = toNullableNumber(loose.winrate);
    const elo = toNullableNumber(loose.elo);
    const tunedWins = Math.max(0, toNumber(loose.tuned_wins));
    const baselineWins = Math.max(0, toNumber(loose.baseline_wins));
    const draws = Math.max(0, toNumber(loose.draws));
    const totalGamesRaw = toNullableNumber(loose.total_games);
    const totalGames =
        totalGamesRaw !== null ? Math.max(0, Math.trunc(totalGamesRaw)) : tunedWins + baselineWins + draws;
    const totalPairsRaw = toNullableNumber(loose.total_pairs);
    const totalPairs = totalPairsRaw !== null ? Math.max(0, Math.trunc(totalPairsRaw)) : null;
    const pairsPlayedRaw = toNullableNumber(loose.pairs_played);
    const pairsPlayed = pairsPlayedRaw !== null ? Math.max(0, Math.trunc(pairsPlayedRaw)) : null;
    const accepted = typeof loose.is_accepted === 'boolean' ? loose.is_accepted : null;
    const baselineIdxRaw = toNullableNumber(loose.baseline_update_idx);
    const baselineUpdateIdx = baselineIdxRaw !== null ? Math.trunc(baselineIdxRaw) : null;
    const baselineVariantToken = coerceString(loose.baseline_variant_token);
    const tunedVariantToken = coerceString(loose.tuned_variant_token);
    const startedAt = toNullableTimestamp(loose.started_at);
    const completedAt = toNullableTimestamp(loose.completed_at);
    const failReasonsRaw = Array.isArray(loose.fail_reasons) ? loose.fail_reasons : [];
    const failReasons = failReasonsRaw
        .map((reason) => (typeof reason === 'string' ? reason : coerceString(reason)))
        .filter((reason): reason is string => Boolean(reason));
    const sprt = coerceObject(loose.sprt);
    let sprtDecision = coerceString(loose.sprt_decision);
    if (!sprtDecision && sprt) {
        const sprtLoose = sprt as LooseObject;
        sprtDecision = coerceString(sprtLoose.decision);
    }

    if (
        status === null &&
        winrate === null &&
        elo === null &&
        tunedWins === 0 &&
        baselineWins === 0 &&
        draws === 0 &&
        totalGames === 0 &&
        totalPairs === null &&
        pairsPlayed === null &&
        accepted === null &&
        baselineUpdateIdx === null &&
        !baselineVariantToken &&
        !tunedVariantToken &&
        startedAt === null &&
        completedAt === null &&
        failReasons.length === 0 &&
        !sprt
    ) {
        return null;
    }

    return {
        status,
        winrate,
        elo,
        tunedWins,
        baselineWins,
        draws,
        totalGames,
        totalPairs,
        pairsPlayed,
        accepted,
        baselineUpdateIdx,
        baselineVariantToken,
        tunedVariantToken,
        startedAt,
        completedAt,
        failReasons,
        sprt,
        sprtDecision,
    } satisfies NormalizedSpsaLtcRegression;
}

function computeDeltaStep(step: number | null, sPlus: number | null, sMinus: number | null): number | null {
    if (Number.isFinite(step)) {
        return step as number;
    }
    if (Number.isFinite(sPlus) && Number.isFinite(sMinus)) {
        const value = (sPlus as number) - (sMinus as number);
        return Number.isFinite(value) ? value : null;
    }
    return null;
}

function normalizeStringMap(value: unknown): Record<string, string> {
    if (!value || typeof value !== 'object' || Array.isArray(value)) {
        return {};
    }
    const record: Record<string, string> = {};
    for (const [key, raw] of Object.entries(value as JsonObject)) {
        if (!key) continue;
        const normalizedKey = String(key);
        const normalizedValue = raw == null ? '-' : String(raw);
        record[normalizedKey] = normalizedValue;
    }
    return record;
}

/**
 * `SpsaSummaryResponse` を canonical contract に基づいて正規化します。
 *
 * 数値フィールドの欠損・非数値は `0` や `null` に落とし込みます。
 */
export function normalizeSpsaSummary(raw: unknown): NormalizedSpsaSummary {
    const parsed = parseSpsaSummaryResponse(raw);
    const rawRecord = parsed as JsonObject;
    const wins = toNumber(parsed.wins);
    const losses = toNumber(parsed.losses);
    const draws = toNumber(parsed.draws);
    const totalGames = wins + losses + draws;

    const tunedBlackWins = toNumber(parsed.tuned_black_wins);
    const tunedBlackLosses = toNumber(parsed.tuned_black_losses);
    const tunedWhiteWins = toNumber(parsed.tuned_white_wins);
    const tunedWhiteLosses = toNumber(parsed.tuned_white_losses);

    const btdElo = toNullableNumber(parsed.btd_elo);
    const elo = toNullableNumber(parsed.elo);
    const btdSe = toNullableNumber(parsed.btd_se);
    const eloSe = toNullableNumber(rawRecord.elo_se as unknown);
    const btdLos = toNullableNumber(parsed.btd_los);
    const los = toNullableNumber(rawRecord.los as unknown);

    const games = coerceObject(rawRecord.games);
    const numUpdates = toNumber(games.total);
    const updatesCompleted = toNumber(games.completed);
    const etaSeconds = toNullableNumber(parsed.eta_seconds);
    const deltaNormLast = toNullableNumber(parsed.delta_norm_last);

    const engineTimeControls = normalizeStringMap(rawRecord.engineTimeControls as unknown);
    const defaultTimeControl = coerceString(rawRecord.defaultTimeControl as unknown) ?? null;
    const engines =
        Array.isArray(rawRecord.engines) && rawRecord.engines.length
            ? rawRecord.engines.map((name) => String(name ?? '')).filter((name) => name.trim().length > 0)
            : [];
    const engineInstances: Record<string, string | null | undefined> = {};
    const rawInstances = rawRecord.engineInstances as unknown;
    if (rawInstances && typeof rawInstances === 'object' && !Array.isArray(rawInstances)) {
        for (const [key, value] of Object.entries(rawInstances as JsonObject)) {
            if (!key) continue;
            engineInstances[String(key)] = value == null ? null : String(value);
        }
    }
    const engineStats: Record<string, { wins?: number; losses?: number; draws?: number; games?: number }> = {};
    const rawStats = rawRecord.engineStats as unknown;
    if (rawStats && typeof rawStats === 'object' && !Array.isArray(rawStats)) {
        for (const [key, value] of Object.entries(rawStats as JsonObject)) {
            if (!key || !value || typeof value !== 'object') continue;
            const statsRecord = value as JsonObject;
            engineStats[String(key)] = {
                wins: toNumber(statsRecord.wins),
                losses: toNumber(statsRecord.losses),
                draws: toNumber(statsRecord.draws),
                games: toNumber(statsRecord.games),
            };
        }
    }

    const engineMeta: Record<string, JsonObject> = {};
    const rawMeta = rawRecord.engineMeta as unknown;
    if (rawMeta && typeof rawMeta === 'object' && !Array.isArray(rawMeta)) {
        for (const [key, value] of Object.entries(rawMeta as JsonObject)) {
            if (!key || !value || typeof value !== 'object') continue;
            engineMeta[String(key)] = value as JsonObject;
        }
    }

    const drawRate = (() => {
        if (typeof parsed.draw_rate === 'number' && Number.isFinite(parsed.draw_rate)) {
            return parsed.draw_rate;
        }
        if (totalGames > 0) {
            return draws / totalGames;
        }
        return null;
    })();

    const ltcRegression = normalizeLtcSummary(parsed.ltc_regression);

    return {
        raw: parsed,
        mode: coerceString(parsed.mode),
        experimentName: coerceString(parsed.experiment_name),
        wins,
        losses,
        draws,
        tunedBlackWins,
        tunedBlackLosses,
        tunedWhiteWins,
        tunedWhiteLosses,
        btdElo,
        elo,
        btdSe,
        eloSe,
        btdLos,
        los,
        numUpdates,
        updatesCompleted,
        etaSeconds,
        drawRate,
        deltaNormLast,
        variantId: coerceString(rawRecord.variant_id as unknown),
        ltcRegression,
        engineTimeControls,
        defaultTimeControl,
        engines,
        engineInstances,
        engineStats,
        engineMeta,
    } satisfies NormalizedSpsaSummary;
}

export function toTournamentSummaryFromSpsa(
    summary: NormalizedSpsaSummary,
    baseMeta: Record<string, NormalizedTournamentEngineMeta> = {},
): NormalizedTournamentSummary {
    const completedGames = summary.wins + summary.losses + summary.draws;
    const engines = summary.engines.length ? summary.engines : Object.keys(summary.engineTimeControls);
    const meta: Record<string, NormalizedTournamentEngineMeta> = {};

    /**
     * Build a complete NormalizedTournamentEngineMeta entry by merging:
     * 1. Default empty values
     * 2. baseMeta (contains merged_options, resolved_options, etc. from initial load)
     * 3. spsaMeta (SPSA-specific overrides, if any)
     *
     * This ensures merged_options from baseMeta is preserved even when SPSA provides
     * partial engine meta without option details.
     */
    const buildMeta = (
        name: string,
        spsaMeta?: JsonObject,
        baseMetaEntry?: NormalizedTournamentEngineMeta,
    ): NormalizedTournamentEngineMeta => {
        // Start with defaults
        const defaults: NormalizedTournamentEngineMeta = {
            name,
            enginePath: null,
            merged_options: {},
            resolved_options: {},
            option_sources: {},
            option_sources_details: {},
            runtime_usi_options: {},
            runtime_engine_info: null,
            raw: {} as TournamentEngineMeta,
        };

        // Merge in baseMeta if available (preserves merged_options, etc.)
        const withBase: NormalizedTournamentEngineMeta = baseMetaEntry
            ? {
                  ...defaults,
                  ...baseMetaEntry,
                  name, // Ensure name is always correct
              }
            : defaults;

        // Merge in SPSA-specific meta if available
        if (spsaMeta && typeof spsaMeta === 'object') {
            const spsaTyped = spsaMeta as Partial<NormalizedTournamentEngineMeta>;

            // Helper to pick the value with content, preferring spsa over base
            const pickWithContent = <T extends Record<string, unknown>>(spsa: T | undefined, base: T): T => {
                if (spsa != null && typeof spsa === 'object' && Object.keys(spsa).length > 0) {
                    return spsa;
                }
                return base;
            };

            return {
                ...withBase,
                ...spsaTyped,
                name, // Ensure name is always correct
                // Use spsaMeta's options if present, otherwise fall back to baseMeta
                merged_options: pickWithContent(spsaTyped.merged_options, withBase.merged_options),
                resolved_options: pickWithContent(spsaTyped.resolved_options, withBase.resolved_options),
                option_sources: pickWithContent(spsaTyped.option_sources, withBase.option_sources),
                option_sources_details: pickWithContent(
                    spsaTyped.option_sources_details,
                    withBase.option_sources_details,
                ),
                runtime_usi_options: pickWithContent(spsaTyped.runtime_usi_options, withBase.runtime_usi_options),
            };
        }

        return withBase;
    };

    // Build meta for each engine, merging SPSA-side data with baseMeta
    for (const name of engines) {
        const spsaMeta = summary.engineMeta?.[name] as JsonObject | undefined;
        const baseMetaEntry = baseMeta[name];
        meta[name] = buildMeta(name, spsaMeta, baseMetaEntry);
    }

    const deriveEngineStats = (): Record<string, { wins: number; losses: number; draws: number; games: number }> => {
        if (engines.length >= 2) {
            const baseline = engines[0];
            const tuned = engines[1];
            const games = completedGames;
            return {
                [tuned]: { wins: summary.wins, losses: summary.losses, draws: summary.draws, games },
                [baseline]: { wins: summary.losses, losses: summary.wins, draws: summary.draws, games },
            };
        }
        return {};
    };
    const derivedStats = deriveEngineStats();

    const engineStats: NormalizedTournamentSummary['engineStats'] = {};
    for (const name of engines) {
        const stats = summary.engineStats[name] ?? derivedStats[name];
        const wins = stats?.wins ?? 0;
        const losses = stats?.losses ?? 0;
        const draws = stats?.draws ?? 0;
        const games = stats?.games ?? wins + losses + draws;
        engineStats[name] = {
            wins,
            losses,
            draws,
            games,
            rating: null,
            score: null,
        };
    }

    const engineInstances: Record<string, string | null> = {};
    for (const [name, instance] of Object.entries(summary.engineInstances)) {
        engineInstances[name] = instance == null ? null : String(instance);
    }

    return {
        raw: summary.raw as unknown as JsonObject,
        engines,
        engineStats,
        engineTimeControls: summary.engineTimeControls,
        defaultTimeControl: summary.defaultTimeControl,
        ratingInitial: 1500,
        completedGames,
        totalGames: completedGames,
        originalTotalGames: completedGames,
        cancelledGames: 0,
        runDir: null,
        tournamentFinished: false,
        sprtConclusion: null,
        sprt: null,
        tournamentType: 'spsa',
        pairResults: {},
        engineMeta: meta,
        engineInstances,
        btd: null,
    };
}

function normalizeLtcSummary(raw: unknown): NormalizedSpsaLtcSummary | null {
    if (!raw || typeof raw !== 'object') {
        return null;
    }
    const loose = raw as LooseObject;
    const enabled = Boolean(loose.enabled);
    if (!enabled) {
        return null;
    }
    const status = coerceString(loose.status) ?? 'unknown';
    const config = coerceObject(loose.config);
    const lastUpdateIdx = toNullableNumber(loose.last_update_idx);
    const winrate = toNullableNumber(loose.winrate);
    const elo = toNullableNumber(loose.elo);
    const baselineVariantToken = coerceString(loose.baseline_variant_token);
    const tunedVariantToken = coerceString(loose.tuned_variant_token);
    const pairsPlayed = toNullableNumber(loose.pairs_played);
    const sprt = coerceObject(loose.sprt);
    let sprtDecision = coerceString(loose.sprt_decision);
    if (!sprtDecision && sprt) {
        const sprtLoose = sprt as LooseObject;
        sprtDecision = coerceString(sprtLoose.decision);
    }
    const historySize = toNumber(loose.history_size);
    const bestEstimateRaw = coerceObject(loose.best_estimate);
    const bestEstimate = bestEstimateRaw
        ? {
              mean: toNullableNumber(bestEstimateRaw.mean),
              variance: toNullableNumber(bestEstimateRaw.variance),
              sigma: toNullableNumber(bestEstimateRaw.sigma),
              lower: toNullableNumber(bestEstimateRaw.lower),
              upper: toNullableNumber(bestEstimateRaw.upper),
              source: coerceString(bestEstimateRaw.source),
              compositionDepth: toNullableNumber(bestEstimateRaw.composition_depth),
          }
        : null;

    return {
        enabled: true,
        status,
        config,
        lastUpdateIdx,
        winrate,
        elo,
        baselineVariantToken,
        tunedVariantToken,
        pairsPlayed,
        sprt,
        sprtDecision,
        historySize,
        bestEstimate,
    } satisfies NormalizedSpsaLtcSummary;
}

export function normalizeSpsaParams(raw: unknown): NormalizedSpsaParams {
    const parsed = parseSpsaParamsResponse(raw);
    const loose = parsed as unknown as LooseObject;
    return {
        raw: parsed,
        variantId: coerceString(loose.variant_id),
        numParams: toNumber(loose.num_params),
        numUsed: toNumber(loose.num_used),
        numClamped: toNumber(loose.num_clamped),
        clampedRatio: toNullableNumber(loose.clamped_ratio),
        params: normalizeParameterEntries(loose.params),
        initialParams: coerceNumericRecord(loose.initial_params),
        diffs: coerceNumericRecord(loose.diffs),
        initialVariantId: coerceString(loose.initial_variant_id),
    } satisfies NormalizedSpsaParams;
}

function normalizeSpsaUpdateEntry(raw: SpsaUpdateEntry): NormalizedSpsaUpdateEntry | null {
    const loose = raw as unknown as LooseObject;
    const updateIdx = toNullableNumber(loose.update_idx);
    if (updateIdx === null) {
        return null;
    }
    const payload = coerceObject(loose.payload);

    let timestamp = toNullableNumber(loose.timestamp ?? payload.timestamp);
    const startedAt = toNullableTimestamp(loose.started_at ?? payload.started_at);
    const endedAt = toNullableTimestamp(loose.ended_at ?? payload.ended_at);
    if (timestamp === null && endedAt !== null) {
        timestamp = endedAt;
    }
    const deltaNorm = toNullableNumber(loose.delta_norm) ?? toNullableNumber(payload.delta_norm);
    const step = toNullableNumber(loose.step);
    const sPlus = toNullableNumber(loose.s_plus ?? payload.s_plus);
    const sMinus = toNullableNumber(loose.s_minus ?? payload.s_minus);
    const deltaStep = computeDeltaStep(step, sPlus, sMinus);
    const variantId = coerceString(loose.variant_id) ?? coerceString(payload.variant_id);
    const wins = Math.max(0, Math.trunc(Number(loose.wins ?? payload.wins ?? 0)));
    const losses = Math.max(0, Math.trunc(Number(loose.losses ?? payload.losses ?? 0)));
    const draws = Math.max(0, Math.trunc(Number(loose.draws ?? payload.draws ?? 0)));
    const btdElo = toNullableNumber(loose.btd_elo ?? payload.btd_elo);
    const phaseWdl = normalizePhaseWdlMap(loose.phase_wdl ?? payload.phase_wdl);
    const perturbations = normalizePerturbations(loose.perturbations ?? payload.perturbations);
    const gainCk = toNullableNumber(loose.c_k ?? payload.c_k);
    const gainAk = toNullableNumber(loose.a_k ?? payload.a_k);
    const isPending = Boolean(loose.is_pending ?? payload.is_pending);

    const ltcRaw = loose.ltc_regression ?? payload.ltc_regression;
    const ltcRegression = normalizeLtcRegression(ltcRaw);
    const hasLtcRegression = Boolean(loose.has_ltc_regression ?? payload.has_ltc_regression) || ltcRegression !== null;
    const ltcRejected =
        typeof loose.is_ltc_rejected === 'boolean'
            ? loose.is_ltc_rejected
            : typeof payload.is_ltc_rejected === 'boolean'
              ? payload.is_ltc_rejected
              : false;
    const ltcRevertedTo = toNullableNumber(loose.ltc_reverted_to ?? payload.ltc_reverted_to);

    return {
        raw,
        updateIdx,
        timestamp,
        startedAt,
        endedAt,
        deltaNorm,
        deltaStep,
        step,
        sPlus,
        sMinus,
        gamesCompleted: toNullableNumber(loose.games_completed ?? payload.games_completed),
        gradients: normalizeParamsRecord(loose.gradients ?? payload.gradients),
        params: normalizeParamsRecord(loose.params ?? payload.params),
        deltas: normalizeParamsRecord(loose.deltas ?? payload.deltas),
        payload,
        variantId,
        perturbations,
        isPending,
        gainCk,
        gainAk,
        wins,
        losses,
        draws,
        btdElo,
        isInitial: false,
        phaseWdl,
        hasLtcRegression,
        ltcRegression,
        ltcRejected,
        ltcRevertedTo: ltcRevertedTo !== null ? Math.trunc(ltcRevertedTo) : null,
    } satisfies NormalizedSpsaUpdateEntry;
}

export function normalizeSpsaUpdates(entries: unknown): NormalizedSpsaUpdateEntry[];
export function normalizeSpsaUpdates(entries: unknown, options: { strict?: boolean }): NormalizedSpsaUpdateEntry[];
export function normalizeSpsaUpdates(
    entries: unknown,
    options: { strict?: boolean } = {},
): NormalizedSpsaUpdateEntry[] {
    const parsed = parseSpsaUpdateEntries(entries);
    const strict = Boolean(options.strict);
    const normalized: NormalizedSpsaUpdateEntry[] = [];
    parsed.forEach((entry, index) => {
        const snapshot = normalizeSpsaUpdateEntry(entry);
        if (!snapshot) {
            if (strict) {
                throw new SpsaConsistencyError(`SpsaUpdatesResponse: update entry at index ${index} is invalid`);
            }
            return;
        }
        normalized.push(snapshot);
    });
    normalized.sort((a, b) => b.updateIdx - a.updateIdx);
    return normalized;
}

export function normalizeUpdatesProgress(raw: unknown, options: { strict?: boolean } = {}): SpsaUpdateProgress | null {
    if (raw == null) {
        return null;
    }
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
        if (options.strict) {
            throw new SpsaConsistencyError('SpsaUpdatesResponse.progress must be an object when provided');
        }
        return null;
    }
    const payload = raw as JsonObject;
    const completedValue = Number(payload.completed);
    const totalValue = Number(payload.total);
    const validCompleted = Number.isFinite(completedValue);
    const validTotal = Number.isFinite(totalValue);
    if (!validCompleted || !validTotal) {
        if (options.strict) {
            throw new SpsaConsistencyError('SpsaUpdatesResponse.progress requires numeric completed and total');
        }
        return null;
    }
    const percentRaw = payload.percent ?? payload.progress;
    const percent = typeof percentRaw === 'number' && Number.isFinite(percentRaw) ? percentRaw : null;
    return {
        completed: Math.max(0, Math.trunc(completedValue)),
        total: Math.max(0, Math.trunc(totalValue)),
        percent,
    } satisfies SpsaUpdateProgress;
}

export function normalizeVariantGames(raw: unknown): NormalizedSpsaUpdateDetail['games'] {
    if (!Array.isArray(raw)) {
        return [];
    }
    return raw
        .map((entry) => {
            if (!entry || typeof entry !== 'object') return null;
            const gameLoose = entry as LooseObject;
            const gameResult: NormalizedSpsaUpdateDetail['games'][number]['gameResult'] =
                typeof gameLoose.game_result === 'string' && isGameResultName(gameLoose.game_result)
                    ? gameLoose.game_result
                    : null;
            return {
                gameId: coerceString(gameLoose.game_id) ?? '',
                blackPlayer: coerceString(gameLoose.black_player),
                whitePlayer: coerceString(gameLoose.white_player),
                gameResult,
                numMoves: toNullableNumber(gameLoose.num_moves),
                phase: coerceString(gameLoose.phase),
                status: coerceString(gameLoose.status),
                assignedInstance: coerceString(gameLoose.assigned_instance),
                round: toNullableNumber(gameLoose.round),
                startTime: coerceString(gameLoose.start_time),
                endTime: coerceString(gameLoose.end_time),
            } satisfies NormalizedSpsaUpdateDetail['games'][number];
        })
        .filter((game): game is NormalizedSpsaUpdateDetail['games'][number] => game !== null);
}

/**
 * `SpsaUpdateDetailResponse` を canonical contract に基づいて正規化します。
 *
 * ゲーム配列や WDL 情報はある程度寛容に正規化しますが、構造上の矛盾があればテストで検知されるべきです。
 */
export function normalizeSpsaUpdateDetail(raw: unknown): NormalizedSpsaUpdateDetail {
    const parsed = parseSpsaUpdateDetailResponse(raw);
    const loose = parsed as unknown as LooseObject;
    const payload = coerceObject(loose.payload);
    const enginesLoose = coerceObject(loose.engines);
    const wdlLoose = coerceObject(loose.wdl);
    const rawMeta = loose.meta ?? payload.meta;
    const meta = coerceObject(rawMeta);
    const hasMeta = rawMeta != null && typeof rawMeta === 'object';
    const view = normalizeDetailView(meta.view, hasMeta);
    const detailWindow = normalizeDetailWindow(meta.window ?? meta.detail_window, view);
    const availableMeta = normalizeDetailIncludes(meta.available_includes);
    const loadedMeta = normalizeDetailIncludes(meta.loaded_includes);
    const resolvedAvailableIncludes: SpsaDetailInclude[] =
        availableMeta.length > 0
            ? availableMeta
            : view === 'full'
              ? (SPSA_DETAIL_SUPPORTED_INCLUDES.slice() as SpsaDetailInclude[])
              : [];
    const resolvedLoadedIncludes: SpsaDetailInclude[] =
        loadedMeta.length > 0 ? loadedMeta : view === 'full' ? [...resolvedAvailableIncludes] : [];

    const games = normalizeVariantGames(loose.games ?? payload.games);
    const ltcGames = normalizeVariantGames(loose.ltc_games ?? payload.ltc_games ?? payload.ltcGames);

    const gradientsSource = loose.gradients ?? payload.gradients ?? null;
    const deltasSource = loose.deltas ?? payload.deltas ?? null;
    const perturbationsSource = loose.perturbations ?? payload.perturbations ?? null;
    const gainCk = toNullableNumber(loose.c_k ?? payload.c_k);
    const gainAk = toNullableNumber(loose.a_k ?? payload.a_k);
    const isPending = Boolean(loose.is_pending ?? payload.is_pending);
    const ltcRaw = loose.ltc_regression ?? payload.ltc_regression;
    const ltcRegression = normalizeLtcRegression(ltcRaw);
    const hasLtcRegression = Boolean(loose.has_ltc_regression ?? payload.has_ltc_regression) || ltcRegression !== null;

    return {
        raw: parsed,
        updateIdx: toNumber(loose.update_idx),
        engines: {
            baseline: coerceString(enginesLoose.baseline),
            tuned: coerceString(enginesLoose.tuned),
        },
        wdl: {
            wins: toNumber(wdlLoose.wins),
            losses: toNumber(wdlLoose.losses),
            draws: toNumber(wdlLoose.draws),
        },
        variantId: coerceString(loose.variant_id),
        params: normalizeParamsRecord(loose.params),
        gradients: normalizeParamsRecord(gradientsSource),
        deltas: normalizeParamsRecord(deltasSource),
        sPlus: toNullableNumber(loose.s_plus ?? payload.s_plus),
        sMinus: toNullableNumber(loose.s_minus ?? payload.s_minus),
        step: toNullableNumber(loose.step ?? payload.step),
        perturbations: normalizePerturbations(perturbationsSource),
        gainCk,
        gainAk,
        isPending,
        games,
        gamesCount: toNumber(loose.games_count ?? games.length),
        ltcGames,
        ltcGamesCount: (() => {
            const rawCount = toNullableNumber(loose.ltc_games_count ?? payload.ltc_games_count);
            if (rawCount !== null) {
                return Math.max(0, Math.trunc(rawCount));
            }
            return ltcGames.length;
        })(),
        payload,
        isInitial: false,
        phaseWdl: normalizePhaseWdlMap(loose.phase_wdl ?? payload.phase_wdl),
        ltcRegression,
        hasLtcRegression,
        view,
        detailWindow,
        loadedIncludes: resolvedLoadedIncludes,
        availableIncludes: resolvedAvailableIncludes,
    } satisfies NormalizedSpsaUpdateDetail;
}
