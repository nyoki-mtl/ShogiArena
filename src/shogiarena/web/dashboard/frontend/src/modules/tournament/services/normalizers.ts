import type {
    NormalizedBTDSummary,
    NormalizedSprtSummary,
    NormalizedTournamentEngineMeta,
    NormalizedTournamentEngineStats,
    NormalizedTournamentGame,
    NormalizedTournamentPairResult,
    NormalizedTournamentPlayerInfo,
    NormalizedTournamentSummary,
    TournamentBTDSummary,
    TournamentEngineMeta,
    TournamentSprtSummary,
    TournamentSummary,
} from '@/modules/tournament/types';
import type { JsonObject, MutableJsonObject } from '@/types/shared';
import { TournamentSummarySchema, TournamentBTDSummarySchema, TournamentSprtSummarySchema } from './schemas';

type LooseObject = MutableJsonObject;

type PairResultsMap = Record<string, Record<string, NormalizedTournamentPairResult>>;

function assertString(value: unknown, context: string): string {
    if (typeof value === 'string' && value.trim().length > 0) {
        return value.trim();
    }
    throw new Error(`${context} must be a non-empty string`);
}

function assertObject(value: unknown, context: string): LooseObject {
    if (value && typeof value === 'object' && !Array.isArray(value)) {
        return value as LooseObject;
    }
    throw new Error(`${context} must be an object`);
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

function coerceDateString(value: unknown): string | null {
    if (value instanceof Date) {
        const timestamp = value.getTime();
        return Number.isNaN(timestamp) ? null : value.toISOString();
    }
    return coerceString(value);
}

function coerceStringRecord(value: unknown): Record<string, string> {
    if (!value || typeof value !== 'object') {
        return {};
    }
    const record: Record<string, string> = {};
    for (const [rawKey, rawValue] of Object.entries(value as JsonObject)) {
        const key = coerceString(rawKey);
        const normalizedValue = coerceString(rawValue);
        if (!key || !normalizedValue) continue;
        record[key] = normalizedValue;
    }
    return record;
}

function coerceNullableString(value: unknown): string | null {
    const normalized = coerceString(value);
    return normalized ?? null;
}

function expectFiniteNumber(value: unknown, context: string): number {
    if (typeof value === 'number' && Number.isFinite(value)) {
        return value;
    }
    if (typeof value === 'string' && value.trim().length > 0) {
        const num = Number(value);
        if (Number.isFinite(num)) return num;
    }
    throw new Error(`${context} must be a finite number`);
}

function expectNonNegativeInteger(value: unknown, context: string): number {
    const num = expectFiniteNumber(value, context);
    if (!Number.isInteger(num) || num < 0) {
        throw new Error(`${context} must be a non-negative integer`);
    }
    return num;
}

function optionalFiniteNumber(value: unknown, context: string): number | null {
    if (value === undefined || value === null) return null;
    return expectFiniteNumber(value, context);
}

function toNullableNumber(value: unknown, context = 'value'): number | null {
    return optionalFiniteNumber(value, context);
}

const BLACK_WIN_CODES = new Set([0, 4, 8, 12, 16]);
const WHITE_WIN_CODES = new Set([1, 5, 9, 13, 17]);
const DRAW_CODES = new Set([2, 6, 10]);
const ERROR_CODES = new Set([3, 7, 15, 18, 19]);

function categorizeResultCode(resultCode: number | null): 0 | 1 | 2 | 3 | null {
    if (resultCode === null) return null;
    if (BLACK_WIN_CODES.has(resultCode)) return 0;
    if (WHITE_WIN_CODES.has(resultCode)) return 1;
    if (DRAW_CODES.has(resultCode)) return 2;
    if (ERROR_CODES.has(resultCode)) return 3;
    return null;
}

function normalizeBtdSummary(value: unknown): NormalizedBTDSummary | null {
    if (value === null || value === undefined) {
        return null;
    }
    const parsed = TournamentBTDSummarySchema.parse(value);
    const anchor = parsed.anchor ? assertString(parsed.anchor, 'btd.anchor') : null;
    const ratings: Record<string, { mean: number | null; standardError: number | null }> = {};
    const ratingEntries =
        parsed.ratings ?? ({} as Record<string, { elo?: number | null | undefined; se?: number | null | undefined }>);
    for (const [name, entry] of Object.entries(ratingEntries) as Array<
        [string, { elo?: number | null | undefined; se?: number | null | undefined }]
    >) {
        ratings[String(name)] = {
            mean: optionalFiniteNumber(entry.elo, `btd.ratings.${name}.elo`),
            standardError: optionalFiniteNumber(entry.se, `btd.ratings.${name}.se`),
        };
    }
    const ratingCov =
        parsed.rating_cov &&
        Object.fromEntries(
            Object.entries(parsed.rating_cov).map(([rowKey, rowValue]) => [
                rowKey,
                Object.fromEntries(
                    Object.entries(rowValue as Record<string, unknown>).map(([colKey, num]) => [
                        colKey,
                        expectFiniteNumber(num, `btd.rating_cov.${rowKey}.${colKey}`),
                    ]),
                ),
            ]),
        );
    if (!anchor && Object.keys(ratings).length === 0) {
        return null;
    }
    return {
        anchor,
        ratings,
        raw: {
            ...parsed,
            rating_cov: ratingCov as TournamentBTDSummary['rating_cov'],
        },
    } satisfies NormalizedBTDSummary;
}

function normalizeSprtSummary(value: unknown): NormalizedSprtSummary | null {
    if (value === null || value === undefined) {
        return null;
    }
    const parsed = TournamentSprtSummarySchema.parse(value) as TournamentSprtSummary;
    const llr = optionalFiniteNumber(parsed.llr, 'sprt.llr');
    const lower = optionalFiniteNumber(parsed.lower, 'sprt.lower');
    const upper = optionalFiniteNumber(parsed.upper, 'sprt.upper');
    const games = optionalFiniteNumber(parsed.games, 'sprt.games');
    const decision = parsed.decision ? assertString(parsed.decision, 'sprt.decision') : null;

    if (llr === null && lower === null && upper === null && games === null && !decision) {
        return null;
    }

    return {
        llr,
        lower,
        upper,
        decision,
        games,
        raw: parsed,
    } satisfies NormalizedSprtSummary;
}

function normalizeEngineInstances(value: unknown, engines: Iterable<string>): Record<string, string | null> {
    const instances: Record<string, string | null> = {};
    if (value && typeof value === 'object') {
        for (const [engine, instanceId] of Object.entries(value as JsonObject)) {
            const key = coerceString(engine) ?? String(engine);
            if (!key) continue;
            instances[key] = instanceId == null ? null : assertString(instanceId, `engineInstances.${key}`);
        }
    }
    for (const engine of engines) {
        if (!Object.hasOwn(instances, engine)) {
            instances[engine] = null;
        }
    }
    return instances;
}

function coerceMutableJson(value: unknown): MutableJsonObject {
    const source = coerceObject(value);
    return { ...source } as MutableJsonObject;
}

function coerceRuntimeUsiOptions(value: unknown): Record<string, { current?: unknown; default?: unknown }> {
    if (!value || typeof value !== 'object') {
        return {};
    }
    const map: Record<string, { current?: unknown; default?: unknown }> = {};
    for (const [rawKey, rawEntry] of Object.entries(value as JsonObject)) {
        const key = coerceString(rawKey);
        if (!key) continue;
        if (!rawEntry || typeof rawEntry !== 'object' || Array.isArray(rawEntry)) {
            continue;
        }
        const entry = rawEntry as MutableJsonObject;
        const normalized: { current?: unknown; default?: unknown } = {};
        if (Object.hasOwn(entry, 'current')) {
            normalized.current = entry.current;
        }
        if (Object.hasOwn(entry, 'default')) {
            normalized.default = (entry as { default?: unknown }).default;
        }
        map[key] = normalized;
    }
    return map;
}

function coerceRuntimeEngineInfo(value: unknown): MutableJsonObject | null {
    if (!value || typeof value !== 'object' || Array.isArray(value)) {
        return null;
    }
    const source = value as MutableJsonObject;
    const info: MutableJsonObject = {};
    const name = coerceString(source.name);
    const author = coerceString(source.author);
    if (name) info.name = name;
    if (author) info.author = author;
    return Object.keys(info).length > 0 ? info : null;
}

function normalizeEngineStatsEntry(value: unknown): NormalizedTournamentEngineStats {
    const stats = assertObject(value, 'engine_stats entry');
    const wins = expectNonNegativeInteger(stats.wins ?? 0, 'engine_stats.wins');
    const draws = expectNonNegativeInteger(stats.draws ?? 0, 'engine_stats.draws');
    const losses = expectNonNegativeInteger(stats.losses ?? 0, 'engine_stats.losses');
    const gamesValue = stats.games ?? wins + draws + losses;
    const games = expectNonNegativeInteger(gamesValue, 'engine_stats.games');
    const ratingValue = optionalFiniteNumber(stats.rating ?? null, 'engine_stats.rating');
    const scoreCandidate = optionalFiniteNumber(stats.score ?? null, 'engine_stats.score');
    const score = scoreCandidate !== null ? scoreCandidate : games > 0 ? (wins + draws * 0.5) / games : null;
    return {
        wins,
        draws,
        losses,
        games,
        rating: ratingValue,
        score,
    } satisfies NormalizedTournamentEngineStats;
}

function normalizeEngineStatsMap(
    rawStats: unknown,
    engineNames: Set<string>,
): Record<string, NormalizedTournamentEngineStats> {
    if (!rawStats || typeof rawStats !== 'object') {
        return {};
    }
    const map: Record<string, NormalizedTournamentEngineStats> = {};
    const statsObject = rawStats as Record<string, unknown>;
    for (const [engine, value] of Object.entries(statsObject)) {
        const key = coerceString(engine) ?? String(engine);
        if (!key) continue;
        engineNames.add(key);
        map[key] = normalizeEngineStatsEntry(value);
    }
    for (const engine of engineNames) {
        if (!Object.hasOwn(map, engine)) {
            map[engine] = {
                wins: 0,
                draws: 0,
                losses: 0,
                games: 0,
                rating: null,
                score: null,
            } satisfies NormalizedTournamentEngineStats;
        }
    }
    return map;
}

function setPairResult(
    target: PairResultsMap,
    engine: string,
    opponent: string,
    wins: number,
    losses: number,
    draws: number,
): void {
    if (!target[engine]) {
        target[engine] = {};
    }
    target[engine][opponent] = { wins, losses, draws } satisfies NormalizedTournamentPairResult;
}

function normalizePairResults(rawPairResults: unknown): PairResultsMap {
    const results: PairResultsMap = {};
    if (!rawPairResults || typeof rawPairResults !== 'object') {
        return results;
    }
    for (const [key, value] of Object.entries(rawPairResults as JsonObject)) {
        if (!value || typeof value !== 'object') continue;
        const [engineA, engineB] = String(key ?? '').split('_vs_');
        const normalizedA = coerceString(engineA);
        const normalizedB = coerceString(engineB);
        if (!normalizedA || !normalizedB) continue;
        const entry = value as LooseObject;
        const aWins = expectNonNegativeInteger(entry[`${normalizedA}_wins`], `pairResults.${key}.${normalizedA}_wins`);
        const bWins = expectNonNegativeInteger(entry[`${normalizedB}_wins`], `pairResults.${key}.${normalizedB}_wins`);
        const draws = expectNonNegativeInteger(entry.draws, `pairResults.${key}.draws`);
        setPairResult(results, normalizedA, normalizedB, aWins, bWins, draws);
        setPairResult(results, normalizedB, normalizedA, bWins, aWins, draws);
    }
    return results;
}

function collectEngines(summary: JsonObject): Set<string> {
    const names = new Set<string>();
    const engines = summary.engines;
    if (Array.isArray(engines)) {
        for (const name of engines) {
            const normalized = coerceString(name);
            if (normalized) names.add(normalized);
        }
    }
    const engineStats = summary.engineStats;
    if (engineStats && typeof engineStats === 'object') {
        for (const name of Object.keys(engineStats as JsonObject)) {
            const normalized = coerceString(name) ?? String(name);
            if (normalized) names.add(normalized);
        }
    }
    const pairResults = summary.pairResults;
    if (pairResults && typeof pairResults === 'object') {
        for (const key of Object.keys(pairResults as JsonObject)) {
            const [engineA, engineB] = String(key ?? '').split('_vs_');
            const normalizedA = coerceString(engineA);
            const normalizedB = coerceString(engineB);
            if (normalizedA) names.add(normalizedA);
            if (normalizedB) names.add(normalizedB);
        }
    }
    return names;
}

function normalizeTimeControls(
    summary: JsonObject,
    engines: Iterable<string>,
): { engineTimeControls: Record<string, string>; defaultTimeControl: string | null } {
    const engineTimeControls: Record<string, string> = {};
    const rawControls = summary.engineTimeControls;
    if (rawControls && typeof rawControls === 'object') {
        for (const [engine, value] of Object.entries(rawControls as JsonObject)) {
            const key = assertString(engine, 'engineTimeControls key');
            const spec = assertString(value, `engineTimeControls.${key}`);
            engineTimeControls[key] = spec;
        }
    }
    const defaultSpec = summary.defaultTimeControl
        ? assertString(summary.defaultTimeControl, 'defaultTimeControl')
        : null;
    if (defaultSpec) {
        for (const engine of engines) {
            if (!engineTimeControls[engine]) {
                engineTimeControls[engine] = defaultSpec;
            }
        }
    }
    return { engineTimeControls, defaultTimeControl: defaultSpec };
}

function normalizeEngineMetaEntry(value: unknown): NormalizedTournamentEngineMeta | null {
    if (!value || typeof value !== 'object') {
        return null;
    }
    const raw = coerceObject(value);
    const nameRaw = coerceString(raw.name);
    const name = nameRaw ? nameRaw.trim() : null;
    if (!name) {
        return null;
    }
    const enginePath = raw.engine_path === null || raw.engine_path === undefined ? null : coerceString(raw.engine_path);
    const optionSources = coerceStringRecord(raw.option_sources);
    const optionSourceDetails = coerceStringRecord(raw.option_sources_details);
    const mergedOptions = coerceMutableJson(raw.merged_options);
    const resolvedOptions = coerceMutableJson(raw.resolved_options);
    const runtimeUsiOptions = coerceRuntimeUsiOptions(raw.runtime_usi_options);
    const runtimeEngineInfo = coerceRuntimeEngineInfo(raw.runtime_engine_info);

    const cloned: MutableJsonObject = {
        ...raw,
        name,
        engine_path: enginePath ?? null,
        option_sources: optionSources,
        option_sources_details: optionSourceDetails,
        merged_options: mergedOptions,
        resolved_options: resolvedOptions,
    };
    if (runtimeEngineInfo) {
        cloned.runtime_engine_info = runtimeEngineInfo;
    }
    if (enginePath) {
        cloned.engine_path = enginePath;
    }
    return {
        name,
        enginePath: enginePath ?? null,
        merged_options: mergedOptions,
        resolved_options: resolvedOptions,
        option_sources: optionSources,
        option_sources_details: optionSourceDetails,
        runtime_usi_options: runtimeUsiOptions,
        runtime_engine_info: runtimeEngineInfo,
        raw: cloned as TournamentEngineMeta,
    } satisfies NormalizedTournamentEngineMeta;
}

function normalizeEngineMetaMap(
    rawMeta: unknown,
    engines: Iterable<string>,
): Record<string, NormalizedTournamentEngineMeta> {
    const map: Record<string, NormalizedTournamentEngineMeta> = {};

    if (Array.isArray(rawMeta)) {
        for (const entry of rawMeta) {
            const normalized = normalizeEngineMetaEntry(entry);
            if (!normalized) continue;
            map[normalized.name] = normalized;
        }
    }
    for (const engine of engines) {
        if (!Object.hasOwn(map, engine)) {
            map[engine] = {
                name: engine,
                enginePath: null,
                merged_options: {} as MutableJsonObject,
                resolved_options: {} as MutableJsonObject,
                option_sources: {},
                option_sources_details: {},
                runtime_usi_options: {},
                runtime_engine_info: null,
                raw: {
                    name: engine,
                    merged_options: {},
                    resolved_options: {},
                    option_sources: {},
                    option_sources_details: {},
                    runtime_usi_options: {},
                } as TournamentEngineMeta,
            } satisfies NormalizedTournamentEngineMeta;
        }
    }
    return map;
}

function isNormalizedTournamentGame(value: unknown): value is NormalizedTournamentGame {
    if (!value || typeof value !== 'object') {
        return false;
    }
    const record = value as LooseObject;
    return (
        typeof record.initialSfen === 'string' &&
        typeof record === 'object' &&
        typeof record.players === 'object' &&
        record.players !== null &&
        Object.hasOwn(record, 'resultCategory') &&
        Object.hasOwn(record, 'scores') &&
        Object.hasOwn(record, 'raw')
    );
}

function normalizePlayerInfo(raw: LooseObject, color: 'black' | 'white'): NormalizedTournamentPlayerInfo {
    const name = coerceString(raw[`${color}_player`]);

    const timeControl = coerceString(raw[`time_control_${color}`]);

    return { name: name ?? null, timeControl: timeControl ?? null } satisfies NormalizedTournamentPlayerInfo;
}

function extractAdjudicationReason(raw: LooseObject): string | null {
    const adjudication = raw.adjudication;
    if (adjudication && typeof adjudication === 'object') {
        const reason = (adjudication as LooseObject).reason;
        const normalizedReason = coerceString(reason);
        if (normalizedReason) {
            return normalizedReason;
        }
    }
    return null;
}

function determineWinner(category: 0 | 1 | 2 | 3 | null): 'black' | 'white' | 'draw' | 'error' | 'unknown' {
    if (category === 0) return 'black';
    if (category === 1) return 'white';
    if (category === 2) return 'draw';
    if (category === 3) return 'error';
    return 'unknown';
}

function computeScores(category: 0 | 1 | 2 | 3 | null): NormalizedTournamentGame['scores'] {
    if (category === 0) {
        return { black: 1, white: 0 };
    }
    if (category === 1) {
        return { black: 0, white: 1 };
    }
    if (category === 2) {
        return { black: 0.5, white: 0.5 };
    }
    return { black: null, white: null };
}

function computeTotalPlies(raw: LooseObject): number | null {
    const totalPlies = toNullableNumber(raw.total_plies ?? null, 'game_record.total_plies');
    if (typeof totalPlies === 'number' && Number.isFinite(totalPlies) && totalPlies >= 0) {
        return Math.trunc(totalPlies);
    }
    const moves = raw.moves;
    if (Array.isArray(moves)) {
        return moves.length;
    }
    return null;
}

const startTimeKeys = ['start_time'] as const;
const endTimeKeys = ['end_time'] as const;

function resolveTimestamp(raw: LooseObject, keys: readonly string[]): string | null {
    for (const key of keys) {
        if (!Object.hasOwn(raw, key)) continue;
        const value = coerceDateString(raw[key]);
        if (value) {
            return value;
        }
    }
    return null;
}

function resolveGameId(raw: LooseObject): string | null {
    const normalized = coerceString(raw.game_id);
    return normalized ?? null;
}

function resolveInitialSfen(raw: LooseObject): string {
    const normalized = coerceString(raw.initial_sfen);
    if (normalized) {
        return normalized;
    }
    return 'startpos';
}

export function normalizeTournamentGame(value: unknown): NormalizedTournamentGame | null {
    if (isNormalizedTournamentGame(value)) {
        return value;
    }
    if (!value || typeof value !== 'object') {
        return null;
    }
    const raw = coerceObject(value);

    const resultCodeCandidate = raw.result_code;
    const resultCodeRaw =
        resultCodeCandidate === null || resultCodeCandidate === undefined
            ? null
            : toNullableNumber(resultCodeCandidate, 'game_record.result_code');
    const normalizedResultCode =
        typeof resultCodeRaw === 'number' && Number.isFinite(resultCodeRaw) && resultCodeRaw >= 0
            ? Math.trunc(resultCodeRaw)
            : null;
    const category = categorizeResultCode(normalizedResultCode);

    return {
        raw,
        gameId: resolveGameId(raw),
        initialSfen: resolveInitialSfen(raw),
        players: {
            black: normalizePlayerInfo(raw, 'black'),
            white: normalizePlayerInfo(raw, 'white'),
        },
        resultCode: normalizedResultCode,
        resultCategory: category,
        winner: determineWinner(category),
        scores: computeScores(category),
        totalPlies: computeTotalPlies(raw),
        startTime: resolveTimestamp(raw, startTimeKeys),
        endTime: resolveTimestamp(raw, endTimeKeys),
        adjudicationReason: extractAdjudicationReason(raw),
    } satisfies NormalizedTournamentGame;
}

export function normalizeTournamentGamesList(values: unknown): NormalizedTournamentGame[] {
    if (!Array.isArray(values)) {
        return [];
    }
    const out: NormalizedTournamentGame[] = [];
    for (const value of values) {
        const normalized = normalizeTournamentGame(value);
        if (normalized) {
            out.push(normalized);
        }
    }
    return out;
}

export function normalizeTournamentSummary(raw: TournamentSummary | JsonObject): NormalizedTournamentSummary {
    const parsed = TournamentSummarySchema.parse(raw);
    const summary = coerceObject(parsed);
    const engines = collectEngines(summary);

    const { engineTimeControls, defaultTimeControl } = normalizeTimeControls(summary, engines);
    const engineStats = normalizeEngineStatsMap(summary.engineStats, engines);
    const engineMeta = normalizeEngineMetaMap(summary.enginesMeta, engines);
    const engineInstances = normalizeEngineInstances(summary.engineInstances, engines);

    const ratingInitial = expectFiniteNumber(summary.ratingInitial, 'ratingInitial');

    const games = coerceObject(summary.games);
    const completedGames = expectNonNegativeInteger(games.completed, 'games.completed');
    let totalGames = expectNonNegativeInteger(games.total, 'games.total');
    const cancelledGames = expectNonNegativeInteger(games.cancelled ?? 0, 'games.cancelled');
    const originalTotal = expectNonNegativeInteger(totalGames + cancelledGames, 'games.total');
    const cappedCancelled = originalTotal > 0 ? Math.min(cancelledGames, originalTotal) : cancelledGames;
    if (totalGames === 0 && originalTotal > 0) {
        const effective = originalTotal - cappedCancelled;
        totalGames = Math.max(0, effective);
    }
    if (totalGames === 0 && originalTotal > 0) {
        totalGames = originalTotal;
    }
    const runDir = summary.runDir ? assertString(summary.runDir, 'runDir') : null;

    const finishedFlag = summary.tournamentFinished;
    const tournamentFinished =
        finishedFlag === true ||
        finishedFlag === 'true' ||
        finishedFlag === 'finished' ||
        finishedFlag === 'completed' ||
        (totalGames > 0 && completedGames >= totalGames);

    const pairResults = normalizePairResults(summary.pairResults);
    const btd = normalizeBtdSummary(summary.btd);
    const sprt = normalizeSprtSummary(summary.sprt);
    const tournamentType = coerceNullableString(summary.tournamentType);

    return {
        raw: summary,
        engines: Array.from(engines).sort((a, b) => a.localeCompare(b)),
        engineStats,
        engineTimeControls,
        defaultTimeControl,
        ratingInitial,
        completedGames,
        totalGames,
        originalTotalGames: originalTotal,
        cancelledGames,
        runDir,
        tournamentFinished,
        sprtConclusion: coerceString(summary.sprtConclusion),
        sprt,
        tournamentType,
        pairResults,
        engineMeta,
        engineInstances,
        btd,
    } satisfies NormalizedTournamentSummary;
}
