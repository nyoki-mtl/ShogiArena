/**
 * SPSA 用の REST フェッチャ群。
 *
 * - ネットワーク由来の一時的な失敗（接続エラー・タイムアウト・一時的な 5xx 等）は
 *   {@link FetcherDeps.reportRecoverableFailure} を通じて「ユーザーに見せる recoverable failure」
 *   として扱います。これらは Notice / Diagnostics に報告されますが、ダッシュボード自体は継続します。
 * - レスポンス JSON の型違反・契約違反（必須フィールド欠損・legacy フィールド混在など）は
 *   `normalizeSpsa*` 系関数が例外を投げることで検知され、**致命的バグ (fatal)** と見なします。
 *   この種の例外は recoverable failure として握りつぶさず、呼び出し側でそのまま再送出するか、
 *   上位のエラー境界へ伝播させて fail-fast させる想定です。
 *
 * 重要:
 * - `requestSummary` / `requestParams` / `requestUpdates` には `propagateError` オプションがあります。
 *   `propagateError: true` を指定した呼び出し元では、`normalizeSpsa*` 起因の契約違反を
 *   Diagnostics やテストで確実に検知できるよう、例外を捕捉して握りつぶさないでください。
 */
import { requestJson } from '@/modules/shared/services/api';
import { recordLiveDiagnosticsMetric } from '@/modules/live/utils/liveNamespace';
import {
    applyConvergenceMetrics,
    applyMobilitySeries,
    applySummaryProgressPatch,
    applyUpdatesResponse,
    cacheUpdateDetail,
    clearParamsCache,
    clearSummaryCache,
    getCachedParams,
    getCachedSummary,
    getInitialUpdateDetail,
    recordParamsError,
    recordSummaryError,
    resolveParams,
    resolveSummary,
    setParamsCache,
    setSummaryCache,
    SUMMARY_CACHE_TTL_MS,
    updateProgressSnapshot,
} from '../state';
import {
    normalizeSpsaParams,
    normalizeSpsaSummary,
    normalizeSpsaUpdateDetail,
    normalizeUpdatesProgress,
} from './normalizers';
import type {
    SpsaCorrelationResponse,
    SpsaConvergenceResponse,
    SpsaLtcResultsResponse,
    SpsaLtcSummary,
    SpsaParamsResponse,
    SpsaSummaryResponse,
    SpsaUpdateDetailRequestOptions,
    SpsaUpdateDetailResponse,
    SpsaUpdateProgress,
    SpsaUpdatesResponse,
    SpsaDetailInclude,
    SpsaDetailViewMode,
    SpsaDetailWindowMode,
} from '@/modules/spsa/types';
import { INITIAL_UPDATE_IDX } from '@/modules/spsa/types';
import { describeError, isAbortError, isLikelyNetworkError } from './errors';
import type { JsonObject } from '@/types/shared';
import type { DashboardCore } from '@/types/dashboard';
import type { LiveDiagnosticsStopwatch } from '@/modules/live/utils/liveNamespace';
import type { SpsaApiCallbacks } from './types';
import type { SpsaStreamCacheState } from './streams';
import { isConsistencyError, mergeSpsaSummarySnapshot } from './consistency';
import {
    ANALYSIS_HYDRATION_METRIC,
    CONVERGENCE_CACHE_TTL_MS,
    CORRELATION_CACHE_TTL_MS,
    DETAIL_FETCH_HYDRATION_METRIC,
    LTC_HYDRATION_METRIC,
    LTC_BACKFILL_METRIC,
    LTC_RESULTS_CACHE_TTL_MS,
    LTC_RESULTS_REST_BACKFILL_COOLDOWN_MS,
    LTC_RESULTS_STREAM_STALE_MS,
    LTC_RESULTS_STREAM_WARMUP_MS,
    LTC_SUMMARY_CACHE_TTL_MS,
    PARAMS_HYDRATION_METRIC,
    SUMMARY_HYDRATION_METRIC,
} from './api.constants';
import { SPSA_SUMMARY_PROGRESS_EVENT } from '../constants';
import {
    SPSA_DETAIL_DEFAULT_VIEW,
    SPSA_DETAIL_DEFAULT_WINDOW,
    SPSA_DETAIL_SUPPORTED_INCLUDES,
} from '@/modules/spsa/constants';
import type { ResumeCoordinator } from '@/modules/shared/services/resumeCoordinator';

const ANALYSIS_FETCH_TIMEOUT_MS = 15_000;

import type { getState } from '../state';

/**
 * `createSpsaFetchers` が依存する外部コンテキスト。
 *
 * - `reportRecoverableFailure` は **ネットワーク層の一時的エラー** をユーザーへ通知するためにのみ使用します。
 *   型レベル・契約レベルの不整合（`normalizeSpsa*` 起因のエラー）をここへ渡さないことを意図しています。
 * - `handleError` は SPSA モジュール内の一般的なエラー表示用で、REST / SSE 共通の
 *   「現在の操作を続行できない」状態をユーザーに示すために使われます。
 */
export type FetcherDeps = {
    core: DashboardCore;
    state: ReturnType<typeof getState>;
    callbacks: SpsaApiCallbacks;
    getApiBase: () => string;
    reportRecoverableFailure: (
        context: string,
        error: unknown,
        options?: { notifyOffline?: boolean; title?: string },
    ) => void;
    handleError: (message: string, options?: { source?: 'sse' | 'generic' }) => void;
    startHydrationAttempt: (metric: string) => LiveDiagnosticsStopwatch;
    recordHydrationCacheHit: (metric: string) => void;
    markAnalysisDataStale: () => void;
    recordDetailPayloadMetrics: (
        payload: unknown,
        context?: { view?: SpsaDetailViewMode; includeCount?: number; window?: SpsaDetailWindowMode | 'full' },
    ) => void;
    streamCacheState: SpsaStreamCacheState;
    isCorrelationStreamActive: () => boolean;
    isConvergenceStreamActive: () => boolean;
    isLtcResultsStreamActive: () => boolean;
    resumeCoordinator?: ResumeCoordinator;
    deriveStreamBackedLtcSummary: () => SpsaLtcSummary | null;
    deriveStreamBackedLtcResults: (limit: number) => SpsaLtcResultsResponse | null;
};

export type SpsaFetchers = ReturnType<typeof createSpsaFetchers>;

interface RetryOptions {
    readonly maxRetries?: number;
    readonly retryDelayMs?: number;
}

const DETAIL_INCLUDE_SET = new Set<SpsaDetailInclude>(SPSA_DETAIL_SUPPORTED_INCLUDES);

/**
 * `SpsaDetailInclude` の配列を安全に正規化します。
 *
 * - 不正な include 値や重複した値は静かに無視されます（契約違反ではなく入力ノイズ扱い）。
 * - サーバ側契約としては {@link SPSA_DETAIL_SUPPORTED_INCLUDES} に含まれる値のみが送られる想定ですが、
 *   将来の拡張や古いフロントエンドとの互換のために、ここでは fail-fast せずにフィルタリングします。
 */
const normalizeDetailIncludeOptions = (
    include?: SpsaDetailInclude | readonly SpsaDetailInclude[],
): SpsaDetailInclude[] => {
    if (!include) {
        return [];
    }
    const values = Array.isArray(include) ? include : [include];
    const seen = new Set<SpsaDetailInclude>();
    const normalized: SpsaDetailInclude[] = [];
    for (const entry of values) {
        if (!DETAIL_INCLUDE_SET.has(entry) || seen.has(entry)) {
            continue;
        }
        seen.add(entry);
        normalized.push(entry);
    }
    return normalized;
};

/**
 * `liveView.progress` 相当のスナップショットを緩やかに正規化します。
 *
 * - `completed` が有限数値でない場合は `null` を返し、呼び出し元が「進捗情報なし」として扱います。
 * - ここでは「できるだけ多くのダッシュボードを動かす」ための best-effort 変換を行い、
 *   契約違反として fail-fast する責務は持ちません（SSE 契約側での検証に委ねます）。
 */
const normalizeProgressSnapshot = (raw: unknown): SpsaUpdateProgress | null =>
    normalizeUpdatesProgress(raw, { strict: false });

/**
 * SPSA REST エンドポイントと各種キャッシュを扱うフェッチャ群を構築します。
 *
 * - 低レベルの HTTP 要求は {@link requestJson} を経由して行い、ネットワークエラーは
 *   {@link FetcherDeps.reportRecoverableFailure} を通じて recoverable failure として扱います。
 * - レスポンスの構造・型に関する検証は `normalizeSpsa*` 系関数に委譲されており、
 *   それらが投げる例外は **契約違反のシグナル** として上位へ伝播させることを意図しています。
 */
export function createSpsaFetchers(deps: FetcherDeps) {
    let correlationFetchPromise: Promise<SpsaCorrelationResponse> | null = null;
    let convergenceFetchPromise: Promise<SpsaConvergenceResponse> | null = null;
    let lastLtcResultsBackfillAt = 0;

    /**
     * ネットワーク層の一時的な失敗に対する簡易リトライラッパー。
     *
     * - {@link isLikelyNetworkError} が true を返すエラーのみをリトライ対象とし、
     *   それ以外（型違反・JSON 解析失敗・契約違反など）は即座に再送出します。
     * - `attempt` は Live Diagnostics 用のストップウォッチであり、リトライの有無をメトリクスに反映します。
     */
    const executeWithRetry = async <T>(
        runner: () => Promise<T>,
        attempt: LiveDiagnosticsStopwatch | null,
        options: RetryOptions = {},
    ): Promise<T> => {
        const maxRetries = Math.max(0, options.maxRetries ?? 0);
        const delayMs = Math.max(0, options.retryDelayMs ?? 0);
        let retries = 0;
        while (true) {
            try {
                return await runner();
            } catch (error) {
                if (isAbortError(error)) {
                    throw error;
                }
                if (retries >= maxRetries || !isLikelyNetworkError(error)) {
                    throw error;
                }
                retries += 1;
                attempt?.markRetry();
                if (delayMs > 0) {
                    await new Promise((resolve) => {
                        window.setTimeout(resolve, delayMs);
                    });
                }
            }
        }
    };

    /**
     * 単純な JSON GET ラッパー。
     *
     * - HTTP/ネットワークエラーは {@link FetcherDeps.reportRecoverableFailure} を通じて通知された上で再送出されます。
     * - 正常にレスポンスを取得できた場合でも、戻り値の構造検証は呼び出し側 (`normalizeSpsa*`) に委ねられます。
     * - ここで投げられる例外は **常に recoverable failure（ネットワーク由来）** を意図しており、
     *   契約違反は後続の正規化処理側で検知されます。
     */
    const fetchJson = async <T>(
        path: string,
        options: { signal?: AbortSignal; title?: string; notifyOffline?: boolean } = {},
    ): Promise<T> => {
        const apiBase = deps.getApiBase();
        const url = `${apiBase}${path}`;
        try {
            const result = await requestJson<T>(url, { signal: options.signal });
            return result;
        } catch (error) {
            if (isAbortError(error)) {
                throw error;
            }
            deps.reportRecoverableFailure(`SPSA fetch ${path}`, error, {
                notifyOffline: options.notifyOffline ?? true,
                title: options.title,
            });
            if (error instanceof Error) {
                throw error;
            }
            throw new Error(describeError(error));
        }
    };

    const publishSummaryProgress = (snapshot: SpsaUpdateProgress): void => {
        const existing =
            deps.core.state?.spsaSummary && typeof deps.core.state.spsaSummary === 'object'
                ? (deps.core.state.spsaSummary as Record<string, unknown>)
                : {};
        const nextRecord: Record<string, unknown> = { ...existing, completed: snapshot.completed };
        if (typeof snapshot.total === 'number' && Number.isFinite(snapshot.total)) {
            nextRecord.total = snapshot.total;
        }
        const next = nextRecord as JsonObject;
        if (deps.core.state) {
            deps.core.state.spsaSummary = next;
        }
        deps.core.events?.emit?.(SPSA_SUMMARY_PROGRESS_EVENT, next);
    };

    const applyProgressPayload = (raw: unknown): SpsaUpdateProgress | null => {
        const snapshot = normalizeProgressSnapshot(raw);
        if (!snapshot) {
            return null;
        }
        updateProgressSnapshot(snapshot);
        applySummaryProgressPatch(snapshot);
        publishSummaryProgress(snapshot);
        return snapshot;
    };

    /**
     * SPSA サマリー (`GET /api/spsa/summary`) を取得し、正規化して state に反映します。
     *
     * - `force === false` かつ有効なキャッシュが存在する場合はネットワークアクセスを行いません。
     * - ネットワーク由来のエラーは recoverable failure として記録され、
     *   {@link FetcherDeps.handleError} を通じてユーザーに通知されます。
     * - `normalizeSpsaSummary` が投げる例外は **サーバ実装の契約違反** とみなし、
     *   `options.propagateError === true` のときは呼び出し元へそのまま伝播させます。
     */
    const requestSummary = async (
        force = false,
        signal?: AbortSignal,
        options: { propagateError?: boolean } = {},
    ): Promise<void> => {
        const propagateError = Boolean(options.propagateError);
        if (!force) {
            const cached = getCachedSummary();
            if (cached) {
                deps.recordHydrationCacheHit(SUMMARY_HYDRATION_METRIC);
                resolveSummary(cached);
                deps.callbacks.onSummaryChanged?.();
                return;
            }
        }
        const attempt = deps.startHydrationAttempt(SUMMARY_HYDRATION_METRIC);
        try {
            const data = await executeWithRetry(
                () =>
                    fetchJson<SpsaSummaryResponse>('/api/spsa/summary', {
                        signal,
                        title: 'Failed to fetch SPSA summary',
                        notifyOffline: true,
                    }),
                attempt,
                { maxRetries: 1, retryDelayMs: 250 },
            );
            const normalized = normalizeSpsaSummary(data);
            const merged = mergeSpsaSummarySnapshot(normalized, 'rest');
            resolveSummary(merged);
            setSummaryCache(merged, SUMMARY_CACHE_TTL_MS);
            deps.callbacks.onSummaryChanged?.();
            attempt.succeed();
        } catch (error) {
            if (isAbortError(error)) {
                attempt.cancel();
                return;
            }
            attempt.fail();
            const message = error instanceof Error ? error.message : 'Failed to load SPSA summary';
            recordSummaryError(message);
            clearSummaryCache();
            deps.handleError(`Failed to refresh SPSA summary: ${message}`);
            if (propagateError || isConsistencyError(error)) {
                throw error instanceof Error ? error : new Error(message);
            }
        }
    };

    /**
     * SPSA パラメータ (`GET /api/spsa/params`) を取得し、正規化して state に反映します。
     *
     * - `force === false` かつ有効なキャッシュが存在する場合はネットワークアクセスを行いません。
     * - ネットワークエラーは recoverable failure として扱い、UI に通知したうえでキャッシュをクリアします。
     * - `normalizeSpsaParams` で検出された型・契約違反は fatal と見なし、
     *   `options.propagateError === true` の場合には呼び出し元で fail-fast できるよう再送出されます。
     */
    const requestParams = async (
        force = false,
        signal?: AbortSignal,
        options: { propagateError?: boolean } = {},
    ): Promise<void> => {
        const propagateError = Boolean(options.propagateError);
        if (!force) {
            const cached = getCachedParams();
            if (cached) {
                deps.recordHydrationCacheHit(PARAMS_HYDRATION_METRIC);
                resolveParams(cached);
                deps.callbacks.onParamsChanged?.();
                return;
            }
        }
        const attempt = deps.startHydrationAttempt(PARAMS_HYDRATION_METRIC);
        try {
            const data = await executeWithRetry(
                () =>
                    fetchJson<SpsaParamsResponse>('/api/spsa/params', {
                        signal,
                        title: 'Failed to fetch SPSA parameters',
                        notifyOffline: true,
                    }),
                attempt,
                { maxRetries: 1, retryDelayMs: 250 },
            );
            const normalized = normalizeSpsaParams(data);
            resolveParams(normalized);
            setParamsCache(normalized);
            deps.callbacks.onParamsChanged?.();
            attempt.succeed();
        } catch (error) {
            if (isAbortError(error)) {
                attempt.cancel();
                return;
            }
            attempt.fail();
            const message = error instanceof Error ? error.message : 'Failed to load SPSA parameters';
            recordParamsError(message);
            clearParamsCache();
            deps.handleError(`Failed to refresh SPSA parameters: ${message}`);
            if (propagateError) {
                throw error instanceof Error ? error : new Error(message);
            }
        }
    };

    /**
     * SPSA アップデート一覧 (`GET /api/spsa/updates`) を取得し、キャッシュと state を更新します。
     *
     * - ネットワークエラーは recoverable failure としてログ出力し、`options.propagateError` に応じて再送出します。
     * - 現状、`SpsaUpdatesResponse` の構造検証は比較的寛容であり、軽微な欠損は best-effort で補完されます。
     *   将来的に契約違反を strict に検知したい場合は、`normalizeSpsaUpdates` のような専用正規化関数を導入し、
     *   ここから呼び出すことを想定しています。
     */
    const requestUpdates = async ({
        limit = deps.state.updates.limit,
        offset = 0,
        signal,
        propagateError = false,
    }: {
        readonly limit?: number;
        readonly offset?: number;
        readonly signal?: AbortSignal;
        readonly propagateError?: boolean;
    } = {}): Promise<void> => {
        const query = new URLSearchParams({ limit: String(limit), offset: String(offset) });
        try {
            const data = await fetchJson<SpsaUpdatesResponse>(`/api/spsa/updates?${query.toString()}`, {
                signal,
                title: 'Failed to fetch SPSA updates',
                notifyOffline: true,
            });
            const normalizedProgress = normalizeUpdatesProgress(data.progress, { strict: true });
            if (normalizedProgress) {
                applyProgressPayload(normalizedProgress);
            }
            applyUpdatesResponse({ ...data, progress: normalizedProgress ?? undefined }, { strict: true });
            deps.markAnalysisDataStale();
            deps.callbacks.onUpdatesChanged?.({ reason: 'entries' });
        } catch (error) {
            if (isAbortError(error)) {
                return;
            }
            deps.handleError(`Failed to refresh SPSA updates: ${describeError(error)}`);
            if (propagateError || isConsistencyError(error)) {
                const wrapped = error instanceof Error ? error : new Error(describeError(error));
                throw wrapped;
            }
        }
    };

    /**
     * 単一アップデートの詳細 (`GET /api/spsa/update/<idx>`) を取得して正規化します。
     *
     * - `INITIAL_UPDATE_IDX` に対しては REST を叩かず、初期キャッシュから取得できない場合は
     *   recoverable failure として `reportRecoverableFailure` に委ねた上で例外を投げます。
     * - 正常レスポンスに対しては `normalizeSpsaUpdateDetail` で厳密な構造検証を行い、
     *   legacy フィールドや契約違反が検出された場合は例外を投げます。
     *   これらはサーバ側のバグとみなされるため、呼び出し元では fail-fast させるべきです。
     */
    const fetchUpdateDetail = async (
        updateIdx: number,
        options?: SpsaUpdateDetailRequestOptions,
    ): Promise<ReturnType<typeof normalizeSpsaUpdateDetail>> => {
        if (updateIdx === INITIAL_UPDATE_IDX) {
            const initialDetail = getInitialUpdateDetail();
            if (initialDetail) {
                cacheUpdateDetail(initialDetail, 'rest');
                return initialDetail;
            }
            const missingError = new Error('Initial SPSA parameters unavailable');
            deps.reportRecoverableFailure('SpsaApi.fetchUpdateDetail.initial', missingError);
            throw missingError;
        }
        const apiBase = deps.getApiBase();
        const attempt = deps.startHydrationAttempt(DETAIL_FETCH_HYDRATION_METRIC);
        const view: SpsaDetailViewMode = options?.view ?? SPSA_DETAIL_DEFAULT_VIEW;
        const windowMode: SpsaDetailWindowMode = options?.window ?? SPSA_DETAIL_DEFAULT_WINDOW;
        const includeValues = normalizeDetailIncludeOptions(options?.include);
        const params = new URLSearchParams({ view, window: windowMode });
        if (includeValues.length) {
            params.set('include', includeValues.join(','));
        }
        const requestUrl = `${apiBase}/api/spsa/update/${encodeURIComponent(String(updateIdx))}?${params.toString()}`;
        try {
            const detail = await executeWithRetry(() => requestJson<SpsaUpdateDetailResponse>(requestUrl), attempt, {
                maxRetries: 1,
                retryDelayMs: 250,
            });
            deps.recordDetailPayloadMetrics(detail, { view, includeCount: includeValues.length, window: windowMode });
            const normalized = normalizeSpsaUpdateDetail(detail);
            cacheUpdateDetail(normalized, 'rest');
            attempt.succeed();
            return normalized;
        } catch (error) {
            if (isAbortError(error)) {
                attempt.cancel();
                throw error;
            }
            attempt.fail();
            if (!isConsistencyError(error)) {
                deps.reportRecoverableFailure('SpsaApi.fetchUpdateDetail', error, {
                    notifyOffline: true,
                    title: 'Failed to load SPSA update detail',
                });
            }
            if (error instanceof Error) {
                throw error;
            }
            throw new Error(describeError(error));
        }
    };

    /**
     * SPSA correlation 解析結果を取得し、SSE キャッシュと REST キャッシュを統合して返します。
     *
     * - 可能な限り SSE ストリームからの最新データを優先し、足りない場合のみ REST をフォールバックとして利用します。
     * - REST からのレスポンスが契約違反だった場合（型不整合など）は例外としてそのまま呼び出し元へ伝播します。
     */
    const fetchCorrelationAnalysis = async (): Promise<SpsaCorrelationResponse> => {
        const now = Date.now();
        const { correlationCache, latestCorrelationData } = deps.streamCacheState;
        if (correlationCache && now < correlationCache.expiresAt) {
            deps.recordHydrationCacheHit(ANALYSIS_HYDRATION_METRIC);
            return correlationCache.data;
        }
        if (deps.isCorrelationStreamActive() && latestCorrelationData) {
            deps.recordHydrationCacheHit(ANALYSIS_HYDRATION_METRIC);
            deps.streamCacheState.correlationCache = {
                data: latestCorrelationData,
                expiresAt: Date.now() + CORRELATION_CACHE_TTL_MS,
            };
            return latestCorrelationData;
        }
        if (deps.streamCacheState.correlationCache) {
            deps.streamCacheState.correlationCache = null;
        }
        const attempt = deps.startHydrationAttempt(ANALYSIS_HYDRATION_METRIC);
        if (correlationFetchPromise) {
            return correlationFetchPromise;
        }

        const controller = new AbortController();
        const timeoutId = window.setTimeout(() => controller.abort(), ANALYSIS_FETCH_TIMEOUT_MS);
        correlationFetchPromise = executeWithRetry(
            () =>
                fetchJson<SpsaCorrelationResponse>('/api/spsa/analysis/correlation', {
                    title: 'Failed to fetch SPSA correlation analysis',
                    notifyOffline: true,
                    signal: controller.signal,
                }),
            attempt,
            { maxRetries: 1, retryDelayMs: 250 },
        )
            .then((response) => {
                if (response.status === 'ready') {
                    deps.streamCacheState.latestCorrelationData = response;
                    deps.streamCacheState.correlationCache = {
                        data: response,
                        expiresAt: Date.now() + CORRELATION_CACHE_TTL_MS,
                    };
                }
                return response;
            })
            .finally(() => {
                window.clearTimeout(timeoutId);
                correlationFetchPromise = null;
            });
        return correlationFetchPromise;
    };

    /**
     * SPSA convergence 解析結果を取得し、必要に応じて REST で補完します。
     *
     * - SSE ストリームが有効かつ新しいスナップショットがあればそれを優先します。
     * - それ以外の場合は `GET /api/spsa/analysis/convergence?format=json` を叩き、
     *   正常系ではメトリクス更新とキャッシュ設定を行います。
     * - ネットワークエラーは recoverable failure として `reportRecoverableFailure` に通知されますが、
     *   レスポンス構造の契約違反は例外として伝播し、呼び出し元で fail-fast させることを意図しています。
     */
    const fetchConvergenceAnalysis = async (): Promise<SpsaConvergenceResponse> => {
        const now = Date.now();
        const { latestConvergenceData, convergenceCacheExpiry } = deps.streamCacheState;
        if (latestConvergenceData && (deps.isConvergenceStreamActive() || now < convergenceCacheExpiry)) {
            deps.recordHydrationCacheHit(ANALYSIS_HYDRATION_METRIC);
            applyConvergenceMetrics(latestConvergenceData);
            return latestConvergenceData;
        }

        if (convergenceFetchPromise) {
            return convergenceFetchPromise;
        }

        const controller = new AbortController();
        const convergenceTimeoutId = window.setTimeout(() => controller.abort(), ANALYSIS_FETCH_TIMEOUT_MS);
        const pendingPromise = (async () => {
            const attempt = deps.startHydrationAttempt(ANALYSIS_HYDRATION_METRIC);
            try {
                const apiBase = deps.getApiBase();
                const payload = await executeWithRetry(
                    () =>
                        requestJson<SpsaConvergenceResponse>(`${apiBase}/api/spsa/analysis/convergence?format=json`, {
                            signal: controller.signal,
                        }),
                    attempt,
                    { maxRetries: 1, retryDelayMs: 250 },
                );
                if (payload.status === 'ready') {
                    deps.streamCacheState.latestConvergenceData = payload;
                    deps.streamCacheState.convergenceCacheExpiry = Date.now() + CONVERGENCE_CACHE_TTL_MS;
                    applyConvergenceMetrics(payload);
                    if (payload.mobility_series) {
                        applyMobilitySeries(payload.mobility_series);
                    }
                    if (payload.ltc_results?.summary) {
                        const now = Date.now();
                        deps.streamCacheState.latestLtcResults = payload.ltc_results;
                        deps.streamCacheState.latestLtcResultsAt = now;
                        deps.streamCacheState.ltcResultsGapDetected =
                            payload.ltc_results.total > payload.ltc_results.results.length;
                        deps.streamCacheState.ltcSummaryCache = {
                            data: payload.ltc_results.summary,
                            expiresAt: now + 30_000,
                        };
                    }
                }
                attempt.succeed();
                return payload;
            } catch (error) {
                if (isAbortError(error)) {
                    attempt.cancel();
                    throw error;
                }
                attempt.fail();
                deps.reportRecoverableFailure('SpsaApi.fetchConvergenceAnalysis', error, {
                    title: 'Failed to fetch SPSA convergence analysis',
                    notifyOffline: true,
                });
                throw error instanceof Error ? error : new Error(describeError(error));
            } finally {
                window.clearTimeout(convergenceTimeoutId);
            }
        })();

        convergenceFetchPromise = pendingPromise;
        pendingPromise.finally(() => {
            if (convergenceFetchPromise === pendingPromise) {
                convergenceFetchPromise = null;
            }
        });
        pendingPromise.catch(() => {
            /* ensure rejection is observed */
        });
        return pendingPromise;
    };

    /**
     * SPSA LTC の回帰サマリーを取得します。
     *
     * - まず SSE ストリーム由来のスナップショットを優先し、存在しない場合のみ REST を利用します。
     * - REST レスポンスの構造検証は `normalizeSpsaSummary` 側に委ねられており、
     *   ここではキャッシュ戦略とフェッチ戦略のみに責務を絞ります。
     */
    const fetchLtcSummary = async (): Promise<SpsaLtcSummary> => {
        const now = Date.now();
        const streamSummary = deps.deriveStreamBackedLtcSummary();
        if (streamSummary) {
            deps.recordHydrationCacheHit(LTC_HYDRATION_METRIC);
            deps.streamCacheState.ltcSummaryCache = {
                data: streamSummary,
                expiresAt: now + LTC_SUMMARY_CACHE_TTL_MS,
            };
            return streamSummary;
        }
        if (deps.streamCacheState.ltcSummaryCache && now < deps.streamCacheState.ltcSummaryCache.expiresAt) {
            deps.recordHydrationCacheHit(LTC_HYDRATION_METRIC);
            return deps.streamCacheState.ltcSummaryCache.data;
        }
        const payload = await fetchJson<SpsaLtcSummary>('/api/spsa/ltc/summary', {
            title: 'Failed to fetch LTC regression summary',
            notifyOffline: true,
        });
        deps.streamCacheState.ltcSummaryCache = {
            data: payload,
            expiresAt: now + LTC_SUMMARY_CACHE_TTL_MS,
        };
        return payload;
    };

    /**
     * SPSA LTC の個別結果一覧を取得します。
     *
     * - `limit` は [1, 500] にクランプされます。
     * - SSE ストリームからの結果を優先しつつ、足りない場合は REST フェッチにフォールバックします。
     */
    const fetchLtcResults = async (limit = 50): Promise<SpsaLtcResultsResponse> => {
        const normalizedLimit = Math.max(1, Math.min(limit, 500));
        const now = Date.now();
        let streamPayload = deps.deriveStreamBackedLtcResults(normalizedLimit);
        if (streamPayload) {
            deps.recordHydrationCacheHit(LTC_HYDRATION_METRIC);
            deps.streamCacheState.ltcResultsCache.set(normalizedLimit, {
                data: streamPayload,
                expiresAt: now + LTC_RESULTS_CACHE_TTL_MS,
            });
            return streamPayload;
        }
        const cached = deps.streamCacheState.ltcResultsCache.get(normalizedLimit);
        if (cached && now < cached.expiresAt) {
            deps.recordHydrationCacheHit(LTC_HYDRATION_METRIC);
            return cached.data;
        }
        const streamActive = deps.isLtcResultsStreamActive();
        const openedAt = deps.streamCacheState.ltcResultsStreamOpenedAt;
        const warmingUp = streamActive && typeof openedAt === 'number' && now - openedAt < LTC_RESULTS_STREAM_WARMUP_MS;
        if (!streamPayload && !cached && warmingUp) {
            await new Promise((resolve) => setTimeout(resolve, 250));
            streamPayload = deps.deriveStreamBackedLtcResults(normalizedLimit);
            if (streamPayload) {
                deps.recordHydrationCacheHit(LTC_HYDRATION_METRIC);
                deps.streamCacheState.ltcResultsCache.set(normalizedLimit, {
                    data: streamPayload,
                    expiresAt: Date.now() + LTC_RESULTS_CACHE_TTL_MS,
                });
                return streamPayload;
            }
        }
        const lastStreamAt = deps.streamCacheState.latestLtcResultsAt;
        const streamStale = typeof lastStreamAt === 'number' ? now - lastStreamAt > LTC_RESULTS_STREAM_STALE_MS : true;
        const gapDetected = deps.streamCacheState.ltcResultsGapDetected;
        const missingStreamData = streamPayload == null;
        const shouldBackfill =
            missingStreamData &&
            (gapDetected || !streamActive || streamStale) &&
            now - lastLtcResultsBackfillAt >= LTC_RESULTS_REST_BACKFILL_COOLDOWN_MS;
        if (cached && !shouldBackfill) {
            deps.recordHydrationCacheHit(LTC_HYDRATION_METRIC);
            return cached.data;
        }
        if (!shouldBackfill && cached) {
            return cached.data;
        }
        if (shouldBackfill) {
            recordLiveDiagnosticsMetric(LTC_BACKFILL_METRIC, {
                triggered: 1,
                gapDetected: gapDetected ? 1 : 0,
                streamInactive: streamActive ? 0 : 1,
                streamStale: streamStale ? 1 : 0,
            });
        }
        const query = new URLSearchParams({ limit: String(normalizedLimit) });
        const payload = await fetchJson<SpsaLtcResultsResponse>(`/api/spsa/ltc/results?${query.toString()}`, {
            title: 'Failed to fetch LTC regression results',
            notifyOffline: true,
        });
        lastLtcResultsBackfillAt = Date.now();
        deps.streamCacheState.ltcResultsGapDetected = false;
        deps.streamCacheState.ltcResultsCache.set(normalizedLimit, {
            data: payload,
            expiresAt: now + LTC_RESULTS_CACHE_TTL_MS,
        });
        return payload;
    };

    const resetAnalysisFetches = (): void => {
        correlationFetchPromise = null;
        convergenceFetchPromise = null;
    };

    return {
        executeWithRetry,
        fetchJson,
        requestSummary,
        requestParams,
        requestUpdates,
        fetchUpdateDetail,
        fetchCorrelationAnalysis,
        fetchConvergenceAnalysis,
        fetchLtcSummary,
        fetchLtcResults,
        resetAnalysisFetches,
    } as const;
}
