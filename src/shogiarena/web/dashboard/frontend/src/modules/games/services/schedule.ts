import { crash } from '@/modules/shared/utils/errors';
import { computeSfenBasePlies } from '@/modules/shared/utils/sfen';
import type {
    AssignmentMode,
    DashboardGamesApi,
    GamesScheduleEntry,
    GamesScheduleSnapshot,
    GamesSortKey,
    LiveViewOptions,
    NormalizedGameRow,
} from '@/modules/games/types';
import type { DashboardNavigationApi } from '@/types/globals';
import type { JsonObject, RequestError } from '@/types/shared';
import type { GamesModuleState } from '../state';
import { byId } from '../utils/dom';
import type { GamesServiceContext, GamesWindow } from './context';
import { recordLiveDiagnosticsMetric } from '@/modules/live/utils/liveNamespace/metrics';

export type GamesDeltaPayload = {
    type?: string;
    rows?: Array<{ op?: string; row?: GamesScheduleEntry; id?: string }>;
    snapshotMeta?: Record<string, unknown>;
};

const VIRTUAL_THRESHOLD = 200;
const VIRTUAL_OVERSCAN = 24;
const DEFAULT_ROW_HEIGHT = 32;

export interface ScheduleController {
    readonly state: GamesModuleState;
    fetchGames: (force?: boolean) => Promise<void>;
    startRealtime: () => void;
    stopRealtime: () => void;
    applyGamesDelta: (delta: GamesDeltaPayload) => Promise<void>;
    applyInstanceFilter: (instanceId: string | null, options?: { activateTab?: boolean }) => void;
    clearInstanceFilter: (options?: { activateTab?: boolean }) => void;
    setSort: (key: GamesSortKey) => void;
    updateSortHeaders: () => void;
    updateFilterIndicator: (options?: { visible?: number; total?: number }) => void;
    renderGamesTable: (snapshot?: GamesScheduleSnapshot | null) => void;
    showGamesUnavailable: (message: string) => void;
    openGameInLiveView: (gameId: string, options?: LiveViewOptions | string) => void;
    navigateToEngineMatchups: (engineName: string) => void;
    getApi: () => DashboardGamesApi | null;
    setApi: (api: DashboardGamesApi) => void;
    setSearchQuery: (query: string) => void;
}

export interface ScheduleControllerDependencies {
    readonly context: GamesServiceContext;
}

export function createScheduleController({ context }: ScheduleControllerDependencies): ScheduleController {
    const { owner, utils, render, state } = context;
    const coreEvents = owner.DashboardCore?.events;
    let dashboardApi: DashboardGamesApi | null = null;
    let pendingScrollReset = false;
    type FilterPredicate = (row: NormalizedGameRow) => boolean;
    const virtual = {
        container: null as HTMLElement | null,
        tbody: null as HTMLTableSectionElement | null,
        topSpacer: null as HTMLTableRowElement | null,
        bottomSpacer: null as HTMLTableRowElement | null,
        rowHeight: 0,
        raf: 0 as number | null,
        rows: [] as NormalizedGameRow[],
        lastRange: { start: 0, end: 0 },
        colCount: 9,
        listenerAttached: false,
        enabled: false,
    };
    const domCache = {
        rowByKey: new Map<string, HTMLTableRowElement>(),
        signatureByKey: new Map<string, string>(),
    };

    let gamesDeltaUnsub: (() => void) | null = null;
    let snapshotFallbackTimer: number | null = null;
    const SNAPSHOT_FALLBACK_DELAY_MS = 1200;

    const getNow = () =>
        typeof performance !== 'undefined' && typeof performance.now === 'function' ? performance.now() : Date.now();

    const SEARCH_FIELD_ALIASES: Record<string, string> = {
        game: 'game',
        id: 'game',
        gid: 'game',
        black: 'black',
        white: 'white',
        engine: 'engine',
        engines: 'engine',
        status: 'status',
        result: 'result',
        outcome: 'result',
        instance: 'instance',
        inst: 'instance',
        instances: 'instance',
        assigned: 'assigned',
        assign: 'assigned',
        opening: 'opening',
        sfen: 'opening',
        moves: 'moves',
        plies: 'moves',
        ended: 'ended',
        finished: 'ended',
        round: 'round',
        order: 'order',
        black_instance: 'black_instance',
        white_instance: 'white_instance',
    };

    const SEARCH_FIELD_PREDICATES: Record<string, (row: NormalizedGameRow, needle: string) => boolean> = {
        game: (row, needle) =>
            containsText(row.game_id, needle) ||
            containsText(row.display_order, needle) ||
            containsText(row.order_index, needle) ||
            containsText(row.round_index, needle),
        order: (row, needle) =>
            containsText(row.display_order, needle) ||
            containsText(row.order_index, needle) ||
            containsText(row.round_index, needle),
        round: (row, needle) => containsText(row.round_index, needle),
        black: (row, needle) => containsText(row.black, needle),
        white: (row, needle) => containsText(row.white, needle),
        engine: (row, needle) => containsText(row.black, needle) || containsText(row.white, needle),
        status: (row, needle) => containsText(row.status, needle),
        result: (row, needle) =>
            containsText(row.result_abbr, needle) ||
            containsText(row.result_label, needle) ||
            containsText(row.result_detail, needle),
        instance: (row, needle) =>
            containsInArray(row.instances, needle) ||
            containsText(row.black_instance, needle) ||
            containsText(row.white_instance, needle) ||
            containsText(row.black_instance_kind, needle) ||
            containsText(row.white_instance_kind, needle),
        assigned: (row, needle) =>
            containsText(row.assigned_instance, needle) ||
            containsText(row.assigned_override, needle) ||
            containsText(row.assigned_override_black, needle) ||
            containsText(row.assigned_override_white, needle) ||
            containsText(row.resolved_instance_black, needle) ||
            containsText(row.resolved_instance_white, needle),
        black_instance: (row, needle) =>
            containsText(row.black_instance, needle) || containsText(row.black_instance_kind, needle),
        white_instance: (row, needle) =>
            containsText(row.white_instance, needle) || containsText(row.white_instance_kind, needle),
        opening: (row, needle) => containsText(row.initial_sfen, needle),
        moves: (row, needle) => containsText(row.total_plies, needle),
        ended: (row, needle) => containsText(row.ended_at, needle),
    };

    function cleanupRetryTimer(): void {
        if (state.retryTimer) {
            owner.clearTimeout(state.retryTimer);
            state.retryTimer = null;
        }
    }

    function ensureSpacers(): void {
        if (!virtual.tbody) return;
        if (!virtual.topSpacer) {
            virtual.topSpacer = document.createElement('tr');
            virtual.topSpacer.dataset.role = 'virtual-spacer';
            const td = document.createElement('td');
            td.colSpan = virtual.colCount;
            td.style.height = '0px';
            td.style.border = 'none';
            virtual.topSpacer.appendChild(td);
        }
        if (!virtual.bottomSpacer) {
            virtual.bottomSpacer = document.createElement('tr');
            virtual.bottomSpacer.dataset.role = 'virtual-spacer';
            const td = document.createElement('td');
            td.colSpan = virtual.colCount;
            td.style.height = '0px';
            td.style.border = 'none';
            virtual.bottomSpacer.appendChild(td);
        }
    }

    function ensureVirtualScrollListener(): void {
        if (!virtual.container) return;
        if (virtual.listenerAttached) return;
        virtual.container.addEventListener('scroll', handleVirtualScroll, { passive: true });
        virtual.listenerAttached = true;
    }

    function detachVirtualScrollListener(): void {
        if (virtual.container && virtual.listenerAttached) {
            virtual.container.removeEventListener('scroll', handleVirtualScroll);
        }
        virtual.listenerAttached = false;
    }

    function resetVirtualState(options?: { preserveScroll?: boolean; preserveMeasurements?: boolean }): void {
        if (!options?.preserveMeasurements) {
            virtual.rowHeight = 0;
        }
        virtual.lastRange = { start: 0, end: 0 };
        virtual.rows = [];
        if (virtual.container && !options?.preserveScroll) {
            virtual.container.scrollTop = 0;
        }
        if (virtual.topSpacer?.parentElement) {
            virtual.topSpacer.parentElement.removeChild(virtual.topSpacer);
        }
        if (virtual.bottomSpacer?.parentElement) {
            virtual.bottomSpacer.parentElement.removeChild(virtual.bottomSpacer);
        }
        virtual.topSpacer = null;
        virtual.bottomSpacer = null;
    }

    function buildRowKey(normalized: NormalizedGameRow, fallbackIndex: number): string {
        if (normalized.game_id) return normalized.game_id;
        const order = normalized.order_index ?? normalized.display_order ?? fallbackIndex;
        const round = normalized.round_index ?? 'r?';
        return `anon:${round}:${order}:${normalized.black}:${normalized.white}`;
    }

    function computeRowSignature(normalized: NormalizedGameRow): string {
        const instances = normalized.instances.join(',');
        return [
            normalized.game_id ?? '',
            normalized.status,
            normalized.round_index ?? '',
            normalized.order_index ?? '',
            normalized.display_order ?? '',
            normalized.black,
            normalized.white,
            normalized.black_instance ?? '',
            normalized.white_instance ?? '',
            normalized.black_instance_kind ?? '',
            normalized.white_instance_kind ?? '',
            normalized.initial_sfen,
            instances,
            normalized.assigned_instance ?? '',
            normalized.assigned_override ?? '',
            normalized.assigned_override_black ?? '',
            normalized.assigned_override_white ?? '',
            normalized.assigned_mode ?? '',
            normalized.resolved_instance_black ?? '',
            normalized.resolved_instance_white ?? '',
            normalized.require_install ? '1' : '0',
            normalized.result_abbr,
            normalized.result_label,
            normalized.result_detail,
            normalized.result_code ?? '',
            normalized.total_plies ?? '',
            normalized.started_at ?? '',
            normalized.ended_at ?? '',
        ].join('|');
    }

    function getRowElement(normalized: NormalizedGameRow, fallbackIndex: number): HTMLTableRowElement {
        const key = buildRowKey(normalized, fallbackIndex);
        const signature = computeRowSignature(normalized);
        const prevSignature = domCache.signatureByKey.get(key);
        const existing = domCache.rowByKey.get(key);
        if (existing && prevSignature === signature) {
            return existing;
        }
        const nextRow = render.buildRow(normalized);
        domCache.rowByKey.set(key, nextRow);
        domCache.signatureByKey.set(key, signature);
        return nextRow;
    }

    function pruneRowCache(activeKeys: Set<string>): void {
        for (const key of domCache.rowByKey.keys()) {
            if (!activeKeys.has(key)) {
                domCache.rowByKey.delete(key);
                domCache.signatureByKey.delete(key);
            }
        }
    }

    function reconcileChildren(parent: HTMLElement, desired: Node[]): void {
        if (typeof parent.replaceChildren === 'function') {
            parent.replaceChildren(...desired);
            return;
        }
        let cursor = parent.firstChild;
        for (const node of desired) {
            if (node === cursor) {
                cursor = cursor?.nextSibling ?? null;
                continue;
            }
            parent.insertBefore(node, cursor);
        }
        while (cursor) {
            const next = cursor.nextSibling;
            parent.removeChild(cursor);
            cursor = next;
        }
    }

    function measureRowHeight(sampleRow: NormalizedGameRow): number {
        if (!virtual.tbody) return DEFAULT_ROW_HEIGHT;
        const temp = render.buildRow(sampleRow);
        temp.style.position = 'absolute';
        temp.style.visibility = 'hidden';
        virtual.tbody.appendChild(temp);
        const rect = temp.getBoundingClientRect();
        virtual.tbody.removeChild(temp);
        const measured = rect.height || DEFAULT_ROW_HEIGHT;
        return Math.max(16, Math.round(measured));
    }

    function renderVirtualizedRows(): void {
        const renderStart = getNow();
        if (!virtual.container || !virtual.tbody || virtual.rows.length === 0) {
            virtual.lastRange = { start: 0, end: 0 };
            if (virtual.tbody) {
                reconcileChildren(virtual.tbody, []);
            }
            recordLiveDiagnosticsMetric('live.games.virtual.render', {
                triggered: 1,
                rows_rendered: 0,
                rows_total: virtual.rows.length,
                latency_ms: Math.max(0, getNow() - renderStart),
            });
            return;
        }

        ensureSpacers();
        if (!virtual.topSpacer || !virtual.bottomSpacer) {
            return;
        }
        if (virtual.rowHeight <= 0) {
            virtual.rowHeight = measureRowHeight(virtual.rows[0] ?? ({} as NormalizedGameRow));
        }

        const containerHeight = virtual.container.clientHeight || window.innerHeight * 0.6;
        const visibleCount = Math.max(1, Math.ceil(containerHeight / virtual.rowHeight) + VIRTUAL_OVERSCAN);
        const scrollTop = virtual.container.scrollTop || 0;
        const startIdx = Math.max(0, Math.floor(scrollTop / virtual.rowHeight) - VIRTUAL_OVERSCAN);
        const end = Math.min(virtual.rows.length, startIdx + visibleCount);

        if (virtual.lastRange.start === startIdx && virtual.lastRange.end === end) {
            return;
        }
        virtual.lastRange = { start: startIdx, end };

        const beforeHeight = startIdx * virtual.rowHeight;
        const afterHeight = Math.max(0, (virtual.rows.length - end) * virtual.rowHeight);

        const desiredNodes: Node[] = [];
        desiredNodes.push(virtual.topSpacer);
        virtual.topSpacer.firstElementChild?.setAttribute('style', `height:${beforeHeight}px;border:none;padding:0;`);

        for (let i = startIdx; i < end; i++) {
            const row = virtual.rows[i];
            if (!row) continue;
            desiredNodes.push(getRowElement(row, i));
        }

        desiredNodes.push(virtual.bottomSpacer);
        virtual.bottomSpacer.firstElementChild?.setAttribute('style', `height:${afterHeight}px;border:none;padding:0;`);

        reconcileChildren(virtual.tbody, desiredNodes);
        recordLiveDiagnosticsMetric('live.games.virtual.render', {
            triggered: 1,
            rows_rendered: end - startIdx,
            rows_total: virtual.rows.length,
            before_px: beforeHeight,
            after_px: afterHeight,
            row_height: virtual.rowHeight,
            latency_ms: Math.max(0, getNow() - renderStart),
        });
    }

    function handleVirtualScroll(): void {
        if (!virtual.rows.length) return;
        if (virtual.raf) {
            return;
        }
        virtual.raf = virtual.container?.ownerDocument?.defaultView?.requestAnimationFrame(() => {
            virtual.raf = null;
            renderVirtualizedRows();
        }) as number | null;
    }

    function handleGamesEventPayload(raw: unknown): void {
        if (!state.active) return;
        if (!raw || typeof raw !== 'object') return;
        const payload = raw as Record<string, unknown>;
        const typeRaw = payload.type;
        const type = typeof typeRaw === 'string' ? typeRaw : 'bulk';
        if (type !== 'bulk') {
            throw crash('Games SSE payload type mismatch', new Error(`Unexpected type: ${String(type)}`));
        }

        const rowsRaw = payload.rows;
        const rows = Array.isArray(rowsRaw) ? (rowsRaw as GamesScheduleEntry[]) : [];
        const metaRaw = payload.snapshotMeta;
        const meta = metaRaw && typeof metaRaw === 'object' ? (metaRaw as Partial<GamesScheduleSnapshot>) : undefined;

        const snapshot: GamesScheduleSnapshot = {
            ...(meta ?? {}),
            schedule: rows,
        };

        state.gamesUnavailable = false;
        state.consecutiveErrors = 0;
        state.lastErrorMessage = null;
        clearSnapshotFallback();

        renderGamesTable(snapshot);
    }

    async function applyGamesDelta(delta: GamesDeltaPayload): Promise<void> {
        if (!state.active) return;
        const start = getNow();
        if (!state.lastSnapshot) {
            scheduleSnapshotFallback('delta');
            recordLiveDiagnosticsMetric('live.games.delta.apply', {
                triggered: 1,
                rows: Array.isArray(delta.rows) ? delta.rows.length : 0,
                fetched: 0,
                latency_ms: Math.max(0, getNow() - start),
            });
            return;
        }

        const rowsUpdate = Array.isArray(delta.rows) ? delta.rows : [];
        if (!rowsUpdate.length) {
            recordLiveDiagnosticsMetric('live.games.delta.apply', {
                triggered: 1,
                rows: 0,
                latency_ms: Math.max(0, getNow() - start),
            });
            return;
        }

        if (!(state.rowsByGame instanceof Map) || state.rowsByGame.size === 0) {
            // seed map from last snapshot if not ready
            const seedRows = Array.isArray(state.lastSnapshot.schedule) ? state.lastSnapshot.schedule : [];
            state.rowsByGame.clear();
            seedRows.forEach((row) => {
                state.rowsByGame.set(row.game_id, row);
            });
        }

        for (const entry of rowsUpdate) {
            if (!entry || typeof entry !== 'object') continue;
            const op = (entry as { op?: string }).op;
            if (op === 'remove') {
                const id = (entry as { id?: string }).id;
                if (id) state.rowsByGame.delete(id);
                continue;
            }
            if (op === 'add' || op === 'update') {
                const rowRaw = (entry as { row?: GamesScheduleEntry }).row;
                if (!rowRaw) continue;
                const normalized = normalizeScheduleRow(rowRaw);
                if (!normalized || !normalized.game_id) continue;
                state.rowsByGame.set(normalized.game_id, normalized);
            }
        }

        const nextRows = Array.from(state.rowsByGame.values());
        state.lastSnapshot = {
            ...(state.lastSnapshot ?? {}),
            ...(delta.snapshotMeta ?? {}),
            schedule: nextRows,
        };
        if (!applyDeltaToDom(rowsUpdate)) {
            renderGamesTable(state.lastSnapshot);
        } else {
            updateGamesSummary(state.lastSnapshot);
        }
        recordLiveDiagnosticsMetric('live.games.delta.apply', {
            triggered: 1,
            rows: rowsUpdate.length,
            map_size: state.rowsByGame.size,
            latency_ms: Math.max(0, getNow() - start),
        });
    }

    function getGamesTableBody(): HTMLTableSectionElement | null {
        const panel = byId<HTMLElement>('gamesTable');
        const table = panel?.querySelector<HTMLTableElement>('table.games-table');
        return table?.querySelector('tbody') ?? null;
    }

    function canApplyDeltaToDom(): boolean {
        if (state.instanceFilter || state.searchQuery) return false;
        if (state.sortKey !== 'order' || state.sortDirection !== 'asc') return false;
        if (!state.lastSnapshot) return false;
        const schedule = state.lastSnapshot.schedule;
        const totalRows = Array.isArray(schedule) ? schedule.length : 0;
        if (totalRows > VIRTUAL_THRESHOLD) return false;
        if (virtual.enabled) return false;
        return true;
    }

    function findRowByGameId(tbody: HTMLTableSectionElement, gameId: string): HTMLTableRowElement | null {
        const rows = Array.from(tbody.querySelectorAll<HTMLTableRowElement>('tr'));
        for (const row of rows) {
            if (row.dataset.gameId === gameId) return row;
        }
        return null;
    }

    function parseOrderIndex(value: string | undefined): number | null {
        if (!value) return null;
        const parsed = Number(value);
        return Number.isFinite(parsed) ? parsed : null;
    }

    function insertRowByOrder(tbody: HTMLTableSectionElement, row: HTMLTableRowElement): void {
        const order = parseOrderIndex(row.dataset.orderIndex);
        if (order === null) {
            tbody.appendChild(row);
            return;
        }
        const rows = Array.from(tbody.querySelectorAll<HTMLTableRowElement>('tr'));
        for (const existing of rows) {
            const existingOrder = parseOrderIndex(existing.dataset.orderIndex);
            if (existingOrder === null) continue;
            if (existingOrder > order) {
                tbody.insertBefore(row, existing);
                return;
            }
        }
        tbody.appendChild(row);
    }

    function applyDeltaToDom(rowsUpdate: Array<{ op?: string; row?: GamesScheduleEntry; id?: string }>): boolean {
        if (!canApplyDeltaToDom()) return false;
        const tbody = getGamesTableBody();
        if (!tbody) return false;
        const activeKeys = new Set<string>();
        for (const row of state.rowsByGame.values()) {
            const key = buildRowKey(row, row.order_index ?? 0);
            activeKeys.add(key);
        }
        pruneRowCache(activeKeys);
        for (const entry of rowsUpdate) {
            if (!entry || typeof entry !== 'object') continue;
            const op = entry.op;
            if (op === 'remove') {
                const id = entry.id;
                if (!id) continue;
                const existing = findRowByGameId(tbody, id);
                if (existing) existing.remove();
                domCache.rowByKey.delete(id);
                domCache.signatureByKey.delete(id);
                continue;
            }
            if (op === 'add' || op === 'update') {
                const rowRaw = entry.row;
                if (!rowRaw) continue;
                const normalized = normalizeScheduleRow(rowRaw);
                if (!normalized || !normalized.game_id) continue;
                const key = buildRowKey(normalized, normalized.order_index ?? 0);
                const nextRow = getRowElement(normalized, normalized.order_index ?? 0);
                let existing = findRowByGameId(tbody, normalized.game_id);
                if (existing && existing !== nextRow) {
                    existing.replaceWith(nextRow);
                    existing = nextRow;
                }
                if (!existing) {
                    insertRowByOrder(tbody, nextRow);
                    continue;
                }
                const existingOrder = parseOrderIndex(existing.dataset.orderIndex);
                const nextOrder = normalized.order_index ?? null;
                if (nextOrder !== null && existingOrder !== nextOrder) {
                    existing.remove();
                    nextRow.dataset.orderIndex = String(nextOrder);
                    insertRowByOrder(tbody, nextRow);
                }
                domCache.rowByKey.set(key, nextRow);
                domCache.signatureByKey.set(key, computeRowSignature(normalized));
            }
        }
        return true;
    }

    function containsText(value: unknown, needle: string): boolean {
        if (!needle) return true;
        if (value === null || value === undefined) return false;
        const text = typeof value === 'string' ? value : String(value);
        return text.toLowerCase().includes(needle);
    }

    function containsInArray(values: Iterable<unknown> | null | undefined, needle: string): boolean {
        if (!values) return false;
        for (const value of values) {
            if (containsText(value, needle)) {
                return true;
            }
        }
        return false;
    }

    function tokenizeQuery(input: string): string[] {
        const tokens: string[] = [];
        let current = '';
        let inQuotes = false;

        for (let i = 0; i < input.length; i++) {
            const char = input[i] ?? '';
            if (char === '"') {
                inQuotes = !inQuotes;
                continue;
            }
            if (/\s/.test(char) && !inQuotes) {
                if (current) {
                    tokens.push(current);
                    current = '';
                }
            } else {
                current += char;
            }
        }

        if (current) {
            tokens.push(current);
        }

        return tokens;
    }

    function attachGamesDeltaListener(): void {
        if (!coreEvents?.on || gamesDeltaUnsub) return;
        gamesDeltaUnsub = coreEvents.on<GamesDeltaPayload>('live:games-delta', (delta) => {
            if (delta && typeof delta === 'object' && (delta as { type?: unknown }).type === 'bulk') {
                handleGamesEventPayload(delta);
                return;
            }
            void applyGamesDelta(delta);
        });
    }

    function detachGamesDeltaListener(): void {
        if (gamesDeltaUnsub) {
            gamesDeltaUnsub();
            gamesDeltaUnsub = null;
        }
    }

    function buildDefaultPredicate(needle: string): FilterPredicate {
        return (row: NormalizedGameRow) =>
            containsText(row.game_id, needle) ||
            containsText(row.display_order, needle) ||
            containsText(row.order_index, needle) ||
            containsText(row.round_index, needle) ||
            containsText(row.black, needle) ||
            containsText(row.white, needle) ||
            containsText(row.black_instance, needle) ||
            containsText(row.white_instance, needle) ||
            containsText(row.black_instance_kind, needle) ||
            containsText(row.white_instance_kind, needle) ||
            containsText(row.assigned_instance, needle) ||
            containsText(row.assigned_override, needle) ||
            containsText(row.status, needle) ||
            containsText(row.result_abbr, needle) ||
            containsText(row.result_label, needle) ||
            containsText(row.result_detail, needle) ||
            containsText(row.initial_sfen, needle) ||
            containsInArray(row.instances, needle);
    }

    function buildSearchPredicates(query: string): FilterPredicate[] {
        const normalized = typeof query === 'string' ? query.trim() : '';
        if (!normalized) return [];
        const tokens = tokenizeQuery(normalized);
        if (!tokens.length) return [];

        const predicates: FilterPredicate[] = [];

        tokens.forEach((rawToken) => {
            if (!rawToken) return;
            const token = rawToken;
            let field: string | null = null;
            let value = token;
            const colonIndex = token.indexOf(':');
            const eqIndex = token.indexOf('=');
            const splitIndex = colonIndex >= 0 ? colonIndex : eqIndex;
            if (splitIndex > 0) {
                field = token.slice(0, splitIndex).toLowerCase();
                value = token.slice(splitIndex + 1);
            }

            value = value.trim();
            if (!value) return;

            const needle = value.toLowerCase();
            if (!needle) return;

            let normalizedField: string | null = field ? (SEARCH_FIELD_ALIASES[field] ?? field) : null;
            if (normalizedField && !SEARCH_FIELD_PREDICATES[normalizedField]) {
                normalizedField = null;
            }

            const predicateFactory = normalizedField ? SEARCH_FIELD_PREDICATES[normalizedField] : null;
            const predicate = predicateFactory
                ? (row: NormalizedGameRow) => predicateFactory(row, needle)
                : buildDefaultPredicate(needle);
            predicates.push(predicate);
        });

        return predicates;
    }

    function getApiBase(): string {
        return utils.apiBase();
    }

    function showNotice(
        message: string,
        variant: Parameters<typeof utils.showNotice>[1] = 'info',
        options?: Parameters<typeof utils.showNotice>[2],
    ) {
        utils.showNotice(message, variant, options);
    }

    function requestJson<T = unknown>(url: string, options?: Parameters<typeof utils.requestJson>[1]) {
        return utils.requestJson<T>(url, options);
    }

    function requireNavigation(targetOwner: GamesWindow): DashboardNavigationApi {
        const navigation = targetOwner.DashboardNavigation;
        if (!navigation) {
            throw new Error('DashboardNavigation is unavailable');
        }
        if (typeof navigation.openGame !== 'function') {
            throw new Error('DashboardNavigation.openGame is unavailable');
        }
        return navigation;
    }

    function openGameInLiveView(gameId: string, options: LiveViewOptions | string = {}): void {
        if (!gameId) return;
        const baseOptions: LiveViewOptions = typeof options === 'string' ? { source: options } : (options ?? {});
        const resolvedOptions: LiveViewOptions = baseOptions.source ? baseOptions : { ...baseOptions, source: 'games' };
        const payload: JsonObject = { ...resolvedOptions };
        const navigationApi = requireNavigation(owner);
        void navigationApi.openGame(gameId, payload);
    }

    function updateFilterIndicator(options?: { visible?: number; total?: number }): void {
        const indicator = byId<HTMLElement>('gamesFilterIndicator');
        if (!indicator) return;
        const parts: string[] = [];
        if (state.instanceFilter) {
            parts.push(`Instance: ${state.instanceFilter}`);
        }
        if (state.searchQuery) {
            parts.push(`Query: ${state.searchQuery}`);
        }
        const hasFilters = parts.length > 0;
        if (hasFilters && typeof options?.visible === 'number' && typeof options?.total === 'number') {
            parts.push(`${options.visible} / ${options.total}`);
        }

        indicator.textContent = parts.join(' · ');
        indicator.hidden = parts.length === 0;
    }

    function sanitizeInstanceLabel(value: unknown): string | null {
        if (typeof value !== 'string') return null;
        const trimmed = value.trim();
        return trimmed ? trimmed : null;
    }

    function formatEngineName(value: unknown): string {
        if (typeof value !== 'string') {
            // Some historical schedules omit engine labels when a bye is generated; surface a placeholder
            // so the row remains accessible instead of crashing the view.
            return '-';
        }
        const output = value.replace(/\r?\n+/g, ' ').trim();
        return output || '-';
    }

    function normalizeScheduleRow(row: unknown): NormalizedGameRow | null {
        if (!row || typeof row !== 'object') return null;
        const raw = row as GamesScheduleEntry;

        const orderIndex = typeof raw.order === 'number' ? raw.order : null;
        const roundIndex = typeof raw.round === 'number' ? raw.round : null;
        const displayOrder = orderIndex !== null ? orderIndex + 1 : null;

        const gameId = typeof raw.game_id === 'string' && raw.game_id ? raw.game_id : null;
        const statusRaw = typeof raw.status === 'string' ? raw.status.trim() : '';
        if (!statusRaw) {
            throw new Error('Games schedule row is missing a status value');
        }
        const status = statusRaw;
        const black = formatEngineName(raw.black);
        const white = formatEngineName(raw.white);

        const blackInstance = typeof raw.black_instance === 'string' && raw.black_instance ? raw.black_instance : null;
        const whiteInstance = typeof raw.white_instance === 'string' && raw.white_instance ? raw.white_instance : null;

        const blackKind =
            typeof raw.black_instance_kind === 'string' && raw.black_instance_kind ? raw.black_instance_kind : null;
        const whiteKind =
            typeof raw.white_instance_kind === 'string' && raw.white_instance_kind ? raw.white_instance_kind : null;

        const initialSfen = typeof raw.initial_sfen === 'string' && raw.initial_sfen ? raw.initial_sfen : 'startpos';
        const basePlies = computeSfenBasePlies(initialSfen);

        const uniqueInstances = Array.from(
            new Set(
                [blackInstance, whiteInstance].filter(
                    (value): value is string => typeof value === 'string' && value.length > 0,
                ),
            ),
        );

        const assignedInstance =
            typeof raw.assigned_instance === 'string' && raw.assigned_instance ? raw.assigned_instance : null;
        const assignedOverride =
            typeof raw.assigned_override === 'string' && raw.assigned_override ? raw.assigned_override : null;
        const assignedOverrideBlack =
            typeof raw.assigned_override_black === 'string' && raw.assigned_override_black
                ? (raw.assigned_override_black as string)
                : null;
        const assignedOverrideWhite =
            typeof raw.assigned_override_white === 'string' && raw.assigned_override_white
                ? (raw.assigned_override_white as string)
                : null;
        const assignedModeRaw = typeof raw.assigned_mode === 'string' ? raw.assigned_mode.trim().toLowerCase() : '';
        const assignedMode: AssignmentMode =
            assignedModeRaw === 'shared' || assignedModeRaw === 'per_color' ? assignedModeRaw : 'auto';
        const resolvedInstanceBlack =
            typeof raw.resolved_instance_black === 'string' && raw.resolved_instance_black
                ? (raw.resolved_instance_black as string)
                : null;
        const resolvedInstanceWhite =
            typeof raw.resolved_instance_white === 'string' && raw.resolved_instance_white
                ? (raw.resolved_instance_white as string)
                : null;
        const requireInstall = Boolean(raw.require_install);

        const resolveTimestamp = (value: unknown): string | null => {
            if (typeof value === 'string' && value) return value;
            if (typeof value === 'number' && Number.isFinite(value)) {
                const ms = value > 1e12 ? value : value * 1000;
                return new Date(ms).toISOString();
            }
            if (value instanceof Date && !Number.isNaN(value.getTime())) {
                return value.toISOString();
            }
            return null;
        };

        const startTime =
            resolveTimestamp((raw as { start_time?: unknown }).start_time) ??
            resolveTimestamp((raw as { start_date?: unknown }).start_date) ??
            resolveTimestamp((raw as { started_at?: unknown }).started_at);

        const endTime =
            resolveTimestamp(raw.end_time) ??
            resolveTimestamp((raw as { end_date?: unknown }).end_date) ??
            resolveTimestamp((raw as { ended_at?: unknown }).ended_at);

        const totalPliesRaw =
            typeof raw.total_plies === 'number' && Number.isFinite(raw.total_plies) ? raw.total_plies : null;
        const totalPliesAdjusted = totalPliesRaw !== null ? totalPliesRaw + basePlies : null;

        const normalized: NormalizedGameRow = {
            game_id: gameId,
            status,
            round_index: roundIndex,
            order_index: orderIndex,
            display_order: displayOrder,
            black,
            white,
            black_instance: sanitizeInstanceLabel(blackInstance),
            white_instance: sanitizeInstanceLabel(whiteInstance),
            black_instance_kind: sanitizeInstanceLabel(blackKind),
            white_instance_kind: sanitizeInstanceLabel(whiteKind),
            initial_sfen: initialSfen,
            instances: uniqueInstances,
            assigned_instance: assignedInstance,
            assigned_override: assignedOverride,
            assigned_override_black: assignedOverrideBlack,
            assigned_override_white: assignedOverrideWhite,
            assigned_mode: assignedMode,
            resolved_instance_black: sanitizeInstanceLabel(resolvedInstanceBlack),
            resolved_instance_white: sanitizeInstanceLabel(resolvedInstanceWhite),
            require_install: requireInstall,
            result_abbr: typeof raw.result_abbr === 'string' ? raw.result_abbr : '',
            result_label: typeof raw.result_label === 'string' ? raw.result_label : '',
            result_detail: typeof raw.result_detail === 'string' ? raw.result_detail : '',
            result_code:
                typeof raw.result_code === 'number' && Number.isFinite(raw.result_code) ? raw.result_code : null,
            total_plies: totalPliesAdjusted,
            started_at: startTime,
            ended_at: endTime,
        };

        return normalized;
    }

    function updateGamesSummary(snapshot: GamesScheduleSnapshot): void {
        const summaryEl = byId<HTMLElement>('gamesSummary');
        if (!summaryEl) return;

        const total = snapshot?.total_games ?? 0;
        const completed = snapshot?.completed_games ?? 0;
        const running = snapshot?.running_games ?? 0;
        const pending = snapshot?.pending_games ?? 0;
        const cancelled = snapshot?.cancelled_games ?? 0;
        const seed = snapshot?.seed;

        const summaryParts = [] as string[];
        if (Number.isFinite(total) && total > 0) {
            summaryParts.push(`Games ${completed} / ${total}`);
        }
        if (Number.isFinite(running) && running > 0) {
            summaryParts.push(`${running} running`);
        }
        if (Number.isFinite(pending) && pending > 0) {
            summaryParts.push(`${pending} pending`);
        }
        if (Number.isFinite(cancelled) && cancelled > 0) {
            summaryParts.push(`${cancelled} cancelled`);
        }
        const seedLabel = typeof seed === 'string' ? seed.trim() : seed;
        if (seedLabel && seedLabel !== '-') {
            summaryParts.push(`Seed ${seedLabel}`);
        }

        summaryEl.textContent = summaryParts.join(' · ');
        summaryEl.hidden = summaryParts.length === 0;
    }

    function updateInstanceFilterOptions(instances: Iterable<string | null | undefined>): void {
        const select = byId<HTMLSelectElement>('gamesInstanceFilter');
        const clearBtn = byId<HTMLButtonElement>('gamesClearInstanceFilter');
        const values = Array.from(instances ?? [])
            .filter((value) => typeof value === 'string' && value.trim())
            .map((value) => value.trim());
        const uniqueValues = Array.from(new Set(values)).sort((a, b) => a.localeCompare(b));
        const missingSelected = state.instanceFilter && !uniqueValues.includes(state.instanceFilter);
        const changed =
            uniqueValues.length !== state.instanceOptions.length ||
            uniqueValues.some((value, index) => value !== state.instanceOptions[index]);

        if (missingSelected) {
            state.instanceFilter = null;
        }

        if (select) {
            if (changed || missingSelected) {
                select.innerHTML = '';
                const allOption = document.createElement('option');
                allOption.value = '';
                allOption.textContent = 'All instances';
                select.appendChild(allOption);
                uniqueValues.forEach((value) => {
                    const option = document.createElement('option');
                    option.value = value;
                    option.textContent = value;
                    select.appendChild(option);
                });
            }
            select.value = state.instanceFilter || '';
        }

        if (clearBtn) {
            clearBtn.hidden = !state.instanceFilter;
        }

        state.instanceOptions = uniqueValues;
        updateFilterIndicator();
    }

    function sortGamesRows(rows: NormalizedGameRow[]): void {
        render.sortRows(rows, state.sortKey, state.sortDirection);
    }

    function updateSortHeaders(): void {
        render.updateSortHeaders(state.sortKey, state.sortDirection);
    }

    function showGamesUnavailable(message: string): void {
        owner.DashboardCore?.showNotice?.(message, 'warn');
    }

    function renderGamesTable(snapshot?: GamesScheduleSnapshot | null): void {
        const start = getNow();
        let effectiveSnapshot = snapshot ?? state.lastSnapshot;
        if (snapshot) {
            state.lastSnapshot = snapshot;
            effectiveSnapshot = snapshot;
        }
        if (!effectiveSnapshot) return;

        const panel = byId<HTMLElement>('gamesTable');
        if (!panel) {
            throw new Error('gamesTable element is missing from the dashboard template');
        }
        panel.dataset.owner = 'games';
        const table = panel.querySelector<HTMLTableElement>('table.games-table');
        if (!table) {
            throw new Error('gamesTable element is missing a games table');
        }

        updateGamesSummary(effectiveSnapshot);

        const tbody = table.querySelector('tbody');
        if (!tbody) {
            throw new Error('gamesTable is missing a tbody element');
        }
        virtual.tbody = tbody;
        virtual.colCount =
            table.querySelectorAll('thead tr:first-child th, thead tr:first-child td').length || virtual.colCount;
        virtual.container = panel.querySelector<HTMLElement>('#gamesTableScroll') ?? panel;
        if (virtual.container) {
            if (!virtual.container.style.maxHeight) {
                virtual.container.style.maxHeight = '70vh';
            }
            virtual.container.style.overflowY = virtual.container.style.overflowY || 'auto';
            if (!virtual.container.style.getPropertyValue('scrollbar-gutter')) {
                virtual.container.style.setProperty('scrollbar-gutter', 'stable');
            }
        }

        const rows = Array.isArray(effectiveSnapshot.schedule) ? effectiveSnapshot.schedule : [];
        const normalizedRows: NormalizedGameRow[] = [];
        const instanceSet = new Set<string>();

        state.rowsByGame.clear();

        rows.forEach((row) => {
            const normalized = normalizeScheduleRow(row);
            if (!normalized) return;
            normalizedRows.push(normalized);
            if (normalized.game_id) {
                state.rowsByGame.set(normalized.game_id, normalized);
            }
            normalized.instances.forEach((inst) => {
                if (inst) instanceSet.add(inst);
            });
        });

        updateInstanceFilterOptions(instanceSet.values());

        const instanceFilteredRows = normalizedRows.filter((row) => {
            if (!state.instanceFilter) return true;
            return row.instances.includes(state.instanceFilter);
        });

        const searchPredicates = buildSearchPredicates(state.searchQuery);
        const fullyFilteredRows =
            searchPredicates.length > 0
                ? instanceFilteredRows.filter((row) => searchPredicates.every((predicate) => predicate(row)))
                : instanceFilteredRows;

        const useVirtual = fullyFilteredRows.length > VIRTUAL_THRESHOLD;
        const MAX_ROWS = useVirtual ? fullyFilteredRows.length : 500;
        const cappedRows = fullyFilteredRows.slice(0, MAX_ROWS);

        updateFilterIndicator({ visible: cappedRows.length, total: normalizedRows.length });

        sortGamesRows(cappedRows);
        updateSortHeaders();

        if (useVirtual) {
            const preserveScroll = !pendingScrollReset && virtual.enabled;
            resetVirtualState({ preserveScroll, preserveMeasurements: virtual.enabled });
            virtual.enabled = true;
            pendingScrollReset = false;
            const activeKeys = new Set<string>();
            cappedRows.forEach((normalized, index) => {
                activeKeys.add(buildRowKey(normalized, index));
            });
            pruneRowCache(activeKeys);
            virtual.rows = cappedRows;
            ensureVirtualScrollListener();
            renderVirtualizedRows();
            recordLiveDiagnosticsMetric('live.games.render', {
                triggered: 1,
                rows_total: normalizedRows.length,
                rows_rendered: cappedRows.length,
                virtual: 1,
                latency_ms: Math.max(0, getNow() - start),
            });
            return;
        }

        detachVirtualScrollListener();
        resetVirtualState({ preserveScroll: !pendingScrollReset, preserveMeasurements: true });
        virtual.enabled = false;
        pendingScrollReset = false;
        virtual.rows = [];
        const desiredNodes: Node[] = [];
        const activeKeys = new Set<string>();
        cappedRows.forEach((normalized, index) => {
            const key = buildRowKey(normalized, index);
            activeKeys.add(key);
            desiredNodes.push(getRowElement(normalized, index));
        });
        pruneRowCache(activeKeys);
        reconcileChildren(tbody, desiredNodes);
        recordLiveDiagnosticsMetric('live.games.render', {
            triggered: 1,
            rows_total: normalizedRows.length,
            rows_rendered: cappedRows.length,
            virtual: 0,
            latency_ms: Math.max(0, getNow() - start),
        });
    }

    function setSort(key: GamesSortKey): void {
        if (!key) return;
        pendingScrollReset = true;
        if (state.sortKey === key) {
            state.sortDirection = state.sortDirection === 'asc' ? 'desc' : 'asc';
        } else {
            state.sortKey = key;
            state.sortDirection = 'asc';
        }
        if (state.lastSnapshot) {
            renderGamesTable(state.lastSnapshot);
        } else {
            void fetchGames(true);
        }
    }

    function setSearchQuery(query: string): void {
        const normalized = typeof query === 'string' ? query.trim() : '';
        if (state.searchQuery === normalized) {
            updateFilterIndicator();
            return;
        }
        pendingScrollReset = true;
        state.searchQuery = normalized;
        if (state.lastSnapshot) {
            renderGamesTable(state.lastSnapshot);
        } else if (normalized) {
            void fetchGames(true);
        } else {
            updateFilterIndicator();
        }
    }

    function applyInstanceFilter(
        instanceId: string | null,
        { activateTab = false }: { activateTab?: boolean } = {},
    ): void {
        const normalized = typeof instanceId === 'string' ? instanceId.trim() : '';
        const next = normalized || null;
        if (state.instanceFilter === next && !activateTab) {
            updateFilterIndicator();
            return;
        }
        pendingScrollReset = true;
        state.instanceFilter = next;
        const navigation = owner.DashboardNavigation as DashboardNavigationApi | undefined;
        if (activateTab) {
            navigation?.openTab?.('games');
        }
        const snapshot = state.lastSnapshot;
        if (snapshot) {
            renderGamesTable(snapshot);
        } else {
            scheduleSnapshotFallback('start');
        }
    }

    function clearInstanceFilter(options?: { activateTab?: boolean }): void {
        applyInstanceFilter(null, options);
    }

    function showUnavailableMessage(message: string): void {
        if (!state.lastSnapshot) {
            showGamesUnavailable(message);
        }
    }

    function clearSnapshotFallback(): void {
        if (snapshotFallbackTimer !== null) {
            window.clearTimeout(snapshotFallbackTimer);
            snapshotFallbackTimer = null;
        }
    }

    function scheduleSnapshotFallback(reason: 'start' | 'delta'): void {
        if (snapshotFallbackTimer !== null) {
            return;
        }
        snapshotFallbackTimer = window.setTimeout(() => {
            snapshotFallbackTimer = null;
            if (!state.active || state.lastSnapshot) {
                return;
            }
            void fetchGames(true);
            recordLiveDiagnosticsMetric('live.games.snapshot.fallback', {
                triggered: 1,
                reason: reason === 'start' ? 1 : 2,
            });
        }, SNAPSHOT_FALLBACK_DELAY_MS);
    }

    function handleGamesFetchError(error: unknown): void {
        const err = error as RequestError | undefined;
        const status = typeof err?.status === 'number' ? err.status : null;
        const name = typeof err?.name === 'string' ? err.name : '';
        const recoverable = status === 503 || status === 0 || name === 'TypeError';
        if (recoverable) {
            state.gamesUnavailable = true;
            state.consecutiveErrors += 1;
            const reason = status ? `HTTP ${status}` : name || 'network error';
            const message = `Scheduler temporarily unavailable (${reason}). Retrying…`;
            if (state.lastErrorMessage !== message) {
                showNotice(message, 'warn');
                state.lastErrorMessage = message;
            }
            console.info('Schedule API temporarily unavailable, retrying', err);
            showUnavailableMessage(message);
            return;
        }
        state.lastErrorMessage = null;
        throw crash('games:schedule:fetch', err ?? new Error('Failed to load games list'));
    }

    async function fetchGames(force = false): Promise<void> {
        if (!state.active && !force) return;
        if (state.fetchInFlight) return;
        state.fetchInFlight = true;
        try {
            const snapshot = await requestJson<GamesScheduleSnapshot>(`${getApiBase()}/api/schedule`);
            state.gamesUnavailable = false;
            state.consecutiveErrors = 0;
            state.lastErrorMessage = null;
            renderGamesTable(snapshot);
        } catch (err) {
            handleGamesFetchError(err);
        } finally {
            state.fetchInFlight = false;
        }
    }

    function navigateToEngineMatchups(engineName: string): void {
        const name = typeof engineName === 'string' ? engineName.trim() : '';
        if (!name) return;
        const navigation = owner.DashboardNavigation;
        if (!navigation || typeof navigation.focusEngine !== 'function') {
            throw new Error('DashboardNavigation.focusEngine is unavailable');
        }
        navigation.focusEngine(name, { tab: 'tournament', scroll: true });
    }

    function getApi(): DashboardGamesApi | null {
        return dashboardApi;
    }

    function setApi(api: DashboardGamesApi): void {
        dashboardApi = api;
    }

    function startRealtime(): void {
        if (!state.active) return;
        attachGamesDeltaListener();
        cleanupRetryTimer();
        state.sseConnected = false;
        state.sseRetryDelayMs = null;
        state.gamesUnavailable = false;
        if (!state.lastSnapshot) {
            scheduleSnapshotFallback('start');
        }
    }

    function stopRealtime(): void {
        cleanupRetryTimer();
        state.sseRetryDelayMs = null;
        state.sseConnected = false;
        detachGamesDeltaListener();
        clearSnapshotFallback();
        detachVirtualScrollListener();
        resetVirtualState();
        virtual.rows = [];
        virtual.enabled = false;
        if (virtual.tbody) {
            reconcileChildren(virtual.tbody, []);
        }
        pruneRowCache(new Set());
    }

    return {
        state,
        fetchGames,
        startRealtime,
        stopRealtime,
        applyGamesDelta,
        applyInstanceFilter,
        clearInstanceFilter,
        setSort,
        setSearchQuery,
        updateSortHeaders,
        updateFilterIndicator,
        renderGamesTable,
        showGamesUnavailable,
        openGameInLiveView,
        navigateToEngineMatchups,
        getApi,
        setApi,
    } satisfies ScheduleController;
}
