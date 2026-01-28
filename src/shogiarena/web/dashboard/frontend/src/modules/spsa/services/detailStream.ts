import { normalizeSpsaUpdateDetail } from './normalizers';
import { cacheUpdateDetail, getCachedUpdateDetail, setUpdateExpanded } from '../state';
import { INITIAL_UPDATE_IDX } from '@/modules/spsa/types';
import { SPSA_DETAIL_REQUIRED_INCLUDES } from '@/modules/spsa/constants';
import { describeError } from './errors';
import type {
    DashboardSpsaPublicApi,
    NormalizedSpsaUpdateDetail,
    SpsaDetailInclude,
    SpsaDetailViewMode,
    SpsaDetailWindowMode,
    SpsaUpdateDetailResponse,
} from '@/modules/spsa/types';
import type { SpsaDashboardState } from '@/modules/spsa/types/internal';
import type { DashboardCore } from '@/types/dashboard';
import type { JsonObject } from '@/types/shared';

const DEFAULT_RECONNECT_MS = 5_000;

function shouldAttemptInclude(detail: NormalizedSpsaUpdateDetail, include: SpsaDetailInclude): boolean {
    const available = detail.availableIncludes.includes(include);
    switch (include) {
        case 'variant_games':
            return available || (Number.isFinite(detail.gamesCount) && (detail.gamesCount as number) > 0);
        case 'ltc_games':
            return (
                available ||
                (Number.isFinite(detail.ltcGamesCount) && (detail.ltcGamesCount as number) > 0) ||
                Boolean(detail.ltcRegression) ||
                Boolean(detail.hasLtcRegression)
            );
        case 'score_history':
            return available;
        default:
            return available;
    }
}

function computeMissingIncludes(detail: NormalizedSpsaUpdateDetail): SpsaDetailInclude[] {
    if (detail.isInitial) {
        return [];
    }
    const missing: SpsaDetailInclude[] = [];
    SPSA_DETAIL_REQUIRED_INCLUDES.forEach((include) => {
        if (!shouldAttemptInclude(detail, include)) {
            return;
        }
        if (detail.loadedIncludes.includes(include)) {
            return;
        }
        missing.push(include);
    });
    return missing;
}

export type DetailBatchEnvelope = {
    type: 'detail_batch';
    details: SpsaUpdateDetailResponse[];
};

export function parseDetailBatchEnvelope(payload: unknown): DetailBatchEnvelope {
    if (!payload || typeof payload !== 'object' || Array.isArray(payload)) {
        throw new Error('SPSA detail SSE payload must be an object');
    }
    const envelope = payload as { type?: unknown; details?: unknown };
    if (envelope.type !== 'detail_batch') {
        throw new Error("SPSA detail SSE payload must have type 'detail_batch'");
    }
    if (!Array.isArray(envelope.details)) {
        throw new Error("SPSA detail SSE payload must include an array 'details'");
    }
    return {
        type: 'detail_batch',
        details: envelope.details as SpsaUpdateDetailResponse[],
    };
}

export type DetailStreamNotifier = {
    onProtocolError?: (message: string, error: unknown) => void;
    onRecoverableError?: (message: string, error: unknown) => void;
    /**
     * SSE 自体が利用できないなど、プロトコル外の致命的な前提違反を通知するためのハンドラ。
     * 省略された場合は呼び出し側で個別にエラーを処理します。
     */
    onError?: (message: string, error: unknown) => void;
};

type DetailStreamOwner = Window & { DashboardCore?: DashboardCore };

function resolveCore(explicit?: DashboardCore | null): DashboardCore | null {
    if (explicit) return explicit;
    if (typeof window === 'undefined') return null;
    return (window as DetailStreamOwner).DashboardCore ?? null;
}

function emitDetailStreamNotice(
    title: string,
    error: unknown,
    fallbackDetail: string,
    core?: DashboardCore | null,
): void {
    const resolvedDetail = describeError(error) || fallbackDetail;
    const targetCore = resolveCore(core);
    if (targetCore?.showSpsaDetailStreamError) {
        targetCore.showSpsaDetailStreamError(title, resolvedDetail);
        return;
    }
    if (targetCore?.showApiError) {
        targetCore.showApiError(title, resolvedDetail);
        return;
    }
    console.error('[SPSA] detail stream', title, resolvedDetail, error);
}

export function createDetailStreamNotifier(core?: DashboardCore | null): DetailStreamNotifier {
    return {
        onProtocolError: (message, error) =>
            emitDetailStreamNotice(message, error, 'SPSA detail stream protocol violation', core),
        onRecoverableError: (message, error) =>
            emitDetailStreamNotice(message, error, 'SPSA detail stream error', core),
        onError: (message, error) => emitDetailStreamNotice(message, error, 'SPSA detail stream error', core),
    };
}

export type DetailStreamController = {
    start: () => void;
    stop: () => void;
    isActive: () => boolean;
    updateTargets: (targets: readonly number[]) => void;
};

export function createDetailStreamController({
    state,
    api,
    endpoint,
    params,
    autoStreamIdle = false,
    notifier = {},
    reconnectDelayMs = DEFAULT_RECONNECT_MS,
}: {
    state: SpsaDashboardState;
    api: DashboardSpsaPublicApi;
    endpoint?: string | null;
    params?: string | null;
    autoStreamIdle?: boolean;
    notifier?: DetailStreamNotifier;
    reconnectDelayMs?: number;
}): DetailStreamController | null {
    if (typeof EventSource !== 'function') {
        notifier.onError?.(
            'SPSA detail stream requires EventSource support. This browser is not supported for SPSA view.',
            new Error('SPSA detail stream requires EventSource support'),
        );
        return null;
    }
    const baseEndpoint = endpoint && endpoint.trim().length > 0 ? endpoint.trim() : '/api/spsa/update/detail/stream';
    const rawParams = params && params.trim().length > 0 ? params.trim() : undefined;
    const trimmedParams = rawParams?.replace(/^\?/, '') ?? '';
    const baseSearchParams = new URLSearchParams(trimmedParams);
    let source: EventSource | null = null;
    let reconnectTimer: number | null = null;
    let currentTargets: number[] = [];
    let activeUrl: string | null = null;

    const arraysEqual = (a: readonly number[], b: readonly number[]): boolean => {
        if (a.length !== b.length) return false;
        return a.every((value, index) => value === b[index]);
    };

    const buildTargetUrl = (): string => {
        const paramsCopy = new URLSearchParams(baseSearchParams);
        if (currentTargets.length > 0) {
            paramsCopy.delete('limit');
            paramsCopy.set('update_idx', currentTargets.join(','));
        } else {
            paramsCopy.delete('update_idx');
        }
        const query = paramsCopy.toString();
        if (!query) return baseEndpoint;
        if (baseEndpoint.includes('?')) {
            return `${baseEndpoint}&${query}`;
        }
        return `${baseEndpoint}?${query}`;
    };

    const shouldStream = (): boolean => autoStreamIdle || currentTargets.length > 0;

    const recordStreamPayload = (snapshot: SpsaUpdateDetailResponse | null | undefined) => {
        if (!snapshot || typeof api.recordDetailPayloadMetrics !== 'function') return;
        const meta = snapshot.meta as JsonObject | undefined;
        const loadedRaw = Array.isArray(meta?.loaded_includes)
            ? (meta?.loaded_includes as string[])
            : Array.isArray(meta?.loadedIncludes)
              ? (meta?.loadedIncludes as string[])
              : undefined;
        const view = typeof meta?.view === 'string' ? (meta?.view as SpsaDetailViewMode) : undefined;
        const windowModeRaw = typeof meta?.window === 'string' ? (meta.window as string) : undefined;
        const resolvedWindow =
            windowModeRaw === 'full' || windowModeRaw === 'short' || windowModeRaw === 'long'
                ? (windowModeRaw as SpsaDetailWindowMode | 'full')
                : undefined;
        api.recordDetailPayloadMetrics?.(snapshot, {
            view,
            includeCount: Array.isArray(loadedRaw) ? loadedRaw.length : undefined,
            window: resolvedWindow,
        });
    };

    const ensureDetailSectionsLoaded = (
        detail: NormalizedSpsaUpdateDetail,
        options: { hydrateOptionalSections?: boolean } = {},
    ) => {
        if (typeof api.fetchUpdateDetail !== 'function') return;
        if (!options.hydrateOptionalSections) return;
        const missing = computeMissingIncludes(detail);
        if (!missing.length) return;
        const key = detail.isInitial ? INITIAL_UPDATE_IDX : detail.updateIdx;
        const requestable = missing;
        if (!requestable.length) return;
        void api
            .fetchUpdateDetail(key, { include: requestable as unknown as SpsaDetailInclude[] })
            .then(() => {
                if (state.updates.expanded.has(key)) {
                    api.focusUpdate?.(key, { scroll: false });
                }
            })
            .catch((error) => {
                notifier.onRecoverableError?.('Failed to hydrate SPSA detail sections', error);
            });
    };

    const applySnapshot = (snapshot: SpsaUpdateDetailResponse | null | undefined) => {
        if (!snapshot) return;
        recordStreamPayload(snapshot);
        const normalized = normalizeSpsaUpdateDetail(snapshot);
        cacheUpdateDetail(normalized, 'sse');
        const key = normalized.isInitial ? INITIAL_UPDATE_IDX : normalized.updateIdx;
        const merged = getCachedUpdateDetail(key) ?? normalized;
        const isExpanded = state.updates.expanded.has(key);
        ensureDetailSectionsLoaded(merged, { hydrateOptionalSections: isExpanded });
        if (isExpanded) {
            setUpdateExpanded(key, true);
            void Promise.resolve().then(() => api?.focusUpdate?.(key));
        }
    };

    const stop = () => {
        if (source) {
            source.removeEventListener('spsa_update_detail', handleMessage);
            source.close();
            source = null;
            activeUrl = null;
        }
        if (reconnectTimer !== null) {
            window.clearTimeout(reconnectTimer);
            reconnectTimer = null;
        }
    };

    const handleMessage = (event: MessageEvent<string>) => {
        try {
            const parsed = JSON.parse(event.data) as JsonObject | unknown;
            if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
                const type = (parsed as JsonObject).type;
                if (type === 'heartbeat') {
                    return;
                }
            }
            const envelope = parseDetailBatchEnvelope(parsed);
            envelope.details.forEach(applySnapshot);
        } catch (error) {
            markProtocolFailure(error);
        }
    };

    const scheduleReconnect = () => {
        if (reconnectTimer !== null) return;
        reconnectTimer = window.setTimeout(() => {
            reconnectTimer = null;
            start();
        }, reconnectDelayMs);
    };

    const markProtocolFailure = (error: unknown) => {
        stop();
        notifier.onProtocolError?.('SPSA detail stream protocol violation', error);
        scheduleReconnect();
    };

    const start = () => {
        if (!shouldStream()) {
            return;
        }
        const targetUrl = buildTargetUrl();
        if (source) {
            if (targetUrl === activeUrl) {
                return;
            }
            stop();
        }
        try {
            const next = new EventSource(targetUrl);
            source = next;
            activeUrl = targetUrl;
            next.addEventListener('spsa_update_detail', handleMessage);
            next.onmessage = handleMessage;
            next.onerror = (event) => {
                notifier.onRecoverableError?.('Detail SSE stream error', event);
                stop();
                scheduleReconnect();
            };
        } catch (error) {
            notifier.onRecoverableError?.('Failed to open detail SSE stream', error);
            stop();
            scheduleReconnect();
        }
    };

    const updateTargets = (targets: readonly number[]) => {
        const normalized = Array.from(
            new Set(
                targets
                    .map((value) => (Number.isFinite(value) ? Math.trunc(value) : null))
                    .filter((value): value is number => value !== null && value >= 0),
            ),
        ).sort((a, b) => a - b);
        if (arraysEqual(normalized, currentTargets)) {
            if (!autoStreamIdle && normalized.length === 0) {
                stop();
            }
            return;
        }
        currentTargets = normalized;
        if (!autoStreamIdle && currentTargets.length === 0) {
            stop();
            return;
        }
        if (source) {
            stop();
            start();
        }
    };

    return {
        start,
        stop,
        isActive: () => Boolean(source),
        updateTargets,
    };
}
