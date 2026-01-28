import { createEmptyState } from '@/modules/shared/components/elements';
import type { DashboardGamesApi } from '@/modules/games/types';
import type { DashboardNavigationApi } from '@/types/globals';
import type { InstanceActionResult, InstanceRecord, InstancesSnapshot } from '@/modules/instances/types';
import { apiBase, postInstanceAction, requestJson } from '@/modules/instances/services/api';
import {
    HEALTH_CHECK_MAX_AGE_MS,
    HEALTH_CHECK_MIN_INTERVAL_MS,
    RESOURCE_SAMPLE_MIN_INTERVAL_MS,
    SPARK_HISTORY_MS,
    SPARK_MAX_POINTS,
} from '@/modules/instances/state/constants';
import {
    type ActiveGameSummary,
    findInstanceById,
    formatInt,
    handleApiError,
    normalizeActiveGames,
    showNotice,
    truncate,
} from '@/modules/instances/utils/helpers';
import { applyActiveHighlights, processHighlightQueue } from '@/modules/instances/utils/highlights';
import type { InstancesDashboardState, LatencyCacheEntry } from '@/modules/instances/state';
import {
    buildCpuSubLine,
    buildHostLabel,
    buildMemBreakdown,
    buildSlotText,
    filterTags,
    formatLatency,
    normalizeType,
    resolveCpuViewMode,
} from '@/modules/instances/viewmodel';

const gridEventBindings = new WeakMap<
    Element,
    {
        click: (event: MouseEvent) => void;
        toggle: (event: Event) => void;
    }
>();

interface InstanceDomRefs {
    card: HTMLElement;
    title: HTMLElement;
    titleText: HTMLElement;
    statusIcon: HTMLElement;
    statusGroup: HTMLElement;
    tags: HTMLElement;
    slotsValue: HTMLElement;
    enginesValue: HTMLElement;
    engineProc: HTMLElement;
    activeGamesValue: HTMLElement;
    infoDetails: HTMLDetailsElement;
    infoList: HTMLDListElement;
    cpuUsage: HTMLElement;
    cpuSub: HTMLElement;
    memUsage: HTMLElement;
    latencyInfo: HTMLElement;
    latencyAvg: HTMLElement;
    latencyRecent: HTMLElement;
    metricsDetails: HTMLDetailsElement;
    metricsOverall: HTMLElement;
    threadsContainer: HTMLElement;
    gamesWrapper: HTMLElement;
    actionToggleDrain: HTMLButtonElement;
    actionProvision: HTMLButtonElement;
    actionDelete: HTMLButtonElement;
}

const instanceDomRegistry = new Map<string, InstanceDomRefs>();
let addInstanceCardEl: HTMLElement | null = null;
let emptyStateEl: HTMLElement | null = null;

export interface RenderCallbacks {
    readonly refresh: () => Promise<void>;
    readonly onRequestEdit: (instance: InstanceRecord) => void;
    readonly onRequestProvision: (instance: InstanceRecord) => void;
    readonly onDrainStarted: (instance: InstanceRecord) => void;
    readonly requestSparklineDraw?: (ids?: readonly string[]) => void;
}

function requireNavigationApi(): DashboardNavigationApi {
    const navigation = window.DashboardNavigation as DashboardNavigationApi | undefined;
    if (!navigation) {
        throw new Error('DashboardNavigation is unavailable');
    }
    if (typeof navigation.openGame !== 'function') {
        throw new Error('DashboardNavigation.openGame is unavailable');
    }
    if (typeof navigation.showInstanceGames !== 'function') {
        throw new Error('DashboardNavigation.showInstanceGames is unavailable');
    }
    return navigation;
}

function buildGamesSignature(games: readonly ActiveGameSummary[]): string {
    return JSON.stringify(
        games.map((game) => ({
            game_id: game.game_id ?? null,
            status: game.status ?? null,
            black_engine: game.black_engine ?? null,
            white_engine: game.white_engine ?? null,
            black_local:
                Array.isArray(game.roles) && game.roles.find((role) => role && role.role === 'black')?.local ? 1 : 0,
            white_local:
                Array.isArray(game.roles) && game.roles.find((role) => role && role.role === 'white')?.local ? 1 : 0,
        })),
    );
}

function filterActiveGames(games: readonly ActiveGameSummary[]): ActiveGameSummary[] {
    return games.filter((game) => game.status !== 'completed');
}

function createGamesEmptyState(doc: Document): HTMLElement {
    const empty = createEmptyState(doc, 'No active games right now. Use "View all games" for history.', 'muted');
    empty.dataset.role = 'games-empty';
    return empty;
}

function ensureViewAllButton(doc: Document, container: HTMLElement, instanceId: string): HTMLButtonElement {
    let button = container.querySelector<HTMLButtonElement>('button.inst-games-viewall');
    if (!button) {
        button = doc.createElement('button');
        button.type = 'button';
        button.className = 'btn ghost inst-games-viewall';
        button.dataset.action = 'view_games';
        container.appendChild(button);
    }
    button.dataset.instanceId = instanceId;
    button.textContent = 'View all games for this instance';
    button.title = `Show games scheduled for ${instanceId}`;
    return button;
}

function ensureGamesTable(doc: Document, container: HTMLElement): HTMLTableElement {
    let table = container.querySelector<HTMLTableElement>('table.inst-games-table');
    if (table) return table;
    const empty = container.querySelector<HTMLElement>('[data-role="games-empty"]');
    if (empty) empty.remove();
    table = doc.createElement('table');
    table.className = 'inst-games-table standings-table';
    table.setAttribute('aria-label', 'Instance games');

    const thead = doc.createElement('thead');
    const headerRow = doc.createElement('tr');
    ['#', 'Game', 'Black', 'White'].forEach((label) => {
        const th = doc.createElement('th');
        th.scope = 'col';
        th.textContent = label;
        headerRow.appendChild(th);
    });
    thead.appendChild(headerRow);
    table.appendChild(thead);

    const tbody = doc.createElement('tbody');
    table.appendChild(tbody);
    const viewAll = container.querySelector<HTMLButtonElement>('button.inst-games-viewall');
    if (viewAll) {
        container.insertBefore(table, viewAll);
    } else {
        container.appendChild(table);
    }
    return table;
}

function createGameRow(doc: Document, key: string): HTMLTableRowElement {
    const row = doc.createElement('tr');
    row.className = 'inst-game-row';
    row.dataset.gameKey = key;

    const indexCell = doc.createElement('td');
    indexCell.className = 'inst-game-index table-group-meta';
    indexCell.dataset.role = 'game-index';
    row.appendChild(indexCell);

    const gameCell = doc.createElement('td');
    gameCell.className = 'inst-game-id table-group-id';
    gameCell.dataset.role = 'game-id';
    row.appendChild(gameCell);

    const blackCell = doc.createElement('td');
    blackCell.className = 'inst-game-black table-group-engine';
    blackCell.dataset.role = 'game-black';
    row.appendChild(blackCell);

    const whiteCell = doc.createElement('td');
    whiteCell.className = 'inst-game-white table-group-engine';
    whiteCell.dataset.role = 'game-white';
    row.appendChild(whiteCell);

    return row;
}

function renderGameIdCell(cell: HTMLElement, gameId: string | null): void {
    cell.replaceChildren();
    if (gameId) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'inst-game-link';
        button.dataset.gameId = gameId;
        button.setAttribute('aria-label', `Open Live View for game ${gameId}`);
        button.textContent = gameId;
        cell.appendChild(button);
        return;
    }
    const span = document.createElement('span');
    span.className = 'muted';
    span.textContent = 'Unassigned';
    cell.appendChild(span);
}

function renderEngineCell(
    cell: HTMLElement,
    engineName: string | null,
    roleInfo: ActiveGameSummary['roles'][number] | undefined,
): void {
    cell.replaceChildren();
    const display = typeof engineName === 'string' ? engineName.trim() : '';
    if (!display) {
        const placeholder = document.createElement('span');
        placeholder.className = 'muted';
        placeholder.textContent = '-';
        cell.appendChild(placeholder);
        return;
    }
    const engineButton = document.createElement('button');
    engineButton.type = 'button';
    engineButton.className = 'games-engine-link inst-engine-link';
    engineButton.dataset.engineName = encodeURIComponent(display);
    engineButton.setAttribute('aria-label', `Show matchups for ${display}`);
    engineButton.textContent = display;
    if (roleInfo?.local) {
        const strong = document.createElement('strong');
        strong.appendChild(engineButton);
        cell.appendChild(strong);
        return;
    }
    cell.appendChild(engineButton);
}

function updateInstanceGames(gamesWrapper: HTMLElement, inst: InstanceRecord): void {
    const activeGamesRaw = Array.isArray(inst.active_games) ? inst.active_games : [];
    const normalizedGames = filterActiveGames(normalizeActiveGames(activeGamesRaw));
    const signature = buildGamesSignature(normalizedGames);
    const container = gamesWrapper.querySelector<HTMLElement>('.inst-games-body');
    if (!container) {
        gamesWrapper.replaceChildren(renderInstanceGamesList(document, normalizedGames, inst.id));
        return;
    }
    if (container.dataset.signature === signature) {
        return;
    }
    container.dataset.signature = signature;
    ensureViewAllButton(document, container, inst.id);

    if (!normalizedGames.length) {
        const table = container.querySelector<HTMLElement>('table.inst-games-table');
        if (table) table.remove();
        if (!container.querySelector('[data-role="games-empty"]')) {
            const empty = createGamesEmptyState(document);
            const viewAll = container.querySelector<HTMLButtonElement>('button.inst-games-viewall');
            if (viewAll) {
                container.insertBefore(empty, viewAll);
            } else {
                container.appendChild(empty);
            }
        }
        return;
    }

    const empty = container.querySelector<HTMLElement>('[data-role="games-empty"]');
    if (empty) empty.remove();
    const table = ensureGamesTable(document, container);
    const tbody = table.tBodies[0] ?? table.appendChild(document.createElement('tbody'));
    const existingRows = new Map<string, HTMLTableRowElement>();
    Array.from(tbody.querySelectorAll<HTMLTableRowElement>('tr.inst-game-row')).forEach((row) => {
        const key = row.dataset.gameKey;
        if (key) existingRows.set(key, row);
    });

    const usedKeys = new Set<string>();
    const fragment = document.createDocumentFragment();

    normalizedGames.forEach((game, index) => {
        const status = String(game.status || 'active').toLowerCase();
        const gameId = game?.game_id ? String(game.game_id) : null;
        const key = gameId ? `id:${gameId}` : `idx:${index}`;
        usedKeys.add(key);
        let row = existingRows.get(key);
        if (!row) {
            row = createGameRow(document, key);
        }
        const indexCell = row.querySelector<HTMLElement>('[data-role="game-index"]');
        if (indexCell) indexCell.textContent = String(index + 1);

        const roles = Array.isArray(game.roles) ? game.roles : [];
        const findRole = (side: 'black' | 'white') => roles.find((role) => role && role.role === side);
        const blackLocal = findRole('black')?.local ? 1 : 0;
        const whiteLocal = findRole('white')?.local ? 1 : 0;
        const rowSig = `${status}|${gameId ?? ''}|${game.black_engine ?? ''}|${game.white_engine ?? ''}|${blackLocal}|${whiteLocal}`;
        if (row.dataset.signature !== rowSig) {
            row.dataset.signature = rowSig;
            row.className = `inst-game-row inst-game--${status}`;
            const gameCell = row.querySelector<HTMLElement>('[data-role="game-id"]');
            if (gameCell) renderGameIdCell(gameCell, gameId);
            const blackCell = row.querySelector<HTMLElement>('[data-role="game-black"]');
            if (blackCell) renderEngineCell(blackCell, game.black_engine, findRole('black'));
            const whiteCell = row.querySelector<HTMLElement>('[data-role="game-white"]');
            if (whiteCell) renderEngineCell(whiteCell, game.white_engine, findRole('white'));
        }
        fragment.appendChild(row);
    });

    existingRows.forEach((row, key) => {
        if (!usedKeys.has(key)) {
            row.remove();
        }
    });

    tbody.appendChild(fragment);
}

function navigateToEngineMatchups(engineName: string): void {
    const name = engineName.trim();
    if (!name) {
        throw new Error('Instances dashboard engine link is missing engine name');
    }
    const navigation = requireNavigationApi();
    if (typeof navigation.focusEngine !== 'function') {
        throw new Error('DashboardNavigation.focusEngine is unavailable');
    }
    navigation.focusEngine(name, { tab: 'tournament', scroll: true });
}

function navigateToLiveGame(gameId: string): void {
    if (!gameId) {
        throw new Error('Instances dashboard game link is missing game id');
    }
    const navigation = requireNavigationApi();
    void navigation.openGame(gameId, { source: 'instances' });
}

function createAddInstanceCard(doc: Document): HTMLElement {
    const card = doc.createElement('div');
    card.className = 'inst-card inst-card--add';
    card.dataset.role = 'add-instance';

    const button = doc.createElement('button');
    button.type = 'button';
    button.className = 'inst-add-card';
    button.dataset.action = 'instances:add';

    const icon = doc.createElement('span');
    icon.className = 'inst-add-icon';
    icon.setAttribute('aria-hidden', 'true');
    icon.textContent = '+';
    button.appendChild(icon);

    const label = doc.createElement('span');
    label.className = 'inst-add-label';
    label.textContent = 'Add Instance';
    button.appendChild(label);

    const sr = doc.createElement('span');
    sr.className = 'sr-only';
    sr.textContent = 'Add new instance';
    button.appendChild(sr);

    card.appendChild(button);
    return card;
}

function renderInstanceGamesList(doc: Document, games: readonly ActiveGameSummary[], instanceId: string): HTMLElement {
    const container = doc.createElement('div');
    container.className = 'inst-games-body table-panel';
    container.dataset.instanceId = instanceId;
    container.dataset.signature = buildGamesSignature(games);

    if (!Array.isArray(games) || games.length === 0) {
        const empty = createEmptyState(doc, 'No active games right now. Use "View all games" for history.', 'muted');
        empty.dataset.role = 'games-empty';
        container.appendChild(empty);
    } else {
        const table = doc.createElement('table');
        table.className = 'inst-games-table standings-table';
        table.setAttribute('aria-label', 'Instance games');

        const thead = doc.createElement('thead');
        const headerRow = doc.createElement('tr');
        ['#', 'Game', 'Black', 'White'].forEach((label) => {
            const th = doc.createElement('th');
            th.scope = 'col';
            th.textContent = label;
            headerRow.appendChild(th);
        });
        thead.appendChild(headerRow);
        table.appendChild(thead);

        const tbody = doc.createElement('tbody');
        games.forEach((game, index) => {
            const status = String(game.status || 'active').toLowerCase();
            const gameId = game?.game_id ? String(game.game_id) : null;
            const key = gameId ? `id:${gameId}` : `idx:${index}`;
            const roles = Array.isArray(game.roles) ? game.roles : [];
            const findRole = (side: 'black' | 'white') => roles.find((role) => role && role.role === side);
            const blackLocal = findRole('black')?.local ? 1 : 0;
            const whiteLocal = findRole('white')?.local ? 1 : 0;
            const row = createGameRow(doc, key);
            row.className = `inst-game-row inst-game--${status}`;
            row.dataset.signature = `${status}|${gameId ?? ''}|${game.black_engine ?? ''}|${game.white_engine ?? ''}|${blackLocal}|${whiteLocal}`;
            const indexCell = row.querySelector<HTMLElement>('[data-role="game-index"]');
            if (indexCell) indexCell.textContent = String(index + 1);
            const gameCell = row.querySelector<HTMLElement>('[data-role="game-id"]');
            if (gameCell) renderGameIdCell(gameCell, gameId);
            const blackCell = row.querySelector<HTMLElement>('[data-role="game-black"]');
            if (blackCell) renderEngineCell(blackCell, game.black_engine, findRole('black'));
            const whiteCell = row.querySelector<HTMLElement>('[data-role="game-white"]');
            if (whiteCell) renderEngineCell(whiteCell, game.white_engine, findRole('white'));
            tbody.appendChild(row);
        });

        table.appendChild(tbody);
        container.appendChild(table);
    }

    const viewAllButton = doc.createElement('button');
    viewAllButton.type = 'button';
    viewAllButton.className = 'btn ghost inst-games-viewall';
    viewAllButton.dataset.action = 'view_games';
    viewAllButton.dataset.instanceId = instanceId;
    viewAllButton.textContent = 'View all games for this instance';
    viewAllButton.title = `Show games scheduled for ${instanceId}`;

    container.appendChild(viewAllButton);

    return container;
}

function createThreadGrid(doc: Document, instId: string, perCore: readonly number[]): HTMLElement {
    const grid = doc.createElement('div');
    grid.className = 'inst-thread-grid';
    grid.dataset.role = 'thread-grid';
    grid.dataset.threadCount = String(perCore.length);
    perCore.forEach((value, idx) => {
        const thread = doc.createElement('div');
        thread.className = 'inst-thread';
        thread.dataset.thread = String(idx);

        const label = doc.createElement('span');
        label.className = 'inst-thread-label';
        label.textContent = `Core ${idx + 1}`;
        thread.appendChild(label);

        const canvas = doc.createElement('canvas');
        canvas.className = 'inst-spark';
        canvas.dataset.id = instId;
        canvas.dataset.kind = 'core';
        canvas.dataset.core = String(idx);
        canvas.width = 120;
        canvas.height = 60;
        thread.appendChild(canvas);

        const valueSpan = doc.createElement('span');
        valueSpan.className = 'inst-thread-value';
        valueSpan.dataset.role = 'thread-value';
        valueSpan.textContent = `${value.toFixed(1)}%`;
        thread.appendChild(valueSpan);

        grid.appendChild(thread);
    });
    return grid;
}

function pruneHistory(state: InstancesDashboardState, validIds: Set<string>): void {
    Object.keys(state.history).forEach((key) => {
        if (!validIds.has(key)) {
            delete state.history[key];
        }
    });
    Object.keys(state.latencyCache).forEach((key) => {
        if (!validIds.has(key)) {
            delete state.latencyCache[key];
        }
    });
    Object.keys(state.cpuViewMode).forEach((key) => {
        if (!validIds.has(key)) {
            delete state.cpuViewMode[key];
        }
    });
    Object.keys(state.healthCheckTs).forEach((key) => {
        if (!validIds.has(key)) {
            delete state.healthCheckTs[key];
        }
    });
    for (const instId of Array.from(state.metricsOpen)) {
        if (!validIds.has(instId)) {
            state.metricsOpen.delete(instId);
        }
    }
    for (const instId of Array.from(state.infoOpen)) {
        if (!validIds.has(instId)) {
            state.infoOpen.delete(instId);
        }
    }
}

function ensureHistory(state: InstancesDashboardState, id: string) {
    if (!state.history[id]) {
        state.history[id] = { cpu: [], mem: [], cores: {} };
    }
    if (!state.latencyCache[id]) {
        state.latencyCache[id] = {
            avg: null,
            recent: null,
            samples: 0,
            lastUpdated: 0,
            seen: false,
        } satisfies LatencyCacheEntry;
    }
}

function updateHistory(state: InstancesDashboardState, instance: InstanceRecord, now: number): boolean {
    ensureHistory(state, instance.id);
    const history = state.history[instance.id];
    const latency = state.latencyCache[instance.id];
    const metrics = instance.metrics ?? {};
    const cpuSeries = history.cpu;
    const memSeries = history.mem;
    const lastCpuTs = cpuSeries.length ? (cpuSeries[cpuSeries.length - 1]?.ts ?? 0) : 0;
    const lastMemTs = memSeries.length ? (memSeries[memSeries.length - 1]?.ts ?? 0) : 0;
    let lastCoreTs = 0;
    Object.values(history.cores).forEach((series) => {
        const ts = series.length ? (series[series.length - 1]?.ts ?? 0) : 0;
        if (ts > lastCoreTs) lastCoreTs = ts;
    });
    const rawSampleTs =
        typeof metrics.timestamp === 'number' && Number.isFinite(metrics.timestamp)
            ? Math.max(0, Math.round(metrics.timestamp * 1000))
            : now;
    const interval = RESOURCE_SAMPLE_MIN_INTERVAL_MS;
    const sampleTs = Math.max(0, Math.round(rawSampleTs / interval) * interval);
    const lastSampleTs = Math.max(lastCpuTs, lastMemTs, lastCoreTs);
    const shouldRecord = !lastSampleTs || sampleTs > lastSampleTs;
    let dirty = false;
    const cpuBefore = cpuSeries.length;
    const memBefore = memSeries.length;
    const coreBefore = Object.fromEntries(Object.entries(history.cores).map(([key, series]) => [key, series.length]));
    if (shouldRecord) {
        const lastCpu = cpuSeries.length ? (cpuSeries[cpuSeries.length - 1]?.value ?? null) : null;
        const lastMem = memSeries.length ? (memSeries[memSeries.length - 1]?.value ?? null) : null;
        const nextCpu =
            typeof metrics.cpu_usage_pct === 'number' ? metrics.cpu_usage_pct : lastCpu !== null ? lastCpu : null;
        const nextMem =
            typeof metrics.mem_used_pct === 'number' ? metrics.mem_used_pct : lastMem !== null ? lastMem : null;
        if (nextCpu !== null) {
            cpuSeries.push({ ts: sampleTs, value: nextCpu });
        }
        if (nextMem !== null) {
            memSeries.push({ ts: sampleTs, value: nextMem });
        }
        if (nextCpu !== null || nextMem !== null) {
            dirty = true;
        }
    }
    const cutoff = now - SPARK_HISTORY_MS;
    const pruneSeries = (series: Array<{ ts: number; value: number }>): void => {
        while (series.length > SPARK_MAX_POINTS) {
            series.shift();
        }
        while (series.length >= 2 && (series[1]?.ts ?? Infinity) < cutoff) {
            series.shift();
        }
    };
    pruneSeries(cpuSeries);
    pruneSeries(memSeries);
    const coreHistory = history.cores;
    if (shouldRecord && Array.isArray(metrics.cpu_usage_pct_per_core)) {
        metrics.cpu_usage_pct_per_core.forEach((value, idx) => {
            if (typeof value !== 'number') return;
            const key = String(idx);
            let series = coreHistory[key];
            if (!series) {
                series = [];
                coreHistory[key] = series;
            }
            series.push({ ts: sampleTs, value });
            pruneSeries(series);
        });
        dirty = true;
    }
    Object.keys(coreHistory).forEach((key) => {
        const series = coreHistory[key];
        if (!series) return;
        pruneSeries(series);
        if (series.length === 0) {
            delete coreHistory[key];
        }
    });
    if (cpuSeries.length !== cpuBefore || memSeries.length !== memBefore) {
        dirty = true;
    }
    for (const [key, beforeLen] of Object.entries(coreBefore)) {
        const afterLen = coreHistory[key]?.length ?? 0;
        if (afterLen !== beforeLen) {
            dirty = true;
            break;
        }
    }

    const hasAvg = typeof metrics.network_rtt_avg_ms === 'number' && Number.isFinite(metrics.network_rtt_avg_ms);
    const hasRecent =
        typeof metrics.network_rtt_recent_ms === 'number' && Number.isFinite(metrics.network_rtt_recent_ms);
    const samples =
        typeof metrics.network_rtt_samples === 'number' && Number.isFinite(metrics.network_rtt_samples)
            ? Math.max(0, Math.floor(metrics.network_rtt_samples))
            : latency.samples;
    if (hasAvg || hasRecent) {
        latency.avg = hasAvg ? Number(metrics.network_rtt_avg_ms) : latency.avg;
        latency.recent = hasRecent ? Number(metrics.network_rtt_recent_ms) : latency.recent;
        latency.samples = samples;
        latency.lastUpdated = now;
        latency.seen = true;
    } else if (samples !== latency.samples) {
        latency.samples = samples;
    }
    return dirty;
}

function ensureAddCard(doc: Document): HTMLElement {
    if (!addInstanceCardEl) {
        addInstanceCardEl = createAddInstanceCard(doc);
    }
    return addInstanceCardEl;
}

function ensureEmptyState(doc: Document): HTMLElement {
    if (!emptyStateEl) {
        emptyStateEl = createEmptyState(doc, 'No instances configured. Run with --instances to enable.', 'muted');
        emptyStateEl.dataset.role = 'instances-empty';
    }
    return emptyStateEl;
}

function updateTitleAndStatus(refs: InstanceDomRefs, inst: InstanceRecord): void {
    const metrics = inst.metrics || {};
    const reachable = metrics.reachable !== false;
    const sig = `${inst.id}:${reachable ? 1 : 0}:${inst.drain ? 1 : 0}`;
    if (refs.card.dataset.statusSig === sig) {
        return;
    }
    refs.card.dataset.statusSig = sig;
    refs.statusIcon.textContent = '⚠️';
    refs.statusIcon.toggleAttribute('hidden', reachable);
    refs.titleText.textContent = inst.id;
    refs.card.classList.toggle('inst-card--down', !reachable);

    const pills: HTMLElement[] = [];
    if (!reachable) {
        const unreachable = document.createElement('span');
        unreachable.className = 'pill ng';
        unreachable.textContent = 'Unreachable';
        pills.push(unreachable);
    }
    if (inst.drain) {
        const draining = document.createElement('span');
        draining.className = 'pill warn';
        draining.textContent = 'Draining';
        pills.push(draining);
    }
    refs.statusGroup.replaceChildren(...pills);
    refs.statusGroup.toggleAttribute('hidden', pills.length === 0);
}

function updateTags(refs: InstanceDomRefs, inst: InstanceRecord): void {
    const normalizedType = normalizeType(inst.type);
    const filteredTags = filterTags(inst.config?.tags, normalizedType);
    if (!filteredTags.length) {
        refs.tags.replaceChildren();
        refs.tags.toggleAttribute('hidden', true);
        return;
    }
    const signature = filteredTags.map((tag) => tag.trim()).join('|');
    if (refs.tags.dataset.signature === signature) {
        refs.tags.toggleAttribute('hidden', false);
        return;
    }
    refs.tags.dataset.signature = signature;
    refs.tags.replaceChildren(
        ...filteredTags.map((tag) => {
            const pillTag = document.createElement('span');
            pillTag.className = 'pill tag';
            pillTag.textContent = tag.trim();
            return pillTag;
        }),
    );
    refs.tags.toggleAttribute('hidden', false);
}

function updateStatsRow(refs: InstanceDomRefs, inst: InstanceRecord): void {
    const metrics = inst.metrics || {};
    const config = inst.config || {};
    const slotCapacity =
        typeof inst.slot_capacity === 'number' && Number.isFinite(inst.slot_capacity)
            ? inst.slot_capacity
            : config.slots;
    const engineLimit =
        typeof inst.engine_limit === 'number' && Number.isFinite(inst.engine_limit) ? inst.engine_limit : null;
    const engineProcesses =
        typeof metrics.engine_processes === 'number' && Number.isFinite(metrics.engine_processes)
            ? Math.max(0, Math.floor(metrics.engine_processes))
            : null;
    const activeGamesCount = filterActiveGames(normalizeActiveGames(inst.active_games)).length;
    const sig = [
        metrics.in_use_slots ?? '',
        slotCapacity ?? '',
        metrics.in_use_engines ?? '',
        engineLimit ?? '',
        engineProcesses ?? '',
        activeGamesCount,
    ].join('|');
    if (refs.card.dataset.statsSig === sig) {
        return;
    }
    refs.card.dataset.statsSig = sig;
    refs.slotsValue.textContent = buildSlotText(metrics.in_use_slots, slotCapacity);
    refs.enginesValue.textContent = buildSlotText(metrics.in_use_engines, engineLimit);
    refs.activeGamesValue.textContent = String(activeGamesCount);
    if (engineProcesses !== null) {
        refs.engineProc.textContent = ` (proc ${engineProcesses})`;
        refs.engineProc.toggleAttribute('hidden', false);
    } else {
        refs.engineProc.textContent = '';
        refs.engineProc.toggleAttribute('hidden', true);
    }
}

function updateInfoDetails(refs: InstanceDomRefs, inst: InstanceRecord): void {
    const config = inst.config || {};
    const metaRows: Array<{ label: string; value: string }> = [];
    if (inst.type) {
        metaRows.push({ label: 'Type', value: inst.type });
    }
    const host = buildHostLabel(config.host, config.user);
    if (host) {
        metaRows.push({ label: 'Host', value: host });
    }
    if (inst.config_path) {
        metaRows.push({ label: 'Config path', value: inst.config_path });
    }
    if (inst.last_seen) {
        const seen =
            typeof inst.last_seen === 'number' ? new Date(inst.last_seen * 1000).toISOString() : String(inst.last_seen);
        metaRows.push({ label: 'Last seen', value: seen });
    }
    if (!metaRows.length) {
        refs.infoList.replaceChildren();
        refs.infoDetails.toggleAttribute('hidden', true);
        return;
    }
    const sig = metaRows.map((row) => `${row.label}:${row.value}`).join('|');
    if (refs.infoList.dataset.signature === sig) {
        refs.infoDetails.toggleAttribute('hidden', false);
        return;
    }
    refs.infoList.dataset.signature = sig;
    refs.infoDetails.toggleAttribute('hidden', false);
    const fragment = document.createDocumentFragment();
    metaRows.forEach((row) => {
        const dt = document.createElement('dt');
        dt.textContent = row.label;
        const dd = document.createElement('dd');
        dd.textContent = row.value;
        fragment.appendChild(dt);
        fragment.appendChild(dd);
    });
    refs.infoList.replaceChildren(fragment);
}

function updateActions(refs: InstanceDomRefs, inst: InstanceRecord): void {
    const sig = `${inst.drain ? 1 : 0}:${inst.is_ssh ? 1 : 0}:${inst.is_local ? 1 : 0}`;
    if (refs.card.dataset.actionSig === sig) {
        return;
    }
    refs.card.dataset.actionSig = sig;
    const icon = refs.actionToggleDrain.querySelector<HTMLElement>('[data-role="action-icon"]');
    const sr = refs.actionToggleDrain.querySelector<HTMLElement>('[data-role="action-sr"]');
    refs.actionToggleDrain.title = inst.drain ? 'Resume accepting new jobs' : 'Stop accepting new jobs (drain)';
    if (icon) icon.textContent = inst.drain ? '▶️' : '⏸️';
    if (sr) sr.textContent = inst.drain ? 'Undrain instance' : 'Drain instance';
    refs.actionProvision.disabled = !inst.is_ssh;
    refs.actionProvision.setAttribute('aria-disabled', String(!inst.is_ssh));
    refs.actionDelete.disabled = Boolean(inst.is_local);
    refs.actionDelete.setAttribute('aria-disabled', String(Boolean(inst.is_local)));
}

function updateMetricsData(refs: InstanceDomRefs, inst: InstanceRecord, state: InstancesDashboardState): void {
    const metrics = inst.metrics || {};
    const perCoreRaw = Array.isArray(metrics.cpu_usage_pct_per_core) ? metrics.cpu_usage_pct_per_core : [];
    const perCore = perCoreRaw.filter((value) => typeof value === 'number');
    const hasPerCore = perCore.length > 0;
    const latency = state.latencyCache[inst.id];
    const metricsSig = [
        metrics.cpu_usage_pct ?? '',
        metrics.mem_used_pct ?? '',
        metrics.mem_used_mb ?? '',
        metrics.mem_total_mb ?? '',
        metrics.cpu_model ?? '',
        metrics.network_rtt_avg_ms ?? '',
        metrics.network_rtt_recent_ms ?? '',
        metrics.network_rtt_samples ?? '',
        latency?.avg ?? '',
        latency?.recent ?? '',
        latency?.samples ?? '',
        latency?.seen ? 1 : 0,
        perCore.length,
        perCoreRaw.map((value) => (typeof value === 'number' ? value.toFixed(1) : '')).join(','),
    ].join('|');
    if (refs.card.dataset.metricsSig === metricsSig) {
        return;
    }
    refs.card.dataset.metricsSig = metricsSig;
    const cpuUsage = typeof metrics.cpu_usage_pct === 'number' ? `${metrics.cpu_usage_pct.toFixed(1)}%` : '-';
    refs.cpuUsage.textContent = cpuUsage;

    const cpuModelRaw = typeof metrics.cpu_model === 'string' ? metrics.cpu_model.trim() : '';
    const cpuModel = cpuModelRaw ? truncate(cpuModelRaw, 72) : '';
    const cpuSubLine = buildCpuSubLine(perCore.length, cpuModel);
    refs.cpuSub.textContent = cpuSubLine;
    refs.cpuSub.toggleAttribute('hidden', !cpuSubLine);

    const memUsed = formatInt(metrics.mem_used_mb);
    const memTotal = formatInt(metrics.mem_total_mb);
    const memBreakdown = buildMemBreakdown(
        typeof metrics.mem_used_pct === 'number' ? metrics.mem_used_pct : null,
        memUsed,
        memTotal,
    );
    refs.memUsage.textContent = memBreakdown;

    const rttAvgMs =
        typeof metrics.network_rtt_avg_ms === 'number' && Number.isFinite(metrics.network_rtt_avg_ms)
            ? metrics.network_rtt_avg_ms
            : (latency?.avg ?? null);
    const rttRecentMs =
        typeof metrics.network_rtt_recent_ms === 'number' && Number.isFinite(metrics.network_rtt_recent_ms)
            ? metrics.network_rtt_recent_ms
            : (latency?.recent ?? null);
    const rttSamples =
        typeof metrics.network_rtt_samples === 'number' && Number.isFinite(metrics.network_rtt_samples)
            ? metrics.network_rtt_samples
            : (latency?.samples ?? 0);
    const showLatency =
        (typeof metrics.network_rtt_avg_ms === 'number' && Number.isFinite(metrics.network_rtt_avg_ms)) ||
        (typeof metrics.network_rtt_recent_ms === 'number' && Number.isFinite(metrics.network_rtt_recent_ms)) ||
        Boolean(latency?.seen);
    refs.latencyInfo.toggleAttribute('hidden', !showLatency);
    refs.latencyAvg.textContent = formatLatency(rttAvgMs ?? null);
    const parts: string[] = [];
    parts.push(`Recent ${formatLatency(rttRecentMs ?? null)}`);
    if ((rttSamples ?? 0) > 0) {
        parts.push(`Samples ${rttSamples}`);
    }
    refs.latencyRecent.textContent = parts.join(' • ');

    state.cpuViewMode[inst.id] = resolveCpuViewMode(state.cpuViewMode[inst.id], hasPerCore);
    const cpuMode = state.cpuViewMode[inst.id];
    const overallHidden = cpuMode !== 'overall';
    const threadsHidden = cpuMode !== 'threads';
    refs.metricsOverall.classList.toggle('hidden', overallHidden);
    refs.threadsContainer.classList.toggle('hidden', threadsHidden);

    const modeButtons = refs.metricsDetails.querySelectorAll<HTMLButtonElement>('.metrics-mode-btn');
    modeButtons.forEach((button) => {
        const mode = button.dataset.mode === 'threads' ? 'threads' : 'overall';
        button.classList.toggle('active', mode === cpuMode);
        if (mode === 'threads') {
            button.disabled = !hasPerCore;
            button.setAttribute('aria-disabled', String(!hasPerCore));
        }
    });

    refs.threadsContainer.dataset.threadCount = String(perCore.length);
    const threadsGrid = refs.threadsContainer.querySelector<HTMLElement>('[data-role="thread-grid"]');
    if (hasPerCore) {
        if (!threadsGrid || threadsGrid.dataset.threadCount !== String(perCore.length)) {
            refs.threadsContainer.replaceChildren(createThreadGrid(document, inst.id, perCore));
        } else {
            const values = threadsGrid.querySelectorAll<HTMLElement>('[data-role="thread-value"]');
            perCore.forEach((value, idx) => {
                const node = values.item(idx);
                if (node) node.textContent = `${value.toFixed(1)}%`;
            });
        }
    } else if (threadsGrid) {
        refs.threadsContainer.replaceChildren();
    }
}

function updateInstanceCard(refs: InstanceDomRefs, inst: InstanceRecord, state: InstancesDashboardState): void {
    updateTitleAndStatus(refs, inst);
    updateTags(refs, inst);
    updateStatsRow(refs, inst);
    updateInfoDetails(refs, inst);
    updateActions(refs, inst);
    updateMetricsData(refs, inst, state);
    updateInstanceGames(refs.gamesWrapper, inst);
}

function renderInstanceCard(state: InstancesDashboardState, inst: InstanceRecord): InstanceDomRefs {
    const doc = document;
    const config = inst.config || {};
    const metrics = inst.metrics || {};
    const idRaw = inst.id || '';
    const slotCapacity =
        typeof inst.slot_capacity === 'number' && Number.isFinite(inst.slot_capacity)
            ? inst.slot_capacity
            : config.slots;
    const slotText = buildSlotText(metrics.in_use_slots, slotCapacity);
    const engineLimit =
        typeof inst.engine_limit === 'number' && Number.isFinite(inst.engine_limit) ? inst.engine_limit : null;
    const engineText = buildSlotText(metrics.in_use_engines, engineLimit);
    const engineProcesses =
        typeof metrics.engine_processes === 'number' && Number.isFinite(metrics.engine_processes)
            ? Math.max(0, Math.floor(metrics.engine_processes))
            : null;
    const reachable = metrics.reachable !== false;
    const host = buildHostLabel(config.host, config.user);
    const activeGamesRaw = Array.isArray(inst.active_games) ? inst.active_games : [];
    const normalizedGames = filterActiveGames(normalizeActiveGames(activeGamesRaw));
    ensureHistory(state, idRaw);
    const perCore = Array.isArray(metrics.cpu_usage_pct_per_core)
        ? metrics.cpu_usage_pct_per_core.filter((value) => typeof value === 'number')
        : [];
    const threadCount = perCore.length;
    const hasCpuUsage = typeof metrics.cpu_usage_pct === 'number';
    const cpuUsage = hasCpuUsage ? `${metrics.cpu_usage_pct.toFixed(1)}%` : '-';
    const cpuModelRaw = typeof metrics.cpu_model === 'string' ? metrics.cpu_model.trim() : '';
    const cpuModel = cpuModelRaw ? truncate(cpuModelRaw, 72) : '';
    const memUsed = formatInt(metrics.mem_used_mb);
    const memTotal = formatInt(metrics.mem_total_mb);
    const hasPerCore = perCore.length > 0;
    state.cpuViewMode[idRaw] = resolveCpuViewMode(state.cpuViewMode[idRaw], hasPerCore);
    const cpuMode = state.cpuViewMode[idRaw];
    const cpuSubLine = buildCpuSubLine(threadCount, cpuModel);
    const memBreakdown = buildMemBreakdown(
        typeof metrics.mem_used_pct === 'number' ? metrics.mem_used_pct : null,
        memUsed,
        memTotal,
    );

    const card = doc.createElement('div');
    card.className = 'inst-card';
    card.dataset.instId = idRaw;
    if (!reachable) {
        card.classList.add('inst-card--down');
    }

    const header = doc.createElement('div');
    header.className = 'inst-header';
    card.appendChild(header);

    const nameBlock = doc.createElement('div');
    nameBlock.className = 'inst-name-block';
    header.appendChild(nameBlock);

    const nameRow = doc.createElement('div');
    nameRow.className = 'inst-name-row';
    nameBlock.appendChild(nameRow);

    const title = doc.createElement('span');
    title.className = 'inst-title';
    title.dataset.role = 'inst-title';
    const icon = doc.createElement('span');
    icon.className = 'inst-status-icon';
    icon.dataset.role = 'status-icon';
    icon.setAttribute('aria-hidden', 'true');
    icon.textContent = '⚠️';
    if (reachable) {
        icon.setAttribute('hidden', 'true');
    }
    const titleText = doc.createElement('span');
    titleText.dataset.role = 'inst-title-text';
    titleText.textContent = idRaw;
    title.appendChild(icon);
    title.appendChild(titleText);
    nameRow.appendChild(title);

    const statusGroup = doc.createElement('div');
    statusGroup.className = 'inst-status-group';
    statusGroup.dataset.role = 'status-group';
    if (!reachable) {
        const unreachable = doc.createElement('span');
        unreachable.className = 'pill ng';
        unreachable.textContent = 'Unreachable';
        statusGroup.appendChild(unreachable);
    }
    if (inst.drain) {
        const draining = doc.createElement('span');
        draining.className = 'pill warn';
        draining.textContent = 'Draining';
        statusGroup.appendChild(draining);
    }
    if (statusGroup.childElementCount === 0) {
        statusGroup.setAttribute('hidden', 'true');
    }
    nameRow.appendChild(statusGroup);

    const actions = doc.createElement('div');
    actions.className = 'inst-header-actions';
    header.appendChild(actions);

    const makeIconButton = (opts: {
        readonly action: string;
        readonly title: string;
        readonly label: string;
        readonly srText: string;
        readonly role?: string;
        readonly disabled?: boolean;
    }) => {
        const button = doc.createElement('button');
        button.className = 'btn icon-btn';
        button.dataset.action = opts.action;
        button.dataset.id = idRaw;
        if (opts.role) {
            button.dataset.role = opts.role;
        }
        button.title = opts.title;
        if (opts.disabled) {
            button.disabled = true;
            button.setAttribute('aria-disabled', 'true');
        }
        const iconSpan = doc.createElement('span');
        iconSpan.setAttribute('aria-hidden', 'true');
        iconSpan.dataset.role = 'action-icon';
        iconSpan.textContent = opts.label;
        const srOnly = doc.createElement('span');
        srOnly.className = 'sr-only';
        srOnly.dataset.role = 'action-sr';
        srOnly.textContent = opts.srText;
        button.appendChild(iconSpan);
        button.appendChild(srOnly);
        return button;
    };

    const editButton = makeIconButton({
        action: 'edit',
        title: 'Edit instance',
        label: '✏️',
        srText: 'Edit instance',
        role: 'action-edit',
    });
    actions.appendChild(editButton);
    const provisionButton = makeIconButton({
        action: 'provision',
        title: 'Provision to remote',
        label: '🛠️',
        srText: 'Provision instance',
        disabled: !inst.is_ssh,
        role: 'action-provision',
    });
    actions.appendChild(provisionButton);
    const toggleDrainButton = makeIconButton({
        action: 'toggle_drain',
        title: inst.drain ? 'Resume accepting new jobs' : 'Stop accepting new jobs (drain)',
        label: inst.drain ? '▶️' : '⏸️',
        srText: inst.drain ? 'Undrain instance' : 'Drain instance',
        role: 'action-toggle-drain',
    });
    actions.appendChild(toggleDrainButton);
    const deleteButton = makeIconButton({
        action: 'delete',
        title: 'Delete instance',
        label: '🗑️',
        srText: 'Delete instance',
        disabled: Boolean(inst.is_local),
        role: 'action-delete',
    });
    actions.appendChild(deleteButton);

    const tagsContainer = doc.createElement('div');
    tagsContainer.className = 'inst-tags';
    tagsContainer.dataset.role = 'tags';
    tagsContainer.setAttribute('aria-label', 'Instance tags');
    tagsContainer.setAttribute('hidden', 'true');
    card.appendChild(tagsContainer);

    const statRow = doc.createElement('div');
    statRow.className = 'stat-row';
    const slotsDiv = doc.createElement('div');
    slotsDiv.append('Slots: ');
    const slotsStrong = doc.createElement('strong');
    slotsStrong.dataset.role = 'slots-value';
    slotsStrong.textContent = slotText;
    slotsDiv.appendChild(slotsStrong);
    statRow.appendChild(slotsDiv);
    const enginesDiv = doc.createElement('div');
    enginesDiv.append('Engines: ');
    const enginesStrong = doc.createElement('strong');
    enginesStrong.dataset.role = 'engines-value';
    enginesStrong.textContent = engineText;
    enginesDiv.appendChild(enginesStrong);
    const procSpan = doc.createElement('span');
    procSpan.className = 'muted';
    procSpan.dataset.role = 'engine-proc';
    if (engineProcesses !== null) {
        procSpan.textContent = ` (proc ${engineProcesses})`;
    } else {
        procSpan.setAttribute('hidden', 'true');
    }
    enginesDiv.appendChild(procSpan);
    statRow.appendChild(enginesDiv);
    const gamesDiv = doc.createElement('div');
    gamesDiv.append('Active games: ');
    const gamesStrong = doc.createElement('strong');
    gamesStrong.dataset.role = 'active-games-value';
    gamesStrong.textContent = String(normalizedGames.length);
    gamesDiv.appendChild(gamesStrong);
    statRow.appendChild(gamesDiv);
    card.appendChild(statRow);

    const metaRows: Array<{ label: string; value: string }> = [];
    if (inst.type) {
        metaRows.push({ label: 'Type', value: inst.type });
    }
    if (host) {
        metaRows.push({ label: 'Host', value: host });
    }
    if (inst.config_path) {
        metaRows.push({ label: 'Config path', value: inst.config_path });
    }
    if (inst.last_seen) {
        const seen =
            typeof inst.last_seen === 'number' ? new Date(inst.last_seen * 1000).toISOString() : String(inst.last_seen);
        metaRows.push({ label: 'Last seen', value: seen });
    }

    const infoDetails = doc.createElement('details');
    infoDetails.className = 'inst-section inst-info';
    infoDetails.dataset.instId = idRaw;
    infoDetails.dataset.role = 'info-details';
    if (!metaRows.length) {
        infoDetails.setAttribute('hidden', 'true');
    }
    const infoSummary = doc.createElement('summary');
    infoSummary.className = 'inst-info-summary';
    infoSummary.textContent = 'Instance details';
    infoDetails.appendChild(infoSummary);

    const infoList = doc.createElement('dl');
    infoList.className = 'inst-info-list';
    infoList.dataset.role = 'info-list';
    metaRows.forEach((row) => {
        const dt = doc.createElement('dt');
        dt.textContent = row.label;
        const dd = doc.createElement('dd');
        dd.textContent = row.value;
        infoList.appendChild(dt);
        infoList.appendChild(dd);
    });
    infoDetails.appendChild(infoList);
    card.appendChild(infoDetails);

    const metricsDetails = doc.createElement('details');
    metricsDetails.className = 'inst-section inst-metrics';
    metricsDetails.dataset.instId = idRaw;
    metricsDetails.dataset.role = 'metrics-details';
    metricsDetails.open = state.metricsOpen.has(idRaw);
    card.appendChild(metricsDetails);

    const metricsSummary = doc.createElement('summary');
    metricsSummary.className = 'metrics-summary';
    metricsSummary.textContent = 'Resource usage';
    metricsDetails.appendChild(metricsSummary);

    const cpuInfo = doc.createElement('div');
    cpuInfo.className = 'resource-info emphasize inst-metrics-block';
    const cpuTitle = doc.createElement('span');
    cpuTitle.className = 'resource-title';
    cpuTitle.textContent = 'CPU';
    cpuInfo.appendChild(cpuTitle);
    const cpuMeta = doc.createElement('span');
    cpuMeta.className = 'resource-meta accent';
    cpuMeta.dataset.role = 'cpu-usage';
    cpuMeta.textContent = cpuUsage;
    cpuInfo.appendChild(cpuMeta);
    const cpuSub = doc.createElement('span');
    cpuSub.className = 'resource-sub muted';
    cpuSub.dataset.role = 'cpu-sub';
    cpuSub.textContent = cpuSubLine;
    if (!cpuSubLine) {
        cpuSub.setAttribute('hidden', 'true');
    }
    cpuInfo.appendChild(cpuSub);
    metricsDetails.appendChild(cpuInfo);

    const toggleGroup = doc.createElement('div');
    toggleGroup.className = 'metrics-toggle';
    toggleGroup.setAttribute('role', 'group');
    toggleGroup.setAttribute('aria-label', 'CPU view mode');
    metricsDetails.appendChild(toggleGroup);

    const makeModeButton = (mode: 'overall' | 'threads') => {
        const button = doc.createElement('button');
        button.type = 'button';
        button.className = `metrics-mode-btn ${cpuMode === mode ? 'active' : ''}`.trim();
        button.dataset.id = idRaw;
        button.dataset.mode = mode;
        button.textContent = mode === 'overall' ? 'Overall' : 'Threads';
        if (mode === 'threads' && !hasPerCore) {
            button.disabled = true;
            button.setAttribute('aria-disabled', 'true');
        }
        return button;
    };

    toggleGroup.appendChild(makeModeButton('overall'));
    toggleGroup.appendChild(makeModeButton('threads'));

    const overallWrapper = doc.createElement('div');
    overallWrapper.className = `metrics-overall ${cpuMode === 'overall' ? '' : 'hidden'}`.trim();
    overallWrapper.dataset.role = 'metrics-overall';
    const overallSpark = doc.createElement('div');
    overallSpark.className = 'spark-wrapper';
    const overallCanvas = doc.createElement('canvas');
    overallCanvas.className = 'inst-spark';
    overallCanvas.dataset.id = idRaw;
    overallCanvas.dataset.kind = 'cpu';
    overallCanvas.width = 320;
    overallCanvas.height = 96;
    overallSpark.appendChild(overallCanvas);
    overallWrapper.appendChild(overallSpark);
    overallWrapper.classList.add('inst-metrics-block');
    metricsDetails.appendChild(overallWrapper);

    const threadsContainer = doc.createElement('div');
    threadsContainer.className = `metrics-threads inst-metrics-block ${cpuMode === 'threads' ? '' : 'hidden'}`.trim();
    threadsContainer.dataset.threadContainer = idRaw;
    threadsContainer.dataset.role = 'threads-container';
    threadsContainer.dataset.threadCount = String(perCore.length);
    if (hasPerCore) {
        threadsContainer.appendChild(createThreadGrid(doc, idRaw, perCore));
    }
    metricsDetails.appendChild(threadsContainer);

    if (inst.is_ssh && (!hasPerCore || !hasCpuUsage)) {
        const hint = doc.createElement('p');
        hint.className = 'resource-sub muted inst-metrics-block inst-metrics-tight';
        hint.textContent =
            'CPU metrics are unavailable for this SSH instance. Install the required tools on the remote host (e.g. run "sudo apt install -y sysstat procps") to enable usage and Threads view.';
        metricsDetails.appendChild(hint);
    }

    const memInfo = doc.createElement('div');
    memInfo.className = 'resource-info emphasize inst-metrics-block';
    const memTitle = doc.createElement('span');
    memTitle.className = 'resource-title';
    memTitle.textContent = 'RAM';
    memInfo.appendChild(memTitle);
    const memMeta = doc.createElement('span');
    memMeta.className = 'resource-meta accent';
    memMeta.dataset.role = 'mem-usage';
    memMeta.textContent = memBreakdown;
    memInfo.appendChild(memMeta);
    metricsDetails.appendChild(memInfo);
    memInfo.classList.add('inst-metrics-block', 'inst-metrics-tight');

    const memSpark = doc.createElement('div');
    memSpark.className = 'spark-wrapper inst-metrics-block';
    const memCanvas = doc.createElement('canvas');
    memCanvas.className = 'inst-spark';
    memCanvas.dataset.id = idRaw;
    memCanvas.dataset.kind = 'mem';
    memCanvas.width = 320;
    memCanvas.height = 96;
    memSpark.appendChild(memCanvas);
    metricsDetails.appendChild(memSpark);

    const latency = state.latencyCache[idRaw];
    const rttAvgMs =
        typeof metrics.network_rtt_avg_ms === 'number' && Number.isFinite(metrics.network_rtt_avg_ms)
            ? metrics.network_rtt_avg_ms
            : (latency?.avg ?? null);
    const rttRecentMs =
        typeof metrics.network_rtt_recent_ms === 'number' && Number.isFinite(metrics.network_rtt_recent_ms)
            ? metrics.network_rtt_recent_ms
            : (latency?.recent ?? null);
    const rttSamples =
        typeof metrics.network_rtt_samples === 'number' && Number.isFinite(metrics.network_rtt_samples)
            ? metrics.network_rtt_samples
            : (latency?.samples ?? 0);
    const showLatency =
        (typeof metrics.network_rtt_avg_ms === 'number' && Number.isFinite(metrics.network_rtt_avg_ms)) ||
        (typeof metrics.network_rtt_recent_ms === 'number' && Number.isFinite(metrics.network_rtt_recent_ms)) ||
        Boolean(latency?.seen);
    const latencyInfo = doc.createElement('div');
    latencyInfo.className = 'resource-info emphasize latency-info inst-metrics-block';
    latencyInfo.dataset.role = 'latency-info';
    if (!showLatency) {
        latencyInfo.setAttribute('hidden', 'true');
    }

    const latencyTitle = doc.createElement('span');
    latencyTitle.className = 'resource-title';
    latencyTitle.textContent = 'Network RTT';
    latencyInfo.appendChild(latencyTitle);

    const latencyMeta = doc.createElement('span');
    latencyMeta.className = 'resource-meta accent';
    latencyMeta.dataset.role = 'latency-avg';
    latencyMeta.textContent = formatLatency(rttAvgMs ?? null);
    latencyInfo.appendChild(latencyMeta);

    const latencySub = doc.createElement('span');
    latencySub.className = 'resource-sub muted';
    latencySub.dataset.role = 'latency-recent';
    const parts: string[] = [];
    const recentLabel = formatLatency(rttRecentMs ?? null);
    parts.push(`Recent ${recentLabel}`);
    if ((rttSamples ?? 0) > 0) {
        parts.push(`Samples ${rttSamples}`);
    }
    latencySub.textContent = parts.join(' • ');
    latencyInfo.appendChild(latencySub);

    metricsDetails.appendChild(latencyInfo);

    const gamesContainer = doc.createElement('div');
    gamesContainer.className = 'inst-games';
    gamesContainer.setAttribute('aria-live', 'polite');
    gamesContainer.dataset.role = 'games-wrapper';
    gamesContainer.replaceChildren(renderInstanceGamesList(doc, normalizedGames, idRaw));
    card.appendChild(gamesContainer);

    infoDetails.open = state.infoOpen.has(idRaw);
    return {
        card,
        title,
        titleText,
        statusIcon: icon,
        statusGroup,
        tags: tagsContainer,
        slotsValue: slotsStrong,
        enginesValue: enginesStrong,
        engineProc: procSpan,
        activeGamesValue: gamesStrong,
        infoDetails,
        infoList,
        cpuUsage: cpuMeta,
        cpuSub,
        memUsage: memMeta,
        latencyInfo,
        latencyAvg: latencyMeta,
        latencyRecent: latencySub,
        metricsDetails,
        metricsOverall: overallWrapper,
        threadsContainer,
        gamesWrapper: gamesContainer,
        actionToggleDrain: toggleDrainButton,
        actionProvision: provisionButton,
        actionDelete: deleteButton,
    };
}

function toggleViewMode(
    state: InstancesDashboardState,
    instId: string,
    mode: 'overall' | 'threads',
    callbacks: RenderCallbacks,
): void {
    if (state.cpuViewMode[instId] === mode) return;
    state.cpuViewMode[instId] = mode;
    const grid = document.getElementById('instancesGrid');
    if (!grid) {
        throw new Error('Instances dashboard requires #instancesGrid element');
    }
    const container = grid.querySelector<HTMLElement>(`details.inst-metrics[data-inst-id="${instId}"]`);
    if (!container) {
        throw new Error(`Instances dashboard is missing metrics container for ${instId}`);
    }
    const overall = container.querySelector<HTMLElement>('.metrics-overall');
    const threads = container.querySelector<HTMLElement>('.metrics-threads');
    if (!overall || !threads) {
        throw new Error(`Instances dashboard is missing metrics content for ${instId}`);
    }
    if (mode === 'threads') {
        overall.classList.add('hidden');
        threads.classList.remove('hidden');
    } else {
        overall.classList.remove('hidden');
        threads.classList.add('hidden');
    }
    callbacks.requestSparklineDraw?.([instId]);
}

function handleNavigateToLiveGame(gameId: string): void {
    navigateToLiveGame(gameId);
}

function handleNavigateToEngine(engineName: string): void {
    navigateToEngineMatchups(engineName);
}

async function handleToggleDrainAction(
    button: HTMLButtonElement,
    state: InstancesDashboardState,
    callbacks: RenderCallbacks,
): Promise<void> {
    if (button.disabled) return;
    const id = button.getAttribute('data-id');
    if (!id) {
        throw new Error('Instances dashboard action button is missing data-id attribute');
    }
    const inst = findInstanceById(state.data?.instances, id);
    if (!inst) {
        throw new Error(`Instances dashboard state is missing instance ${id}`);
    }
    const next = inst.drain ? 'undrain' : 'drain';
    try {
        const result = await postInstanceAction(state, id, next);
        await callbacks.refresh();
        if (next === 'drain' && (result as InstanceActionResult | null)?.success && inst) {
            callbacks.onDrainStarted(inst);
        }
    } catch (error) {
        handleApiError(error, 'Failed to toggle drain');
    }
}

function handleEditInstanceAction(
    button: HTMLButtonElement,
    state: InstancesDashboardState,
    callbacks: RenderCallbacks,
): void {
    if (button.disabled) return;
    const id = button.getAttribute('data-id');
    if (!id) {
        throw new Error('Instances dashboard edit action is missing data-id attribute');
    }
    const inst = findInstanceById(state.data?.instances, id);
    if (!inst) {
        throw new Error(`Instances dashboard state is missing instance ${id}`);
    }
    callbacks.onRequestEdit(inst);
}

async function handleDeleteInstanceAction(button: HTMLButtonElement, callbacks: RenderCallbacks): Promise<void> {
    if (button.disabled) return;
    const id = button.getAttribute('data-id');
    if (!id) {
        throw new Error('Instances dashboard delete action is missing data-id attribute');
    }
    if (!window.confirm(`Delete instance ${id}?`)) return;
    try {
        await requestJson(`${apiBase()}/api/instances/${encodeURIComponent(id)}`, { method: 'DELETE' });
        showNotice(`Deleted instance ${id}`, 'info');
        await callbacks.refresh();
    } catch (error) {
        handleApiError(error, 'Failed to delete instance');
    }
}

function handleProvisionInstanceAction(
    button: HTMLButtonElement,
    state: InstancesDashboardState,
    callbacks: RenderCallbacks,
): void {
    if (button.disabled) return;
    const id = button.getAttribute('data-id');
    if (!id) {
        throw new Error('Instances dashboard provision action is missing data-id attribute');
    }
    const inst = findInstanceById(state.data?.instances, id);
    if (!inst) {
        throw new Error(`Instances dashboard state is missing instance ${id}`);
    }
    callbacks.onRequestProvision(inst);
}

function handleViewGamesAction(button: HTMLButtonElement): void {
    const navigation = requireNavigationApi();
    const instanceId = (button.getAttribute('data-instance-id') || '').trim();
    if (!instanceId) {
        throw new Error('Instances dashboard view games action is missing instance identifier');
    }

    const owner = button.ownerDocument;
    if (!owner) {
        throw new Error('Instances dashboard games action is detached from document');
    }

    const defaultView = owner.defaultView as (Window & { DashboardGames?: DashboardGamesApi }) | null;
    const gamesApi = defaultView?.DashboardGames ?? null;

    navigation.openTab?.('games');

    const searchInput = owner.getElementById('gamesFilterInput') as HTMLInputElement | null;
    if (!searchInput) {
        throw new Error('Games filter input is unavailable');
    }

    gamesApi?.clearInstanceFilter?.();
    gamesApi?.setInstanceFilter?.(null, { activateTab: false });

    const query = `instance:${instanceId}`;
    searchInput.value = query;
    searchInput.dispatchEvent(new Event('input', { bubbles: true }));
    searchInput.focus({ preventScroll: true });
}

function handleMetricsModeAction(
    button: HTMLButtonElement,
    state: InstancesDashboardState,
    callbacks: RenderCallbacks,
): void {
    if (button.disabled) return;
    const instId = button.getAttribute('data-id');
    if (!instId) {
        throw new Error('Instances dashboard metrics toggle is missing data-id attribute');
    }
    const mode = button.getAttribute('data-mode') === 'threads' ? 'threads' : 'overall';
    toggleViewMode(state, instId, mode, callbacks);
}

function createGridClickHandler(
    grid: HTMLElement,
    state: InstancesDashboardState,
    callbacks: RenderCallbacks,
): (event: MouseEvent) => void {
    return (event: MouseEvent) => {
        const target = event.target as HTMLElement | null;
        if (!target || !grid.contains(target)) return;

        const button = target.closest<HTMLButtonElement>('button');
        if (!button) return;

        if (button.classList.contains('inst-game-link')) {
            event.preventDefault();
            event.stopPropagation();
            const gid = button.getAttribute('data-game-id');
            if (gid) handleNavigateToLiveGame(gid);
            return;
        }

        if (button.classList.contains('inst-engine-link')) {
            event.preventDefault();
            event.stopPropagation();
            const encoded = button.getAttribute('data-engine-name');
            let name = button.textContent || '';
            if (encoded) {
                name = decodeURIComponent(encoded);
            }
            handleNavigateToEngine(name);
            return;
        }

        if (button.classList.contains('metrics-mode-btn')) {
            event.preventDefault();
            event.stopPropagation();
            handleMetricsModeAction(button, state, callbacks);
            return;
        }

        const action = button.dataset.action;
        if (!action) {
            return;
        }

        if (action === 'instances:add') {
            return;
        }

        event.preventDefault();
        event.stopPropagation();

        switch (action) {
            case 'toggle_drain':
                void handleToggleDrainAction(button, state, callbacks);
                break;
            case 'edit':
                handleEditInstanceAction(button, state, callbacks);
                break;
            case 'delete':
                void handleDeleteInstanceAction(button, callbacks);
                break;
            case 'provision':
                handleProvisionInstanceAction(button, state, callbacks);
                break;
            case 'view_games':
                handleViewGamesAction(button);
                break;
            default:
                break;
        }
    };
}

function createGridToggleHandler(state: InstancesDashboardState): (event: Event) => void {
    return (event: Event) => {
        const detail = event.target as HTMLElement | null;
        if (!(detail instanceof HTMLDetailsElement)) return;
        const instId = detail.getAttribute('data-inst-id');
        if (!instId) {
            throw new Error('Instances dashboard details panel is missing data-inst-id attribute');
        }
        if (detail.matches('details.inst-metrics')) {
            if (detail.open) state.metricsOpen.add(instId);
            else state.metricsOpen.delete(instId);
        } else if (detail.matches('details.inst-info')) {
            if (detail.open) state.infoOpen.add(instId);
            else state.infoOpen.delete(instId);
        }
    };
}

function bindGridInteractions(grid: HTMLElement, state: InstancesDashboardState, callbacks: RenderCallbacks): void {
    if (gridEventBindings.has(grid)) {
        return;
    }
    const clickHandler = createGridClickHandler(grid, state, callbacks);
    const toggleHandler = createGridToggleHandler(state);
    grid.addEventListener('click', clickHandler);
    grid.addEventListener('toggle', toggleHandler, true);
    gridEventBindings.set(grid, { click: clickHandler, toggle: toggleHandler });
}

export function renderSnapshot(
    state: InstancesDashboardState,
    snapshot: InstancesSnapshot,
    callbacks: RenderCallbacks,
): void {
    const now = Date.now();
    const snapshotTs = typeof snapshot.timestamp === 'number' ? snapshot.timestamp * 1000 : null;
    state.lastSnapshotAt = snapshotTs ?? now;
    const statsEl = document.getElementById('instStats');
    const grid = document.getElementById('instancesGrid');
    if (!grid) {
        throw new Error('Instances dashboard requires #instancesGrid element');
    }

    if (statsEl) {
        const stats = snapshot.stats || {};
        const unknownSlots = stats.unknown_slots_instances ?? 0;
        const unknownLabel = unknownSlots > 0 ? ` | Unknown slots: ${unknownSlots}` : '';
        statsEl.textContent = `Instances: ${stats.total_instances ?? 0} (reachable ${stats.reachable_instances ?? 0}) | Draining: ${
            stats.draining_instances ?? 0
        } | Active hosts: ${stats.active_instances ?? 0} | Slots: ${stats.in_use_slots ?? 0}/${stats.total_slots ?? 0} (available ${
            stats.available_slots ?? 0
        })${unknownLabel} | Active games: ${stats.active_games ?? 0}`;
    }

    const list = snapshot.instances || [];
    const validIds = new Set(list.map((inst) => inst.id));
    pruneHistory(state, validIds);

    Array.from(instanceDomRegistry.keys()).forEach((id) => {
        if (validIds.has(id)) return;
        const refs = instanceDomRegistry.get(id);
        refs?.card.remove();
        instanceDomRegistry.delete(id);
    });

    if (!list.length) {
        grid.querySelectorAll<HTMLElement>('.inst-card[data-inst-id]').forEach((card) => {
            card.remove();
        });
        grid.replaceChildren(ensureEmptyState(document), ensureAddCard(document));
        bindGridInteractions(grid, state, callbacks);
        applyActiveHighlights(state);
        processHighlightQueue(state);
        const dirtyIds = Array.from(state.sparklineDirtyIds);
        state.sparklineDirtyIds.clear();
        callbacks.requestSparklineDraw?.(dirtyIds);
        return;
    }

    if (emptyStateEl?.parentElement === grid) {
        emptyStateEl.remove();
    }

    list.forEach((inst) => {
        const changed = updateHistory(state, inst, now);
        if (changed) {
            state.sparklineDirtyIds.add(inst.id);
        }
        let refs = instanceDomRegistry.get(inst.id);
        if (!refs) {
            refs = renderInstanceCard(state, inst);
            instanceDomRegistry.set(inst.id, refs);
        }
        updateInstanceCard(refs, inst, state);
    });

    const orderedIds = list.map((inst) => inst.id);
    const currentIds = Array.from(grid.querySelectorAll<HTMLElement>('.inst-card[data-inst-id]')).map(
        (card) => card.dataset.instId || '',
    );
    const orderMatches =
        orderedIds.length === currentIds.length && orderedIds.every((id, idx) => id === currentIds[idx]);

    const addCard = ensureAddCard(document);
    const addCardAtEnd = grid.lastElementChild === addCard;
    if (!orderMatches || !grid.contains(addCard) || !addCardAtEnd) {
        orderedIds.forEach((id) => {
            const refs = instanceDomRegistry.get(id);
            if (refs) grid.appendChild(refs.card);
        });
        grid.appendChild(addCard);
    }

    bindGridInteractions(grid, state, callbacks);
    applyActiveHighlights(state);
    processHighlightQueue(state);
    const dirtyIds = Array.from(state.sparklineDirtyIds);
    state.sparklineDirtyIds.clear();
    callbacks.requestSparklineDraw?.(dirtyIds);
}

export function computeHealthCheckTargets(
    state: InstancesDashboardState,
    instances: readonly InstanceRecord[],
    forceHealth: boolean,
): string[] {
    const now = Date.now();
    const targets: string[] = [];
    for (const inst of instances) {
        const id = inst.id;
        if (!id) continue;
        if (forceHealth) {
            targets.push(id);
            continue;
        }
        const metrics = inst.metrics || {};
        const metricsTsMs = typeof metrics.timestamp === 'number' ? metrics.timestamp * 1000 : null;
        const reachable = metrics.reachable !== false;
        const lastCheck = state.healthCheckTs[id] ?? 0;
        const metricsStale = !metricsTsMs || now - metricsTsMs > HEALTH_CHECK_MAX_AGE_MS;
        const due = now - lastCheck > HEALTH_CHECK_MIN_INTERVAL_MS;
        if (!reachable || metricsStale) {
            if (due) targets.push(id);
        }
    }
    return targets;
}

export function renderError(error: unknown): void {
    const stats = document.getElementById('instStats');
    if (!stats) {
        const message = error instanceof Error ? error.message : String(error);
        console.warn('Instances dashboard error before instStats is ready:', message);
        return;
    }
    const message = error instanceof Error ? error.message : String(error);
    stats.textContent = `Failed to load instances: ${message}`;
}
