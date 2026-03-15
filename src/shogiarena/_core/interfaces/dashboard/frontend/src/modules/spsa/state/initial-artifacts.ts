import { INITIAL_UPDATE_IDX } from '@/modules/spsa/types';
import type {
    NormalizedSpsaParams,
    NormalizedSpsaUpdateDetail,
    NormalizedSpsaUpdateEntry,
    SpsaUpdateDetailResponse,
    SpsaUpdateEntry,
} from '@/modules/spsa/types';
import type { JsonObject } from '@/types/shared';

import { state } from './store';

type InitialParamsRecord = Record<string, number>;

export function clearInitialUpdateArtifacts(): void {
    state.updates.initialEntry = null;
    state.updates.initialDetail = null;
    state.updates.cache.delete(INITIAL_UPDATE_IDX);
    state.updates.detailCache.delete(INITIAL_UPDATE_IDX);
    state.updates.expanded.delete(INITIAL_UPDATE_IDX);
}

export function recomputeInitialUpdateArtifacts(): void {
    const paramsData = state.params.data ?? null;
    const initialParams = deriveInitialParamsRecord(paramsData);
    if (!initialParams) {
        clearInitialUpdateArtifacts();
        return;
    }

    const variantId = 'v000000';
    const hasInitialVariant = Boolean(paramsData?.initialVariantId ?? paramsData?.variantId);
    const syntheticPayload: JsonObject = {
        synthetic: 'initial',
        source: hasInitialVariant ? 'initial' : 'current',
    };

    const rawEntry: SpsaUpdateEntry = {
        update_idx: INITIAL_UPDATE_IDX,
        params: { ...initialParams },
        variant_id: variantId,
        wins: 0,
        losses: 0,
        draws: 0,
        payload: syntheticPayload,
        perturbations: { plus: {}, minus: {} },
        pending: false,
    };

    const entry: NormalizedSpsaUpdateEntry = {
        raw: rawEntry,
        updateIdx: INITIAL_UPDATE_IDX,
        timestamp: null,
        startedAt: null,
        endedAt: null,
        deltaNorm: null,
        deltaStep: null,
        step: null,
        sPlus: null,
        sMinus: null,
        gamesCompleted: 0,
        gradients: {},
        params: { ...initialParams },
        deltas: {},
        payload: syntheticPayload,
        variantId,
        perturbations: { plus: {}, minus: {} },
        isPending: false,
        gainCk: null,
        hasLtcRegression: false,
        gainAk: null,
        ltcRegression: null,
        ltcRejected: false,
        ltcRevertedTo: null,
        wins: 0,
        losses: 0,
        draws: 0,
        btdElo: null,
        isInitial: true,
        phaseWdl: {
            plus: { wins: 0, losses: 0, draws: 0 },
            minus: { wins: 0, losses: 0, draws: 0 },
        },
    };

    const detailRaw: SpsaUpdateDetailResponse = {
        update_idx: INITIAL_UPDATE_IDX,
        engines: { baseline: null, tuned: null },
        wdl: { wins: 0, losses: 0, draws: 0 },
        variant_id: variantId,
        params: { ...initialParams },
        gradients: {},
        deltas: {},
        s_plus: null,
        s_minus: null,
        step: null,
        games: [],
        games_count: 0,
        ltc_games: [],
        ltc_games_count: 0,
        payload: syntheticPayload,
    };

    const detail: NormalizedSpsaUpdateDetail = {
        raw: detailRaw,
        updateIdx: INITIAL_UPDATE_IDX,
        engines: { baseline: null, tuned: null },
        wdl: { wins: 0, losses: 0, draws: 0 },
        variantId,
        params: { ...initialParams },
        gradients: {},
        deltas: {},
        sPlus: null,
        sMinus: null,
        step: null,
        perturbations: { plus: {}, minus: {} },
        gainCk: null,
        gainAk: null,
        isPending: false,
        games: [],
        gamesCount: 0,
        ltcGames: [],
        ltcGamesCount: 0,
        payload: syntheticPayload,
        isInitial: true,
        phaseWdl: {
            plus: { wins: 0, losses: 0, draws: 0 },
            minus: { wins: 0, losses: 0, draws: 0 },
        },
        ltcRegression: null,
        hasLtcRegression: false,
        view: 'full',
        detailWindow: 'full',
        loadedIncludes: [],
        availableIncludes: [],
    };

    state.updates.initialEntry = entry;
    state.updates.initialDetail = detail;
    state.updates.cache.set(INITIAL_UPDATE_IDX, entry);
    state.updates.detailCache.set(INITIAL_UPDATE_IDX, detail);
}

function ensureInitialUpdateArtifacts(): void {
    if (state.updates.initialEntry && state.updates.initialDetail) {
        return;
    }
    recomputeInitialUpdateArtifacts();
}

export function getInitialUpdateDetail(): NormalizedSpsaUpdateDetail | null {
    ensureInitialUpdateArtifacts();
    return state.updates.initialDetail ?? null;
}

function deriveInitialParamsRecord(params: NormalizedSpsaParams | null): InitialParamsRecord | null {
    if (!params) return null;
    const initial = params.initialParams;
    const record: InitialParamsRecord = {};
    if (!initial || Object.keys(initial).length === 0) {
        return record;
    }
    const usedNames = new Set<string>();

    for (const entry of params.params ?? []) {
        if (!entry?.name || entry.notUsed) continue;
        usedNames.add(entry.name);
    }

    for (const [key, value] of Object.entries(initial)) {
        if (usedNames.size > 0 && !usedNames.has(key)) continue;
        const num = typeof value === 'number' ? value : Number(value);
        if (Number.isFinite(num)) {
            record[key] = num;
        }
    }

    return record;
}
