import type {
    NormalizedSpsaParams,
    NormalizedSpsaSummary,
    NormalizedSpsaUpdateDetail,
    NormalizedSpsaUpdateEntry,
    SpsaConvergenceMetricsSnapshot,
    SpsaDashboardState,
    SpsaDataNode,
    SpsaTrendSeries,
} from '@/modules/spsa/types';

export const SUMMARY_CACHE_TTL_MS = 10_000;
export const PARAMS_CACHE_TTL_MS = 60_000;
export const MAX_TREND_POINTS = 1_000;
/**
 * Updatesテーブルに同時に表示する最大行数。
 * サーバ側のlimitとは独立しており、UI負荷を抑えるためのクライアント側制限。
 */
export const MAX_UPDATE_VISIBLE_ROWS = 100;
/**
 * クライアント側でキャッシュしておく更新エントリの最大件数。
 * トレンドやモビリティなどの派生計算のコストを履歴の長さに依存させないための上限。
 */
export const MAX_UPDATE_CACHE_ENTRIES = 2_000;

const initialDataNode = <T>(): SpsaDataNode<T> => ({
    data: null,
    status: 'idle',
    error: null,
    lastFetched: null,
});

const makeTrendSeries = (): SpsaTrendSeries => ({
    deltaNorm: [],
    deltaStep: [],
    gainAk: [],
    timestamps: [],
    variantIndices: [],
});

const makeConvergenceMetrics = (): SpsaConvergenceMetricsSnapshot => ({
    recentAvgDeltaNorm: null,
    recentMeanVectorDeltaNorm: null,
    recentAvgMeanVectorDeltaNorm: null,
    sampleSize: null,
    updatedAt: null,
});

export const state: SpsaDashboardState = {
    active: false,
    root: null,
    summary: initialDataNode<NormalizedSpsaSummary>(),
    summaryCache: null,
    params: initialDataNode<NormalizedSpsaParams>(),
    paramsCache: null,
    updates: {
        entries: [],
        total: 0,
        limit: 50,
        offset: 0,
        hasMore: false,
        lastUpdateIdx: -1,
        cache: new Map<number, NormalizedSpsaUpdateEntry>(),
        initialEntry: null,
        initialDetail: null,
        detailCache: new Map<number, NormalizedSpsaUpdateDetail>(),
        expanded: new Set<number>(),
        progress: null,
    },
    trend: makeTrendSeries(),
    convergenceMetrics: makeConvergenceMetrics(),
    connection: {
        eventSource: null,
        eventSourceStatus: 'closed',
        abortControllers: new Set<AbortController>(),
        refreshInFlight: false,
        refreshPromise: null,
        queuedRefresh: null,
    },
    visibility: {
        pageHidden: false,
        refreshPaused: false,
    },
    errorMessage: null,
    lastErrorAt: null,
};

export const resetConvergenceMetricsSnapshot = (): void => {
    state.convergenceMetrics = makeConvergenceMetrics();
};
