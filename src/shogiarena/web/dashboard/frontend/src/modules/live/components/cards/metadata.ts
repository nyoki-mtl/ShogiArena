import type { DashboardCoreState } from '@/types/dashboard';

export type GameMeta = {
    variantId?: string | null;
    phase?: string | null;
    blackPlayer?: string | null;
    whitePlayer?: string | null;
};

// `gameMetadata` can grow unbounded over long runs because game ids are effectively unique.
// Keep it bounded; stale entries can always be re-hydrated from the games list API.
const GAME_METADATA_MAX = 2000;

function ensureStore(state: DashboardCoreState): Map<string, GameMeta> {
    if (state.gameMetadata instanceof Map) {
        return state.gameMetadata as Map<string, GameMeta>;
    }
    const store = new Map<string, GameMeta>();
    state.gameMetadata = store;
    return store;
}

export function getGameMetadataStore(state: DashboardCoreState): Map<string, GameMeta> {
    return ensureStore(state);
}

export function setGameMetadataEntry(
    store: Map<string, GameMeta>,
    gameId: string,
    meta: GameMeta | null | undefined,
): void {
    const id = gameId.trim();
    if (!id) return;
    const current = store.get(id) ?? {};
    const next: GameMeta = { ...current };
    if (meta) {
        if (meta.variantId !== undefined) next.variantId = meta.variantId ?? null;
        if (meta.phase !== undefined) next.phase = meta.phase ?? null;
        if (meta.blackPlayer !== undefined) next.blackPlayer = meta.blackPlayer ?? null;
        if (meta.whitePlayer !== undefined) next.whitePlayer = meta.whitePlayer ?? null;
    }
    // Refresh insertion order to behave like a simple LRU.
    if (store.has(id)) {
        store.delete(id);
    }
    store.set(id, next);
    while (store.size > GAME_METADATA_MAX) {
        const iter = store.keys().next();
        if (iter.done) break;
        const oldestKey = iter.value;
        store.delete(oldestKey);
    }
}

export function getGameMetadataEntry(store: Map<string, GameMeta>, gameId: string): GameMeta | null {
    if (!gameId) return null;
    return store.get(gameId) ?? null;
}

function phaseSymbol(phase: string | null | undefined): string {
    if (!phase) return '';
    if (phase === 'plus') return '+';
    if (phase === 'minus') return '-';
    return '';
}

export function formatVariantDisplay(
    variantId: string | null | undefined,
    phase: string | null | undefined,
): string | null {
    const symbol = phaseSymbol(phase);
    if (variantId && symbol) return `${variantId}${symbol}`;
    if (variantId) return variantId;
    return null;
}
