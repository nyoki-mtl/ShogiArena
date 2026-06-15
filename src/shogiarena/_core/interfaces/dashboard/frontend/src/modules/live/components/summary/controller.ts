import { crash } from '@/modules/shared/utils/errors';
import { requestJson } from '@/modules/shared/services/api';
import { normalizeGamesListResponse } from './normalizers';
import type { DashboardCore } from '@/types/dashboard';
import type { DashboardTabsApi } from '@/types/globals';
import type { LiveCardId, LiveSummaryApi, SseSummaryPayload } from '@/types/live';
import {
    ensureLiveNamespace,
    registerLiveApi,
    recordLiveDiagnosticsMetric,
    requireLiveApi,
} from '@/modules/live/utils/liveNamespace';
import { getResumeCoordinator } from '@/modules/shared/services/resume-coordinator';
import type { RatingDeltaInfo, TournamentEngineMeta, TournamentSummary } from '@/modules/tournament/types';
import type { JsonObject } from '@/types/shared';
import { createSummaryDataAccess, type SummaryWindowBase } from './context';
import { createPentaController } from './penta';
import { gameResultWinner } from '@/modules/shared/utils/game-result';
import { summaryStore } from '@/store';
import {
    renderOptionsPane,
    renderPairwiseMatrix,
    renderSpsaUnavailable,
    renderStandingsView,
    renderSummaryStats,
} from './render';
import { normalizeSummaryEventPayload } from './contract-guard';
import { isExpandedState, type PairwiseCell, type ProgressViewModel, type StandingsRow } from './state';

type SummaryRecord = TournamentSummary;
type SummaryEventPayload = SummaryRecord | SseSummaryPayload;
type StandingsStatsLike = { wins?: number | null; losses?: number | null; draws?: number | null } | null | undefined;

type SummaryWindow = SummaryWindowBase & {
    DashboardCore?: DashboardCore;
    DashboardTabs?: DashboardTabsApi;
};

const defaultWindow = window as SummaryWindow;

function createLiveSummaryApi(owner: SummaryWindow): LiveSummaryApi {
    if (!owner.DashboardCore) {
        throw new Error('DashboardCore must be loaded before live summary module');
    }

    ensureLiveNamespace(owner);
    const cards = requireLiveApi(owner, 'cards', 'DashboardLiveCards must be loaded before live summary module');
    const timeApi = requireLiveApi(owner, 'time', 'DashboardLiveTime must be loaded before live summary module');

    if (typeof cards.populateSourceDropdown !== 'function') {
        throw new Error('DashboardLiveCards.populateSourceDropdown must be a function');
    }

    const { state, events, warnSoftFailure, getApiBase, registerFetcher, getFetcher, mutateState } =
        owner.DashboardCore as DashboardCore;
    const doc = owner.document;
    const { formatTimeControlShort } = timeApi;

    const closeAllButton = doc.getElementById('liveCloseAllCards') as HTMLButtonElement | null;
    const openAllButton = doc.getElementById('liveOpenAllWorkers') as HTMLButtonElement | null;

    // Sync liveStreamingEnabled state based on syncTempo.
    // 'off' = streaming disabled, any other value = streaming enabled.
    const syncStreamingStateFromTempo = () => {
        const syncTempo = (state as { syncTempo?: 'off' | 'auto' | 2 | 4 | 8 | 'unlimited' }).syncTempo ?? 'auto';
        const enabled = syncTempo !== 'off';
        if (mutateState) {
            mutateState('liveStreamingEnabled', enabled);
        } else {
            state.liveStreamingEnabled = enabled;
        }
        if (!enabled) {
            cards.freezeAllWorkerClocks?.();
        }
    };

    // Initial sync based on current syncTempo.
    syncStreamingStateFromTempo();

    if (closeAllButton) {
        closeAllButton.addEventListener('click', () => {
            const deleteCard = cards.deleteCard?.bind(cards);
            if (!deleteCard) {
                console.warn('Unable to close live cards: delete API unavailable.');
                return;
            }
            const collectCards = (): ReadonlyArray<{ id?: LiveCardId | string | number }> => {
                if (typeof cards.getCardList === 'function') {
                    return [...cards.getCardList()] as ReadonlyArray<{ id?: LiveCardId | string | number }>;
                }
                if (Array.isArray(state.cards)) {
                    return [...state.cards] as ReadonlyArray<{ id?: LiveCardId | string | number }>;
                }
                return [];
            };
            const cardsToClose = collectCards();
            if (!cardsToClose.length) {
                return;
            }
            cardsToClose.forEach((card) => {
                const cardId = card?.id;
                if (cardId === undefined || cardId === null) {
                    return;
                }
                try {
                    deleteCard(cardId as LiveCardId);
                } catch (error) {
                    warnSoftFailure('Failed to close live card', error);
                }
            });
        });
    }

    if (openAllButton) {
        openAllButton.addEventListener('click', () => {
            const openAll = cards.openAllWorkerCards?.bind(cards);
            if (!openAll) {
                console.warn('Unable to open live cards: openAllWorkerCards API unavailable.');
                return;
            }
            openAll();
        });
    }

    const { resolveSummarySnapshot, getFinalRatingsFromSummary } = createSummaryDataAccess(owner);
    let spsaFallbackRendered = false;
    const GAMES_FETCH_TTL_MS = 5000;
    let gamesFetchPromise: Promise<JsonObject[]> | null = null;
    let lastGamesFetchAt = 0;
    let previousStandingsOrder: string[] = [];
    let lastSummaryStatsSignature = '';
    let lastPairwiseSignature = '';
    const resumeCoordinator = getResumeCoordinator(owner);
    let summaryFlushScheduled = false;
    let standingsFlushScheduled = false;
    let pendingSummaryStats = false;
    let pendingStandings = false;
    let pendingSourceRefresh = false;
    let sourceRefreshScheduled = false;
    let sourceRefreshQueue: LiveCardId[] | null = null;
    const SOURCE_REFRESH_CHUNK = 4;

    const scheduleIdle = (task: () => void): void => {
        if (typeof requestIdleCallback === 'function') {
            requestIdleCallback(() => task(), { timeout: 120 });
            return;
        }
        setTimeout(task, 0);
    };

    const scheduleStandingsFlush = (): void => {
        if (standingsFlushScheduled) return;
        standingsFlushScheduled = true;
        const run = () => {
            standingsFlushScheduled = false;
            if (resumeCoordinator.isCritical()) {
                resumeCoordinator.defer('live.summary.update_standings', scheduleStandingsFlush);
                return;
            }
            if (!pendingStandings) return;
            pendingStandings = false;
            updateStandings();
        };
        scheduleIdle(run);
    };

    const scheduleSummaryFlush = (): void => {
        if (summaryFlushScheduled) return;
        summaryFlushScheduled = true;
        const run = () => {
            summaryFlushScheduled = false;
            if (resumeCoordinator.isCritical()) {
                resumeCoordinator.defer('live.summary.flush', scheduleSummaryFlush);
                return;
            }
            if (pendingSummaryStats) {
                pendingSummaryStats = false;
                updateSummaryStats();
            }
            if (pendingStandings) {
                scheduleStandingsFlush();
            }
        };
        if (typeof requestAnimationFrame === 'function') {
            requestAnimationFrame(() => run());
        } else {
            setTimeout(run, 0);
        }
    };

    const scheduleSourceRefresh = (): void => {
        if (sourceRefreshScheduled) return;
        sourceRefreshScheduled = true;
        const run = () => {
            sourceRefreshScheduled = false;
            if (resumeCoordinator.isCritical()) {
                resumeCoordinator.defer('live.summary.populate_sources', scheduleSourceRefresh);
                return;
            }
            if (!pendingSourceRefresh) return;
            if (!sourceRefreshQueue) {
                sourceRefreshQueue = Array.isArray(state.cards)
                    ? state.cards
                          .map((card) => (card && typeof card === 'object' ? (card as { id?: LiveCardId }).id : null))
                          .filter((id): id is LiveCardId => id != null)
                    : [];
            }
            const queue = sourceRefreshQueue;
            if (!queue.length) {
                pendingSourceRefresh = false;
                sourceRefreshQueue = null;
                return;
            }
            const batch = queue.splice(0, SOURCE_REFRESH_CHUNK);
            for (const cardId of batch) {
                try {
                    void cards.populateSourceDropdown(cardId);
                } catch (error) {
                    warnSoftFailure(`Failed to refresh sources for card ${cardId}`, error);
                }
            }
            if (queue.length > 0) {
                scheduleIdle(run);
            } else {
                pendingSourceRefresh = false;
                sourceRefreshQueue = null;
            }
        };
        scheduleIdle(run);
    };

    const fetchGamesList = async (force = false): Promise<ReadonlyArray<JsonObject>> => {
        const startMs =
            typeof performance !== 'undefined' && typeof performance.now === 'function'
                ? performance.now()
                : Date.now();
        const recordResult = (games: JsonObject[], fromCache: boolean): ReadonlyArray<JsonObject> => {
            const endMs =
                typeof performance !== 'undefined' && typeof performance.now === 'function'
                    ? performance.now()
                    : Date.now();
            recordLiveDiagnosticsMetric('live.summary.fetch_games_list', {
                triggered: 1,
                total_ms: Math.max(0, endMs - startMs),
                games: games.length,
                from_cache: fromCache ? 1 : 0,
                force: force ? 1 : 0,
            });
            return games;
        };
        if (!force && Array.isArray(state.gamesList) && state.gamesList.length) {
            return recordResult(state.gamesList as unknown as JsonObject[], true);
        }

        if (!force && gamesFetchPromise) {
            const games = await gamesFetchPromise;
            return recordResult(games, true);
        }

        const now = Date.now();
        if (
            !force &&
            now - lastGamesFetchAt < GAMES_FETCH_TTL_MS &&
            Array.isArray(state.gamesListCache) &&
            state.gamesListCache.length
        ) {
            state.gamesList = state.gamesListCache as unknown as typeof state.gamesList;
            return recordResult(state.gamesList as unknown as JsonObject[], true);
        }

        const cachedFetcher = getFetcher?.('gamesList');
        if (!force && typeof cachedFetcher === 'function') {
            try {
                const games = await cachedFetcher();
                if (Array.isArray(games) && games.length) {
                    state.gamesList = games as unknown as typeof state.gamesList;
                    state.gamesListCache = games as unknown as typeof state.gamesListCache;
                    lastGamesFetchAt = Date.now();
                    return recordResult(games as JsonObject[], true);
                }
            } catch (error) {
                warnSoftFailure('Cached gamesList fetcher failed', error);
            }
        }

        const API_BASE = getApiBase();
        const promise = requestJson<{ games?: unknown }>(`${API_BASE}/api/games?limit=5000`)
            .then((data) => {
                const games = normalizeGamesListResponse(data);
                state.gamesList = games as unknown as typeof state.gamesList;
                state.gamesListCache = games as unknown as typeof state.gamesListCache;
                lastGamesFetchAt = Date.now();
                return games as unknown as JsonObject[];
            })
            .catch((error) => {
                throw crash('Failed to fetch games list', error);
            })
            .finally(() => {
                gamesFetchPromise = null;
            });
        gamesFetchPromise = promise;
        const games = await promise;
        return recordResult(games, false);
    };

    const { buildPentaPane, setupPentaUI, computePairPentanomial } = createPentaController({
        doc,
        state,
        fetchGamesList,
        resolveSummarySnapshot,
    });

    const renderOptions = (metaMap: Map<string, TournamentEngineMeta>, name: string): HTMLElement =>
        renderOptionsPane(doc, metaMap, name);

    const updateSummaryStats = (): void => {
        const startMs =
            typeof performance !== 'undefined' && typeof performance.now === 'function'
                ? performance.now()
                : Date.now();
        const { normalized } = resolveSummarySnapshot();
        const summaryType = typeof normalized.tournamentType === 'string' ? normalized.tournamentType : null;
        if (summaryType && summaryType !== 'spsa') {
            owner.ARENA_DISABLE_SPSA = true;
        }
        if (owner.ARENA_DISABLE_SPSA && !spsaFallbackRendered) {
            renderSpsaUnavailable(doc);
            spsaFallbackRendered = true;
        }
        const liveView = state.liveViewSnapshot;
        const liveProgress =
            liveView?.progress && (liveView.mode === 'tournament' || liveView.progress.kind === 'games')
                ? liveView.progress
                : null;
        const completed =
            typeof liveProgress?.completed === 'number' && Number.isFinite(liveProgress.completed)
                ? liveProgress.completed
                : Number.isFinite(normalized.completedGames)
                  ? normalized.completedGames
                  : 0;
        const total =
            typeof liveProgress?.total === 'number' && Number.isFinite(liveProgress.total)
                ? liveProgress.total
                : Number.isFinite(normalized.totalGames)
                  ? normalized.totalGames
                  : 0;
        const indicator =
            (doc.getElementById('progressText')?.dataset.progressState as string | undefined) ?? undefined;
        const cancelled =
            typeof liveProgress?.cancelled === 'number' && Number.isFinite(liveProgress.cancelled)
                ? liveProgress.cancelled
                : normalized.cancelledGames;
        const progressFinal =
            typeof liveProgress?.is_final === 'boolean'
                ? liveProgress.is_final
                : Boolean(normalized.tournamentFinished);

        const sprtRaw = normalized.sprt ?? null;
        const sprt =
            sprtRaw && typeof sprtRaw === 'object'
                ? {
                      llr:
                          typeof (sprtRaw as { llr?: unknown }).llr === 'number'
                              ? (sprtRaw as { llr: number }).llr
                              : undefined,
                      lower:
                          typeof (sprtRaw as { lower?: unknown }).lower === 'number'
                              ? (sprtRaw as { lower: number }).lower
                              : undefined,
                      upper:
                          typeof (sprtRaw as { upper?: unknown }).upper === 'number'
                              ? (sprtRaw as { upper: number }).upper
                              : undefined,
                      decision:
                          typeof (sprtRaw as { decision?: unknown }).decision === 'string'
                              ? (sprtRaw as { decision: string }).decision
                              : undefined,
                      games:
                          typeof (sprtRaw as { games?: unknown }).games === 'number'
                              ? (sprtRaw as { games: number }).games
                              : undefined,
                  }
                : null;

        const model: ProgressViewModel = {
            completed,
            total,
            indicator,
            isFinal: progressFinal,
            cancelled,
            sprt,
            hasPentaData: Boolean(normalized.engines?.length),
        };
        const sprtSig = sprt
            ? [sprt.llr ?? '', sprt.lower ?? '', sprt.upper ?? '', sprt.decision ?? '', sprt.games ?? ''].join('|')
            : '';
        const signature = [
            completed,
            total,
            indicator ?? '',
            progressFinal ? 1 : 0,
            cancelled ?? '',
            sprtSig,
            model.hasPentaData ? 1 : 0,
        ].join('|');
        if (signature !== lastSummaryStatsSignature) {
            renderSummaryStats(doc, model);
            lastSummaryStatsSignature = signature;
        }
        const endMs =
            typeof performance !== 'undefined' && typeof performance.now === 'function'
                ? performance.now()
                : Date.now();
        recordLiveDiagnosticsMetric('live.summary.update_stats', {
            triggered: 1,
            total_ms: Math.max(0, endMs - startMs),
            engines: normalized.engines.length,
        });
    };

    const formatWDL = (stats: StandingsStatsLike): string => {
        if (!stats) return '0-0-0';
        const w = Number(stats.wins || 0);
        const d = Number(stats.draws || 0);
        const l = Number(stats.losses || 0);
        return `${w}-${d}-${l}`;
    };

    let lastStandingsSignature = '';
    const updateStandings = (): void => {
        const startMs =
            typeof performance !== 'undefined' && typeof performance.now === 'function'
                ? performance.now()
                : Date.now();
        const { normalized, raw: summary } = resolveSummarySnapshot();
        const afterResolveMs =
            typeof performance !== 'undefined' && typeof performance.now === 'function'
                ? performance.now()
                : Date.now();
        const ratingInitial = normalized.ratingInitial;
        const ratings = getFinalRatingsFromSummary();
        const btd = normalized.btd;
        const usingBTD = Boolean(btd && Object.keys(btd.ratings).length);
        const anchorName = btd?.anchor ?? null;
        const ciMap = new Map<string, number>();
        if (btd) {
            for (const [name, entry] of Object.entries(btd.ratings)) {
                const se = entry?.standardError ?? null;
                if (typeof se === 'number' && Number.isFinite(se)) {
                    ciMap.set(name, 1.96 * se);
                }
            }
        }

        const now = Date.now();
        const carry = new Map<string, RatingDeltaInfo>();
        if (state.ratingDeltas instanceof Map) {
            for (const [key, info] of state.ratingDeltas.entries()) {
                if (info && typeof info.until === 'number' && info.until > now) carry.set(key, info);
            }
        }
        const prevRatings = state.prevRatings instanceof Map ? state.prevRatings : new Map();
        for (const [name, cur] of ratings.entries()) {
            if (!prevRatings.has(name)) continue;
            const before = Number(prevRatings.get(name) || ratingInitial);
            const after = Number(cur || ratingInitial);
            const diff = Math.round(after - before);
            if (diff !== 0) carry.set(String(name), { delta: diff, until: now + 3000 });
        }
        if (state.pendingDeltaHint instanceof Map) {
            const nextHints = new Map();
            for (const [name, until] of state.pendingDeltaHint.entries()) {
                if (typeof until !== 'number' || until <= now) continue;
                nextHints.set(name, until);
                if (!carry.has(name)) carry.set(name, { delta: 0, until: Math.min(until, now + 3000) });
            }
            state.pendingDeltaHint = nextHints;
        }
        state.ratingDeltas = carry;
        state.prevRatings = new Map(ratings);
        if (state.ratingDeltaTimerId) {
            clearTimeout(state.ratingDeltaTimerId);
            state.ratingDeltaTimerId = null;
        }

        const names = new Set<string>();
        normalized.engines.forEach((n) => {
            if (n) names.add(String(n));
        });
        Object.keys(normalized.engineStats).forEach((n) => {
            names.add(n);
        });
        for (const k of ratings.keys()) names.add(String(k));
        const list = Array.from(names).map((name) => {
            const r = ratings.has(name) ? Number(ratings.get(name)) : ratingInitial;
            const stats = normalized.engineStats ? normalized.engineStats[name] : null;
            const wdl = formatWDL(stats);
            const games = stats ? Number(stats.games || 0) : 0;
            return { name, rating: Number(r || ratingInitial), wdl, games };
        });
        list.sort((a, b) => b.rating - a.rating || b.games - a.games || a.name.localeCompare(b.name));

        const defaultTC = normalized.defaultTimeControl ?? '';
        state.standingsTCMap = new Map(Object.entries(normalized.engineTimeControls));
        if (defaultTC) {
            normalized.engines.forEach((n) => {
                const key = String(n || '');
                if (key && !state.standingsTCMap.has(key)) {
                    state.standingsTCMap.set(key, defaultTC);
                }
            });
        }

        const rows: StandingsRow[] = list.map((row) => {
            const tcSpec = state.standingsTCMap instanceof Map ? state.standingsTCMap.get(row.name) || '' : '';
            const tcShort = tcSpec ? formatTimeControlShort(tcSpec) : '-';
            return {
                name: row.name,
                rating: row.rating,
                wdl: row.wdl,
                games: row.games,
                tcShort,
                moved: previousStandingsOrder.length ? previousStandingsOrder.includes(row.name) === false : false,
                isAnchor: Boolean(anchorName && row.name === anchorName),
            };
        });

        const expandedState = isExpandedState(state.expanded) ? state.expanded : null;
        const expandedSig = expandedState
            ? `${expandedState.mode}:${expandedState.name}:${expandedState.opponent ?? ''}`
            : '';
        const rowsSig = rows
            .map((row) => `${row.name}|${row.rating}|${row.wdl}|${row.games}|${row.tcShort}|${row.isAnchor ? 1 : 0}`)
            .join('||');
        const signature = `${expandedSig}::${rowsSig}`;

        const metaArr: TournamentEngineMeta[] = Array.isArray(summary.engines_meta)
            ? (summary.engines_meta as TournamentEngineMeta[])
            : [];
        const metaMap = new Map<string, TournamentEngineMeta>();
        metaArr.forEach((meta) => {
            if (!meta) return;
            const key = typeof meta.name === 'string' && meta.name ? meta.name : '';
            if (key) metaMap.set(key, meta);
        });

        const renderPenta = (name: string, initialOpponent?: string | null): HTMLElement => {
            const pane = buildPentaPane();
            void setupPentaUI(name, pane, initialOpponent ?? undefined).catch(() => {
                state.expanded = { name, mode: 'penta', opponent: initialOpponent ?? undefined };
            });
            return pane;
        };

        let afterRenderMs = afterResolveMs;
        if (signature !== lastStandingsSignature) {
            const nextOrder = renderStandingsView({
                doc,
                rows,
                ratingDeltas: state.ratingDeltas instanceof Map ? state.ratingDeltas : new Map(),
                usingBTD,
                ciMap,
                previousOrder: previousStandingsOrder,
                expanded: expandedState,
                renderOptionsPane: (name) => renderOptions(metaMap, name),
                renderPentaPane: renderPenta,
                onExpandedChange: (next) => {
                    state.expanded = next;
                },
                onHighlightChange: (key) => {
                    state.highlightEngine = key;
                },
            });
            afterRenderMs =
                typeof performance !== 'undefined' && typeof performance.now === 'function'
                    ? performance.now()
                    : Date.now();
            previousStandingsOrder = nextOrder;
            lastStandingsSignature = signature;
        }

        if (carry.size) {
            let minRemain = Infinity;
            for (const info of carry.values()) {
                const remaining = Math.max(0, Number(info.until) - now);
                if (remaining < minRemain) minRemain = remaining;
            }
            if (Number.isFinite(minRemain) && minRemain > 0) {
                state.ratingDeltaTimerId = setTimeout(
                    () => {
                        try {
                            updateStandings();
                        } catch (error) {
                            warnSoftFailure('Failed to refresh standings after rating delta expiry', error);
                        }
                    },
                    Math.min(minRemain + 10, 3500),
                );
            }
        }

        recordLiveDiagnosticsMetric('live.summary.update_standings', {
            triggered: 1,
            resolve_ms: Math.max(0, afterResolveMs - startMs),
            render_ms: Math.max(0, afterRenderMs - afterResolveMs),
            total_ms: Math.max(0, afterRenderMs - startMs),
            engines: normalized.engines.length,
            pair_results: Object.keys(normalized.pairResults ?? {}).length,
            rows: rows.length,
        });
    };

    const updatePairwiseMatrix = async (): Promise<void> => {
        if (resumeCoordinator.isCritical()) {
            resumeCoordinator.defer('live.summary.update_pairwise', () => {
                void updatePairwiseMatrix();
            });
            return;
        }
        const startMs =
            typeof performance !== 'undefined' && typeof performance.now === 'function'
                ? performance.now()
                : Date.now();
        try {
            let games = await fetchGamesList();
            if (!games.length) {
                try {
                    const API_BASE = getApiBase();
                    const data = await requestJson<{ games?: unknown }>(`${API_BASE}/api/games?limit=1000`);
                    games = normalizeGamesListResponse(data) as unknown as JsonObject[];
                    if (games.length) {
                        state.gamesList = games as unknown as typeof state.gamesList;
                        state.gamesListCache = games as unknown as typeof state.gamesListCache;
                        lastGamesFetchAt = Date.now();
                    }
                } catch (_error) {
                    // fall back to cached data if available
                }
                if (!games.length) {
                    const cached = await fetchGamesList(true);
                    games = Array.isArray(cached) ? Array.from(cached) : [];
                    if (!games.length) {
                        throw new Error('Failed to fetch /api/games for pairwise matrix');
                    }
                }
            }

            const { normalized } = resolveSummarySnapshot();
            const engines = new Set<string>();
            normalized.engines.forEach((n) => {
                if (n) engines.add(String(n));
            });
            Object.keys(normalized.engineStats).forEach((n) => {
                engines.add(String(n));
            });
            const stateWorkers = state.workers instanceof Map ? state.workers : new Map();
            for (const workerState of stateWorkers.values()) {
                if (workerState?.lastGameEngine) engines.add(String(workerState.lastGameEngine));
            }
            games.forEach((g) => {
                const a = String(g.black_player ?? '').trim();
                const b = String(g.white_player ?? '').trim();
                if (a) engines.add(a);
                if (b) engines.add(b);
            });

            const enginesArr: string[] = Array.from(engines).sort();
            if (!enginesArr.length) {
                return;
            }

            const cachedSignature =
                typeof state.gamesSignature === 'string' && state.gamesSignature
                    ? `sig:${state.gamesSignature}|eng:${enginesArr.join('|')}`
                    : '';
            if (cachedSignature && cachedSignature === lastPairwiseSignature) {
                return;
            }

            const pair = new Map<string, PairwiseCell>();
            let signatureHash = 5381;
            const hashString = (value: string): void => {
                for (let i = 0; i < value.length; i += 1) {
                    signatureHash = ((signatureHash << 5) + signatureHash + value.charCodeAt(i)) | 0;
                }
            };
            games.forEach((g) => {
                const a = String(g.black_player ?? '').trim();
                const b = String(g.white_player ?? '').trim();
                if (!a || !b) return;
                const gid = String(g.game_id ?? '');
                const gameResult = String(g.game_result ?? '');
                if (gid) hashString(gid);
                if (gameResult) hashString(gameResult);
                hashString(a);
                hashString(b);
                const key = [a, b].sort().join('||');
                if (!pair.has(key)) pair.set(key, {});
                const cell = pair.get(key);
                if (!cell) return;
                const winner = gameResultWinner(g.game_result);
                if (winner === 'black') {
                    cell[a] = (cell[a] || 0) + 1;
                    cell[b] = cell[b] || 0;
                } else if (winner === 'white') {
                    cell[b] = (cell[b] || 0) + 1;
                    cell[a] = cell[a] || 0;
                } else if (winner === 'draw') {
                    cell.d = (cell.d || 0) + 1;
                }
            });

            hashString(`engines:${enginesArr.join('|')}`);
            const signature = cachedSignature || `${signatureHash}:${enginesArr.length}:${games.length}`;
            if (signature === lastPairwiseSignature) {
                return;
            }
            lastPairwiseSignature = signature;
            renderPairwiseMatrix(doc, enginesArr, pair);
        } catch (error) {
            warnSoftFailure('Failed to render pairwise matrix', error);
        } finally {
            const endMs =
                typeof performance !== 'undefined' && typeof performance.now === 'function'
                    ? performance.now()
                    : Date.now();
            recordLiveDiagnosticsMetric('live.summary.update_pairwise', {
                triggered: 1,
                total_ms: Math.max(0, endMs - startMs),
            });
        }
    };

    const handleSummaryEvent = (
        event:
            | SummaryEventPayload
            | JsonObject
            | { summary?: SummaryEventPayload | JsonObject }
            | CustomEvent<{ summary?: SummaryEventPayload | JsonObject }>
            | null,
    ): void => {
        if (state.offlineNotified || owner.ARENA_DASHBOARD_STOPPED === true) {
            return;
        }
        let payload: SummaryEventPayload | JsonObject | null = null;
        const changedKeys = new Set<string>();
        if (event && typeof event === 'object') {
            if ('detail' in event && event.detail && typeof event.detail === 'object') {
                const detail = event.detail as { summary?: SummaryEventPayload | JsonObject };
                if (detail.summary && typeof detail.summary === 'object') {
                    payload = detail.summary as SummaryEventPayload | JsonObject;
                }
            } else if ('summary' in event && event.summary && typeof event.summary === 'object') {
                payload = event.summary as SummaryEventPayload | JsonObject;
            } else {
                payload = event as SummaryEventPayload | JsonObject;
            }
        }
        if (payload) {
            for (const key of Object.keys(payload)) {
                changedKeys.add(key);
            }
            const validated = normalizeSummaryEventPayload(payload);
            const mutable = { ...validated } as SummaryRecord;
            if (Object.hasOwn(mutable, 'rating_series')) {
                delete (mutable as { rating_series?: unknown }).rating_series;
            }
            const existing = summaryStore.getRawSummary('tournament') ?? {};
            const mergedLiveView =
                existing.live_view &&
                mutable.live_view &&
                typeof existing.live_view === 'object' &&
                typeof mutable.live_view === 'object'
                    ? { ...existing.live_view, ...mutable.live_view }
                    : (mutable.live_view ?? existing.live_view);
            // shallow-merge for partial diff
            const merged: SummaryRecord = {
                ...(existing as SummaryRecord),
                ...mutable,
                ...(mergedLiveView ? { live_view: mergedLiveView } : {}),
            };
            summaryStore.applyTournamentSummary(merged as JsonObject);
            const tournamentType = (mutable as { tournament_type?: unknown }).tournament_type;
            if (typeof tournamentType === 'string' && tournamentType !== 'spsa') {
                owner.ARENA_DISABLE_SPSA = true;
            }
        }

        const progressChanged =
            !payload || ['games', 'is_summary_ready', 'live_view', 'timestamp'].some((k) => changedKeys.has(k));
        const standingsChanged =
            !payload ||
            [
                'standings',
                'engines',
                'engines_meta',
                'engine_time_controls',
                'default_time_control',
                'pair_results',
                'rating_initial',
                'btd',
            ].some((k) => changedKeys.has(k));

        if (standingsChanged) {
            state.gamesList = null;
            state.pentaCache = new Map();
        }

        if (progressChanged) {
            pendingSummaryStats = true;
        }
        if (standingsChanged) {
            pendingStandings = true;
            pendingSourceRefresh = true;
        }
        if (pendingSummaryStats || pendingStandings) {
            scheduleSummaryFlush();
        }

        const dashboardRunning = !owner.ARENA_DASHBOARD_STOPPED;
        if (!state.offlineNotified && dashboardRunning && pendingSourceRefresh) {
            scheduleSourceRefresh();
        }
    };

    if (events && typeof events.on === 'function') {
        events.on('dashboard:offline', () => {
            // When dashboard goes offline, sync streaming state from tempo.
            // If tempo is 'off', streaming is already disabled.
            syncStreamingStateFromTempo();
        });
        events.on('summary:update', handleSummaryEvent);
    }

    const initialSummary = summaryStore.getRawSummary('tournament');
    if (initialSummary) {
        handleSummaryEvent({ summary: initialSummary });
    }

    if (typeof registerFetcher === 'function') {
        registerFetcher('gamesList', fetchGamesList);
    }

    const summaryApi: LiveSummaryApi = {
        updateSummaryStats,
        updateStandings,
        updatePairwiseMatrix,
        computePairPentanomial,
        handleSummaryEvent,
    };

    return summaryApi;
}

export function installLiveSummaryModule(owner: SummaryWindow = defaultWindow): LiveSummaryApi {
    if (!owner.DashboardCore) {
        throw new Error('DashboardCore must be installed before live summary module');
    }

    const namespace = ensureLiveNamespace(owner);
    const existing = namespace.get('summary');
    if (existing) {
        return existing;
    }

    const api = createLiveSummaryApi(owner);
    registerLiveApi(owner, 'summary', api, { provider: 'live/components/summary' });
    return api;
}
