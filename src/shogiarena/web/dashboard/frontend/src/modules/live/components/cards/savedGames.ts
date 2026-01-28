import type { LiveCardId, LiveCardState, LiveGameRecord, LiveViewSnapshot } from '@/modules/live/types';
import type { DashboardCore } from '@/types/dashboard';
import type { JsonObject, RequestError } from '@/types/shared';
import { requestJson } from '@/modules/shared/services/api';
import { normalizeLiveGameRecord, normalizeLiveViewSnapshot } from '@/modules/live/services/updates/normalizers';
import { recordLiveDiagnosticsMetric, startDiagnosticsStopwatch } from '@/modules/live/utils/liveNamespace';
import { getResumeCoordinator } from '@/modules/shared/services/resumeCoordinator';
import type { GameMeta } from './metadata';
import type { WorkerSnapshotRecord } from './types';

type GamesListItem = {
    game_id?: string | number | null;
    black_player?: string | null;
    white_player?: string | null;
    variant_id?: string | null;
    phase?: string | null;
    [key: string]: unknown;
};

type GamesListResponse = {
    games?: GamesListItem[];
    signature?: string | null;
    source?: string | null;
    total?: number | null;
    limit?: number | null;
    liveView?: LiveViewSnapshot | null;
    [key: string]: unknown;
};

type SavedGamesDeps = {
    state: DashboardCore['state'];
    events: DashboardCore['events'];
    warnSoftFailure: DashboardCore['warnSoftFailure'];
    showApiError: DashboardCore['showApiError'];
    notifyDashboardServerStopped: () => void;
    getApiBase: DashboardCore['getApiBase'];
    liveViewStore: ReturnType<typeof import('@/modules/shared/stores/liveViewSnapshot')['getLiveViewSnapshotStore']>;
    setGameMetadata: (gameId: string, meta: GameMeta) => void;
    findCardById: (cardId: LiveCardId) => LiveCardState | undefined;
    workerLatestLabel: (idx: number) => string;
    formatVariantDisplay: (variantId: string | null, phase: string | null) => string;
    updateCardHeaderTitle: (cardState: LiveCardState) => void;
    owner: Window & { ARENA_DASHBOARD_STOPPED?: boolean };
};

export function createSavedGamesController(deps: SavedGamesDeps) {
    const {
        state,
        events,
        warnSoftFailure,
        showApiError,
        notifyDashboardServerStopped,
        getApiBase,
        liveViewStore,
        setGameMetadata,
        findCardById,
        workerLatestLabel,
        formatVariantDisplay,
        updateCardHeaderTitle,
        owner,
    } = deps;

    const SAVED_GAMES_HYDRATION_METRIC = 'live:hydration:saved-games';
    const SAVED_GAMES_CACHE_TTL_MS = 60_000;
    const GAME_DATA_CACHE_MAX = 200;
    const resumeCoordinator = getResumeCoordinator(owner);

    let cachedSavedGames: GamesListItem[] | null = null;
    let cachedSavedGamesFetchedAt = 0;
    let cachedSavedGamesPromise: Promise<GamesListItem[]> | null = null;
    let savedGamesFetchWarned = false;

    const recordSavedGamesMetric = (update: { triggered?: number; failed?: number; cacheHit?: number }) => {
        recordLiveDiagnosticsMetric(SAVED_GAMES_HYDRATION_METRIC, update);
    };
    const recordSavedGamesCacheHit = () => recordSavedGamesMetric({ cacheHit: 1 });

    const getGameMetadata = (gameId: string): GameMeta | undefined => state.gameMetadata?.get(gameId);

    const setGameMetadataEntry = (gameId: string, meta: GameMeta): void => {
        setGameMetadata(gameId, meta);
    };

    const getStateSavedGames = (): GamesListItem[] | null => {
        if (Array.isArray(state.gamesList) && state.gamesList.length > 0) {
            return state.gamesList as unknown as GamesListItem[];
        }
        if (Array.isArray(state.gamesListCache) && state.gamesListCache.length > 0) {
            return state.gamesListCache as unknown as GamesListItem[];
        }
        return null;
    };

    const getCachedSavedGames = (): GamesListItem[] | null => {
        const fromState = getStateSavedGames();
        if (fromState) {
            return fromState;
        }
        if (!cachedSavedGames) return null;
        if (Date.now() - cachedSavedGamesFetchedAt > SAVED_GAMES_CACHE_TTL_MS) {
            cachedSavedGames = null;
            cachedSavedGamesFetchedAt = 0;
            return null;
        }
        return cachedSavedGames;
    };

    const persistSavedGamesCache = (games: GamesListItem[]): void => {
        cachedSavedGames = games;
        cachedSavedGamesFetchedAt = Date.now();
        state.gamesList = games as unknown as typeof state.gamesList;
        state.gamesListCache = games as unknown as typeof state.gamesListCache;
    };

    const updateLiveViewSnapshot = (liveView: LiveViewSnapshot | null | undefined): void => {
        const normalized = normalizeLiveViewSnapshot(liveView, 'cards.liveView_fallback');
        if (!normalized) {
            return;
        }
        liveViewStore.publishSnapshot(normalized);
        if (normalized.progress) {
            events.emit?.('live:progress', {
                source: 'live_api',
                mode: normalized.mode,
                progress: normalized.progress,
            });
        }
    };

    const applySavedGamesResponse = (payload: GamesListResponse | null | undefined): GamesListItem[] => {
        const list = Array.isArray(payload?.games) ? payload?.games : [];
        const seenOrder = Array.isArray(list)
            ? list
                  .map((game) => game?.game_id ?? '')
                  .filter((value): value is string | number => Boolean(value))
                  .map((value) => String(value))
            : [];
        const order = seenOrder.length > 0 ? seenOrder : undefined;
        const aggregated = new Map<string, GamesListItem>();
        const setGameMetadata = (gid: string, meta: GameMeta) => {
            const existing = aggregated.get(gid) ?? {};
            aggregated.set(gid, { ...existing, game_id: gid, ...meta });
        };
        const addGame = (record: GamesListItem) => {
            const gid = record.game_id;
            const key = gid == null ? '' : String(gid).trim();
            if (!key) return;
            const meta: GameMeta = {};
            if ('variant_id' in record && record.variant_id !== undefined) {
                meta.variantId = record.variant_id ?? null;
            }
            if ('phase' in record && record.phase !== undefined) {
                meta.phase = record.phase ?? null;
            }
            if ('black_player' in record && record.black_player !== undefined) {
                meta.blackPlayer = (record.black_player as string | null) ?? null;
            } else if ((record as { blackPlayer?: string | null }).blackPlayer !== undefined) {
                meta.blackPlayer = (record as { blackPlayer?: string | null }).blackPlayer ?? null;
            }
            if ('white_player' in record && record.white_player !== undefined) {
                meta.whitePlayer = (record.white_player as string | null) ?? null;
            } else if ((record as { whitePlayer?: string | null }).whitePlayer !== undefined) {
                meta.whitePlayer = (record as { whitePlayer?: string | null }).whitePlayer ?? null;
            }
            if (Object.keys(meta).length > 0) {
                setGameMetadata(key, meta);
            }
            aggregated.set(key, { ...record, game_id: key });
        };
        list.forEach(addGame);
        const games = order
            ? order.map((gid) => aggregated.get(gid)).filter((entry): entry is GamesListItem => Boolean(entry))
            : Array.from(aggregated.values());
        return games;
    };

    async function fetchSavedGamesListFromApi(): Promise<GamesListItem[]> {
        const API_BASE = getApiBase();
        const liveView = liveViewStore.getSnapshot();
        let runtimeMode = liveView?.mode ?? state.runtimeMode ?? 'unknown';
        if (runtimeMode === 'unknown' && state.spsaMode) {
            runtimeMode = 'spsa';
        }
        const endpoint =
            runtimeMode === 'tournament'
                ? `${API_BASE}/api/tournament/match-history?limit=500`
                : runtimeMode === 'spsa'
                  ? `${API_BASE}/api/spsa/games?limit=500`
                  : `${API_BASE}/api/games?limit=500`;
        const attempt = startDiagnosticsStopwatch(SAVED_GAMES_HYDRATION_METRIC);
        try {
            const payload = await requestJson<GamesListResponse>(endpoint);
            const games = applySavedGamesResponse(payload);
            if (typeof payload?.signature === 'string') {
                state.gamesSignature = payload.signature;
            }
            persistSavedGamesCache(games);
            updateLiveViewSnapshot(payload?.liveView ?? null);
            attempt.succeed();
            return games;
        } catch (err) {
            const requestError = err as RequestError | undefined;
            if (requestError?.status) {
                const payload = requestError.payload;
                const message = {
                    endpoint,
                    status: requestError.status,
                    message: requestError.message,
                    payload: payload ?? null,
                };
                showApiError('Failed to fetch games list', message);
            } else if (err instanceof TypeError) {
                notifyDashboardServerStopped();
            } else {
                showApiError('Network error while fetching games list', {
                    endpoint,
                    error: String(err),
                });
            }
            attempt.fail();
            throw err;
        }
    }

    async function fetchSavedGamesList(force = false): Promise<GamesListItem[]> {
        if (!force) {
            const cached = getCachedSavedGames();
            if (cached) {
                recordSavedGamesCacheHit();
                return cached;
            }
            if (cachedSavedGamesPromise) {
                return cachedSavedGamesPromise;
            }
        }

        const fetchPromise = fetchSavedGamesListFromApi().finally(() => {
            if (cachedSavedGamesPromise === fetchPromise) {
                cachedSavedGamesPromise = null;
            }
        });

        cachedSavedGamesPromise = fetchPromise;
        return fetchPromise;
    }

    async function loadSavedGamesList(force = false): Promise<GamesListItem[]> {
        const cachedSnapshot = getStateSavedGames();
        if (cachedSnapshot && !force) {
            recordSavedGamesCacheHit();
            return cachedSnapshot as GamesListItem[];
        }
        if (!force) {
            const cached = getCachedSavedGames();
            if (cached) {
                recordSavedGamesCacheHit();
                return cached;
            }
        }
        return fetchSavedGamesList(force);
    }

    const deriveSavedGamesSignature = (games: GamesListItem[]): string => {
        const rawSignature = typeof state.gamesSignature === 'string' ? state.gamesSignature.trim() : '';
        if (rawSignature) {
            return rawSignature;
        }
        const count = games.length;
        const first = count > 0 ? String(games[0]?.game_id ?? '') : '';
        const last = count > 0 ? String(games[count - 1]?.game_id ?? '') : '';
        return `fallback:${count}:${first}:${last}`;
    };

    function fullGameOptionLabel(game: GamesListItem, idx?: number): string {
        const gid = game.game_id == null ? '' : String(game.game_id);
        const normalizeDisplayName = (value: unknown, fallback: string): string => {
            if (typeof value === 'string') {
                const trimmed = value.trim();
                if (trimmed) return trimmed;
            }
            return fallback;
        };
        const record = game as JsonObject;
        const meta = getGameMetadata(gid);
        const blackName = normalizeDisplayName(
            game.black_player ?? record.blackPlayer ?? meta?.blackPlayer,
            meta?.blackPlayer ?? 'Black',
        );
        const whiteName = normalizeDisplayName(
            game.white_player ?? record.whitePlayer ?? meta?.whitePlayer,
            meta?.whitePlayer ?? 'White',
        );
        const variantLabel = formatVariantDisplay(
            game.variant_id ?? (record.variantId as string | null | undefined) ?? meta?.variantId ?? null,
            game.phase ?? (record.phase as string | null | undefined) ?? meta?.phase ?? null,
        );
        const baseLabel = gid || variantLabel || `game_${String(idx ?? 0).padStart(4, '0')}`;
        return `${baseLabel}: ${blackName} vs ${whiteName}`;
    }

    function savedGameOptionLabel(game: GamesListItem, idx: number): string {
        const gid = game.game_id == null ? '' : String(game.game_id);
        if (gid) return gid;
        return `game_${String(idx).padStart(4, '0')}`;
    }

    async function populateSourceDropdown(cardId: LiveCardId, options?: { bypassDefer?: boolean }): Promise<void> {
        if (!options?.bypassDefer && resumeCoordinator.isCritical()) {
            resumeCoordinator.defer('live.cards.populate_sources', () => {
                void populateSourceDropdown(cardId, { bypassDefer: true });
            });
            return;
        }
        const startMs =
            typeof performance !== 'undefined' && typeof performance.now === 'function'
                ? performance.now()
                : Date.now();
        const select = document.getElementById(`source-${cardId}`) as HTMLSelectElement | null;
        if (!select) return;

        const cardState = findCardById(cardId);
        if (!cardState) return;

        const cardKey = String(cardId);

        const dashboardStopped = owner.ARENA_DASHBOARD_STOPPED === true;
        if (savedGamesFetchWarned || state.offlineNotified || dashboardStopped) {
            return;
        }

        let games: GamesListItem[] = [];
        let usedCache = false;
        if (Array.isArray(state.gamesList) && state.gamesList.length > 0) {
            games = state.gamesList as unknown as GamesListItem[];
            usedCache = true;
        } else {
            if (state.offlineNotified || dashboardStopped) {
                savedGamesFetchWarned = true;
                notifyDashboardServerStopped();
                return;
            }
            try {
                games = await loadSavedGamesList(false);
            } catch (error) {
                savedGamesFetchWarned = true;
                warnSoftFailure('Failed to fetch games list for source dropdown', error);
                notifyDashboardServerStopped();
                return;
            }
        }

        const normalizedGames = games.map((game) => {
            const gid = game.game_id == null ? '' : String(game.game_id).trim();
            return { ...game, game_id: gid };
        });

        normalizedGames.forEach((game) => {
            const gid = game.game_id == null ? '' : String(game.game_id);
            if (!gid) return;
            const meta: GameMeta = {};
            if ('variant_id' in game && game.variant_id !== undefined) {
                meta.variantId = game.variant_id ?? null;
            }
            if ('phase' in game && game.phase !== undefined) {
                meta.phase = game.phase ?? null;
            }
            if ('black_player' in game && game.black_player !== undefined) {
                meta.blackPlayer = game.black_player ?? null;
            }
            if ('white_player' in game && game.white_player !== undefined) {
                meta.whitePlayer = game.white_player ?? null;
            }
            if (Object.keys(meta).length > 0) {
                setGameMetadataEntry(gid, meta);
            }
        });

        if (!(state.gamesSignatureByCard instanceof Map)) {
            state.gamesSignatureByCard = new Map();
        }
        const hasSavedGroup = !!select.querySelector('optgroup[label="Saved Games"]');
        const signature = deriveSavedGamesSignature(normalizedGames);
        const cardSignature = `${signature}:${normalizedGames.length}`;
        const prevSig = state.gamesSignatureByCard.get(cardKey);
        if (prevSig === cardSignature && hasSavedGroup) {
            return;
        }

        select.innerHTML = '';

        if (cardState.source === 'empty') {
            const placeholder = document.createElement('option');
            placeholder.value = 'empty';
            placeholder.textContent = 'Start position (Initial)';
            placeholder.disabled = true;
            placeholder.selected = true;
            select.appendChild(placeholder);
        }

        const workerGroup = document.createElement('optgroup');
        workerGroup.label = 'Worker Latest';
        for (let i = 0; i < state.numWorkers; i++) {
            const option = document.createElement('option');
            option.value = `worker-latest:${i}`;
            const label = workerLatestLabel(i);
            option.textContent = label;
            option.title = label;
            if (cardState.source === option.value) {
                option.selected = true;
            }
            workerGroup.appendChild(option);
        }
        select.appendChild(workerGroup);

        if (normalizedGames.length > 0) {
            const gamesGroup = document.createElement('optgroup');
            gamesGroup.label = 'Saved Games';
            normalizedGames.forEach((game, idx) => {
                const option = document.createElement('option');
                const rawGameId = game.game_id;
                const gameIdString = rawGameId == null ? '' : String(rawGameId);
                option.value = `db-game:${gameIdString}`;
                option.textContent = savedGameOptionLabel(game, idx);
                option.title = fullGameOptionLabel(game, idx);
                if (typeof game.variant_id === 'string' && game.variant_id) {
                    option.dataset.variantId = game.variant_id;
                }
                if (typeof game.phase === 'string' && game.phase) {
                    option.dataset.phase = game.phase;
                }
                if (cardState.source === option.value) {
                    option.selected = true;
                }
                gamesGroup.appendChild(option);
            });
            select.appendChild(gamesGroup);
        }

        state.gamesSignatureByCard.set(cardKey, cardSignature);

        const selected = select.options[select.selectedIndex];
        if (selected) {
            const baseTitle = selected.title || selected.textContent || '';
            const variantHint = selected.dataset.variantId;
            if (variantHint && !baseTitle.includes(variantHint)) {
                select.title = `${variantHint} – ${baseTitle}`.trim();
            } else {
                select.title = baseTitle;
            }
        }

        updateCardHeaderTitle(cardState);
        const endMs =
            typeof performance !== 'undefined' && typeof performance.now === 'function'
                ? performance.now()
                : Date.now();
        recordLiveDiagnosticsMetric('live.cards.populate_sources', {
            triggered: 1,
            total_ms: Math.max(0, endMs - startMs),
            games: normalizedGames.length,
            from_cache: usedCache ? 1 : 0,
        });
    }

    async function getGameData(gameId: string | number): Promise<WorkerSnapshotRecord | null> {
        const cacheKey = String(gameId);
        if (state.gameDataCache.has(cacheKey)) {
            const cached = state.gameDataCache.get(cacheKey) as WorkerSnapshotRecord;
            // Refresh LRU position
            state.gameDataCache.delete(cacheKey);
            state.gameDataCache.set(cacheKey, cached);
            return cached;
        }

        const API_BASE = getApiBase();
        const endpoint = state.spsaMode ? `${API_BASE}/api/spsa/game/${gameId}` : `${API_BASE}/api/game/${gameId}`;

        let game: LiveGameRecord | null = null;
        try {
            game = await requestJson<LiveGameRecord>(endpoint);
        } catch (error) {
            const requestError = error as RequestError | undefined;
            if (requestError?.status) {
                const payload = requestError.payload;
                const bodyText =
                    typeof payload === 'string' ? payload : payload != null ? JSON.stringify(payload) : '(no body)';
                const detail = {
                    endpoint,
                    status: requestError.status,
                    message: requestError.message,
                    body: bodyText,
                };
                showApiError('Failed to fetch game data from API', detail);
                throw new Error(`Failed to fetch game data from API (HTTP ${requestError.status})`, {
                    cause: requestError,
                });
            }

            const detail = {
                endpoint,
                error: error instanceof Error ? error.message : String(error),
            };
            showApiError('Network error while fetching game data', detail);
            if (error instanceof TypeError) {
                notifyDashboardServerStopped();
            }
            throw new Error('Network error while fetching game data', { cause: error });
        }
        if (!game) {
            return null;
        }
        const normalized = normalizeLiveGameRecord(game);
        const cached: WorkerSnapshotRecord = {
            ...normalized,
            time_limit: game.time_limit ?? null,
        } as WorkerSnapshotRecord;
        state.gameDataCache.set(cacheKey, cached);
        while (state.gameDataCache.size > GAME_DATA_CACHE_MAX) {
            const oldestKey = state.gameDataCache.keys().next().value as string | undefined;
            if (!oldestKey) break;
            state.gameDataCache.delete(oldestKey);
        }
        const variantToken = (() => {
            if (typeof game.variant_id === 'string' && game.variant_id) {
                return game.variant_id;
            }
            if (cacheKey.startsWith('v') && cacheKey.includes('-')) {
                return cacheKey.split('-', 1)[0];
            }
            return undefined;
        })();
        setGameMetadataEntry(cacheKey, {
            variantId: variantToken,
            blackPlayer: cached.black_name ?? null,
            whitePlayer: cached.white_name ?? null,
            phase: typeof game.phase === 'string' ? game.phase : null,
        });
        return cached;
    }

    return {
        loadSavedGamesList,
        populateSourceDropdown,
        getGameData,
        savedGameOptionLabel,
        fullGameOptionLabel,
    };
}
