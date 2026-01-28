import { resolveApiBase } from '@/modules/shared';
import { requestJson } from '@/modules/shared/services/api';
import type { TournamentSummary } from '@/modules/tournament/types';
import type { ArenaDashboardWindow } from '@/types/globals';
import type { JsonObject } from '@/types/shared';
import type { DashboardFeatureOverrides, DashboardInitialData } from '@/bootstrap';
import type { DashboardRuntimeMode } from '@/types/dashboard';

type DashboardProfile = 'tournament' | 'spsa' | 'match' | 'sprt';

const PROFILE_RUNTIME_MODE: Record<DashboardProfile, DashboardRuntimeMode> = {
    tournament: 'tournament',
    spsa: 'spsa',
    match: 'match',
    sprt: 'sprt',
};

const PROFILE_FEATURE_OVERRIDES: Record<DashboardProfile, DashboardFeatureOverrides> = {
    tournament: {
        spsa: false,
        match: false,
        sprt: false,
    },
    spsa: {
        games: false,
        match: false,
        sprt: false,
        tournament: {
            state: false,
            data: false,
            matchups: false,
            openings: false,
            standings: false,
            bootstrap: false,
        },
    },
    match: {
        spsa: false,
        sprt: false,
        tournament: {
            state: false,
            data: false,
            matchups: false,
            openings: false,
            standings: false,
            bootstrap: false,
        },
    },
    sprt: {
        spsa: false,
        match: false,
        tournament: {
            state: false,
            data: false,
            matchups: false,
            openings: false,
            standings: false,
            bootstrap: false,
        },
    },
};

export interface DashboardProfileConfiguration {
    profile: DashboardProfile;
    features: DashboardFeatureOverrides;
    runtimeMode: DashboardRuntimeMode;
}

function resolveDashboardProfile(documentRef: Document | null | undefined): DashboardProfile {
    const profile = documentRef?.body?.dataset?.dashboardProfile ?? '';
    const normalized = profile.trim().toLowerCase();
    if (normalized === 'spsa') return 'spsa';
    if (normalized === 'match') return 'match';
    if (normalized === 'sprt') return 'sprt';
    return 'tournament';
}

export function resolveProfileConfiguration(documentRef: Document | null | undefined): DashboardProfileConfiguration {
    const profile = resolveDashboardProfile(documentRef);
    return {
        profile,
        features: PROFILE_FEATURE_OVERRIDES[profile],
        runtimeMode: PROFILE_RUNTIME_MODE[profile],
    };
}

function isJsonObject(value: unknown): value is JsonObject {
    return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function ensureApiPort(owner: ArenaDashboardWindow): number {
    const raw = owner.ARENA_API_PORT;
    const normalized = Number(raw);
    if (!Number.isFinite(normalized) || normalized <= 0) {
        throw new Error('Failed to load initial dashboard data: ARENA_API_PORT must be a positive number');
    }
    return Math.trunc(normalized);
}

function assignApiPort(owner: ArenaDashboardWindow, apiPort: number): void {
    owner.ARENA_API_PORT = apiPort;
    if (typeof window !== 'undefined' && window !== owner) {
        (window as ArenaDashboardWindow).ARENA_API_PORT = apiPort;
    }
}

function resolveSummaryEndpoint(runtimeMode: string | null | undefined): string {
    const normalized = (runtimeMode ?? '').toLowerCase();
    if (normalized === 'spsa') return '/api/spsa/summary';
    if (normalized === 'sprt') return '/api/sprt/summary';
    if (normalized === 'match') return '/api/match/summary';
    return '/api/summary';
}

async function fetchSummaryFromApi(runtimeMode?: string | null): Promise<TournamentSummary> {
    if (typeof fetch !== 'function') {
        throw new Error('Failed to fetch summary: fetch API is not available');
    }

    const apiBase = resolveApiBase();

    try {
        const endpoint = resolveSummaryEndpoint(runtimeMode);
        const data = (await requestJson<unknown>(`${apiBase}${endpoint}`, { cache: 'no-cache' })) as unknown;
        if (!isJsonObject(data)) {
            throw new Error('Failed to load summary from API: response is not a valid object');
        }
        return data as TournamentSummary;
    } catch (error) {
        throw new Error('Failed to load summary from API', { cause: error });
    }
}

export async function loadInitialDashboardData(
    owner: ArenaDashboardWindow,
    runtimeMode?: string | null,
): Promise<DashboardInitialData> {
    if (!owner) {
        throw new Error('Failed to load initial dashboard data: owner window is not provided');
    }

    const initialData: DashboardInitialData = {};

    const apiPort = ensureApiPort(owner);
    initialData.apiPort = apiPort;
    assignApiPort(owner, apiPort);

    const summary = await fetchSummaryFromApi(runtimeMode);
    initialData.summary = summary;

    return initialData;
}
