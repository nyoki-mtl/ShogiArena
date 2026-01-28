import type { LiveCardsApi, LiveDashboardNamespace, LiveSummaryApi, LiveTimeApi, LiveUpdatesApi } from '@/types/live';
import { refreshLiveNamespaceDiagnostics } from './diagnostics';

export type LiveNamespaceKey = 'cards' | 'time' | 'summary' | 'updates';

export interface LiveNamespaceTimelineEntry {
    key: LiveNamespaceKey;
    provider: string | null;
    timestamp: number;
    sinceStart: number;
    sincePrevious: number;
}

export interface LiveNamespaceMetricWindow {
    durationMs: number;
    triggered: number;
    failed: number;
    cacheHit: number;
    sampleCount: number;
}

export interface LiveNamespaceMetricExtra {
    total: number;
    last: number;
    samples: number;
    max: number;
    min: number;
}

export interface LiveNamespaceMetricExtraWindow {
    durationMs: number;
    samples: number;
    latest: number;
    average: number;
    max: number;
    min: number;
    values: readonly number[];
}

export interface LiveNamespaceMetric {
    name: string;
    triggered: number;
    failed: number;
    cacheHit: number;
    updatedAt: number;
    extras?: Record<string, LiveNamespaceMetricExtra>;
    window?: LiveNamespaceMetricWindow;
    extraWindows?: Record<string, LiveNamespaceMetricExtraWindow>;
}

export interface LiveNamespaceDiagnostics {
    order: readonly LiveNamespaceKey[];
    providers: Readonly<Partial<Record<LiveNamespaceKey, string | null>>>;
    timeline: readonly LiveNamespaceTimelineEntry[];
    metrics: Readonly<Record<string, LiveNamespaceMetric>>;
    startedAt: number;
    lastUpdated: number;
}

export interface LiveDiagnosticsMetricUpdate {
    [key: string]: number | undefined;
    triggered?: number;
    failed?: number;
    cacheHit?: number;
}

export interface LiveDiagnosticsStopwatch {
    succeed: (extra?: LiveDiagnosticsMetricUpdate) => void;
    fail: (extra?: LiveDiagnosticsMetricUpdate) => void;
    cancel: () => void;
    markRetry: () => void;
}

export const LIVE_DIAGNOSTICS_WINDOW_MS = 15 * 60 * 1000;
const META_KEY = Symbol('dashboardLiveMeta');

export interface LiveNamespaceHandle {
    readonly live: LiveDashboardNamespace;
    readonly get: <K extends LiveNamespaceKey>(key: K) => LiveNamespaceValue<K> | undefined;
    readonly set: <K extends LiveNamespaceKey>(key: K, value: LiveNamespaceValue<K>) => void;
    readonly ensure: <K extends LiveNamespaceKey>(key: K) => LiveNamespaceValue<K>;
}

export type LiveNamespaceValue<K extends LiveNamespaceKey> = K extends 'cards'
    ? LiveCardsApi
    : K extends 'time'
      ? LiveTimeApi
      : K extends 'summary'
        ? LiveSummaryApi
        : LiveUpdatesApi;

export type LiveNamespaceOwner = Window & {
    DashboardLive?: LiveDashboardNamespace;
    DashboardLiveCards?: LiveCardsApi;
    DashboardLiveTime?: LiveTimeApi;
    DashboardLiveSummary?: LiveSummaryApi;
    DashboardLiveUpdates?: LiveUpdatesApi;
    DashboardLiveDiagnostics?: LiveNamespaceDiagnostics;
};

type OwnerApiKey = 'DashboardLiveCards' | 'DashboardLiveTime' | 'DashboardLiveSummary' | 'DashboardLiveUpdates';

const ownerKeys: Record<LiveNamespaceKey, OwnerApiKey> = {
    cards: 'DashboardLiveCards',
    time: 'DashboardLiveTime',
    summary: 'DashboardLiveSummary',
    updates: 'DashboardLiveUpdates',
};

const DEPENDENCIES: Record<LiveNamespaceKey, ReadonlyArray<LiveNamespaceKey>> = {
    time: [],
    cards: ['time'],
    summary: ['cards', 'time'],
    updates: ['cards', 'time'],
};

export interface LiveNamespaceMetricWindowSample {
    timestamp: number;
    triggered: number;
    failed: number;
    cacheHit: number;
}

export interface LiveNamespaceMetricExtraSample {
    timestamp: number;
    value: number;
}

interface LiveNamespaceMetaEvent {
    key: LiveNamespaceKey;
    provider: string | null;
    timestamp: number;
}

export interface LiveNamespaceMeta {
    order: LiveNamespaceKey[];
    providers: Partial<Record<LiveNamespaceKey, string | null>>;
    events: LiveNamespaceMetaEvent[];
    startedAt: number;
    lastUpdated: number;
    metrics: Record<string, LiveNamespaceMetric>;
    metricWindows: Record<string, LiveNamespaceMetricWindowSample[]>;
    extraWindows: Record<string, Record<string, LiveNamespaceMetricExtraSample[]>>;
}

type NamespaceWithMeta = LiveDashboardNamespace & { [META_KEY]?: LiveNamespaceMeta };

function attachOwnerNamespaceMirrors(owner: LiveNamespaceOwner, namespace: LiveDashboardNamespace): void {
    for (const [key, ownerKey] of Object.entries(ownerKeys) as Array<[LiveNamespaceKey, OwnerApiKey]>) {
        const descriptor = Object.getOwnPropertyDescriptor(owner, ownerKey);
        if (descriptor && !descriptor.configurable) {
            continue;
        }

        Object.defineProperty(owner, ownerKey, {
            configurable: true,
            enumerable: false,
            get() {
                return Reflect.get(namespace as object, key);
            },
            set(_value) {
                throw new Error(
                    `[DashboardLive] ${ownerKey} must be registered via registerLiveApi; direct assignment is not supported`,
                );
            },
        });
    }
}

export function getTimestamp(): number {
    if (typeof performance !== 'undefined' && typeof performance.now === 'function') {
        return performance.now();
    }
    return Date.now();
}

export function getNamespaceMeta(namespace: LiveDashboardNamespace): LiveNamespaceMeta {
    const store = namespace as NamespaceWithMeta;
    let meta = store[META_KEY];
    if (!meta) {
        const now = getTimestamp();
        meta = {
            order: [],
            providers: {},
            events: [],
            startedAt: now,
            lastUpdated: now,
            metrics: {},
            metricWindows: {},
            extraWindows: {},
        };
        store[META_KEY] = meta;
    } else {
        if (!meta.metricWindows) {
            meta.metricWindows = {};
        }
        if (!meta.extraWindows) {
            meta.extraWindows = {};
        }
    }
    return meta;
}

export function ensureLiveNamespace(owner: LiveNamespaceOwner = window as LiveNamespaceOwner): LiveNamespaceHandle {
    let namespace = owner.DashboardLive;
    if (!namespace) {
        namespace = {} as LiveDashboardNamespace;
        owner.DashboardLive = namespace;
        attachOwnerNamespaceMirrors(owner, namespace);
    } else {
        attachOwnerNamespaceMirrors(owner, namespace);
    }

    return {
        live: namespace,
        get(key) {
            return Reflect.get(namespace as object, key) as LiveNamespaceValue<typeof key> | undefined;
        },
        set(key, value) {
            Reflect.set(namespace as object, key, value);
            const meta = getNamespaceMeta(namespace);
            if (!meta.order.includes(key)) {
                meta.order.push(key);
            }
        },
        ensure(key) {
            const existing = this.get(key);
            if (existing) {
                return existing;
            }
            throw new Error(`DashboardLive ${key} API is not registered`);
        },
    } satisfies LiveNamespaceHandle;
}

export function resetLiveNamespace(owner: LiveNamespaceOwner = window as LiveNamespaceOwner): void {
    delete owner.DashboardLive;
    delete owner.DashboardLiveCards;
    delete owner.DashboardLiveTime;
    delete owner.DashboardLiveSummary;
    delete owner.DashboardLiveUpdates;
    delete owner.DashboardLiveDiagnostics;
}

export function requireLiveApi<K extends LiveNamespaceKey>(
    owner: LiveNamespaceOwner,
    key: K,
    message?: string,
): LiveNamespaceValue<K> {
    const handle = ensureLiveNamespace(owner);
    const value = handle.get(key);
    if (!value) {
        throw new Error(message ?? `DashboardLive ${key} API is not registered`);
    }
    return value;
}

export interface RegisterLiveApiOptions {
    provider?: string;
}

export function registerLiveApi<K extends LiveNamespaceKey>(
    owner: LiveNamespaceOwner,
    key: K,
    value: LiveNamespaceValue<K>,
    options: RegisterLiveApiOptions = {},
): LiveNamespaceValue<K> {
    const handle = ensureLiveNamespace(owner);
    const namespace = handle.live;
    const meta = getNamespaceMeta(namespace);

    const existing = handle.get(key);
    if (existing) {
        if (existing !== value) {
            const provider = meta.providers[key];
            const order = meta.order.length ? meta.order.join(' -> ') : '(none)';
            const suffix = provider ? ` by ${provider}` : '';
            throw new Error(`DashboardLive ${key} API is already registered${suffix}. Registered APIs: ${order}`);
        }
        return existing;
    }

    for (const dependency of DEPENDENCIES[key]) {
        const depValue = handle.get(dependency);
        if (!depValue) {
            const order = meta.order.length ? meta.order.join(' -> ') : '(none)';
            throw new Error(
                `DashboardLive ${key} requires ${dependency} to be registered first. Registered APIs: ${order}`,
            );
        }
    }

    handle.set(key, value);
    const previousProvider = meta.providers[key] ?? null;
    const resolvedProvider = options.provider ?? previousProvider;
    meta.providers[key] = resolvedProvider;
    const timestamp = getTimestamp();
    meta.events.push({ key, provider: resolvedProvider ?? null, timestamp });
    meta.lastUpdated = timestamp;
    refreshLiveNamespaceDiagnostics(owner, meta);
    return value;
}
