import type { DashboardEnginesApi } from '../types/public';
import {
    cacheDomRefs,
    clearRowRegistry,
    createDomRefs,
    detailRowRegistry,
    engineRowRegistry,
    type EngineRowDomRefs,
} from '../utils/dom';
import type { EnginesLocalState } from '../state';
import {
    createEnginesLocalState,
    clearSummarySubscription,
    isInitialized,
    markInitialized,
    resetExpanded,
    setActive as setStateActive,
    setSummaryUnsubscribe,
    toggleExpanded,
} from '../state';
import { normalizeInstanceLabel } from './summary';
import { createEngineSummaryStore, getUnifiedStore, type EngineSummaryStore } from './store';
import type { StoreEvent } from '@/store/types';
import type {
    EngineDetailContext,
    EngineDetailMode,
    EngineInstanceDetail,
    EngineMetaDetail,
    EnginesWindow,
} from '../types/internal';
import type { OptionsRenderDeps, TimeControlRenderDeps } from '../components/view';
import type { NormalizedTournamentEngineMeta } from '@/modules/tournament/types';
import type { JsonObject } from '@/types/shared';
import { escapeHtml } from '@/modules/shared/utils/html';
import { formatDuration, formatNodesCountShort, parseTimeControlSpec } from '@/modules/shared/utils/timeControl';
import {
    renderInstanceDetail,
    renderInstanceInfo,
    renderOptionsDetail,
    renderTimeControlDetail,
} from '../components/view';
import { requestJson } from '@/modules/shared/services/api';
import { renderFullOptionsTable as renderFullOptionsTableMarkup } from '@/modules/tournament/components/standings';
import { formatOptionValue } from '@/modules/tournament/utils/formatters';
import { reportDashboardRecoverableFailure } from '@/modules/shared/utils/errors';
import type { RequestError } from '@/types/shared';

const defaultWindow = window as EnginesWindow;

let installedEnginesModule = false;

export function installEnginesModule(owner: EnginesWindow = defaultWindow): DashboardEnginesApi {
    if (installedEnginesModule) {
        return owner.DashboardEngines as DashboardEnginesApi;
    }

    const arenaWindow = owner;

    const core = arenaWindow.DashboardCore;
    if (!core) {
        throw new Error('DashboardCore must be loaded before engines module');
    }

    const { warnSoftFailure, showNotice, getApiBase } = core;

    if (!arenaWindow.DashboardEngines) {
        arenaWindow.DashboardEngines = {} as DashboardEnginesApi;
    }
    const Dashboard = arenaWindow.DashboardEngines as DashboardEnginesApi;

    const localState: EnginesLocalState = createEnginesLocalState();
    const dom = createDomRefs();

    const instanceCache = new Map<string, EngineInstanceDetail>();
    const fullOptionsCache = new Map<string, unknown>();

    // Centralized data store for consistent, always-fresh data access
    const summaryStore: EngineSummaryStore = createEngineSummaryStore(arenaWindow);

    const formatTimeControlShortLocal = (spec: string): string => {
        try {
            const tc = parseTimeControlSpec(spec);
            const parts: string[] = [];
            if (tc.mode === 'fixed' && tc.fixedMs != null && tc.fixedMs > 0) {
                parts.push(`Fixed ${formatDuration(tc.fixedMs)}`);
            } else {
                const timeBits: string[] = [];
                if (tc.initial && tc.initial > 0) timeBits.push(formatDuration(tc.initial));
                if (tc.increment > 0) timeBits.push(`+Inc ${formatDuration(tc.increment)}`);
                if (tc.byoyomi > 0) timeBits.push(`+Byo ${formatDuration(tc.byoyomi)}`);
                if (timeBits.length) parts.push(timeBits.join(' '));
            }
            if (tc.depth != null) parts.push(`Depth ${tc.depth}`);
            if (tc.nodes != null) parts.push(`Nodes ${formatNodesCountShort(tc.nodes)}`);
            if (tc.allowTimeout) parts.push('Allow Timeout');
            return parts.length ? parts.join(' · ') : '-';
        } catch {
            return spec;
        }
    };

    function buildRowSignature(
        name: string,
        displaySpec: string,
        instanceId: string,
        hasInstanceDetail: boolean,
        rawSpec: string,
    ): string {
        return `${name}|${displaySpec}|${instanceId}|${hasInstanceDetail ? 1 : 0}|${rawSpec}`;
    }

    function createEngineRow(
        doc: Document,
        name: string,
        displaySpec: string,
        instanceId: string,
        hasInstanceDetail: boolean,
        rawSpec: string,
    ): EngineRowDomRefs {
        const row = doc.createElement('tr');
        row.dataset.engine = name;
        row.className = 'engines-row standings-row';
        row.setAttribute('aria-expanded', 'false');

        const nameCell = doc.createElement('td');
        nameCell.className = 'engine-name-cell table-group-engine';
        nameCell.dataset.action = 'options';
        nameCell.dataset.engine = name;
        nameCell.tabIndex = 0;
        nameCell.setAttribute('role', 'button');
        nameCell.textContent = name;
        row.appendChild(nameCell);

        const instanceCell = doc.createElement('td');
        instanceCell.className = hasInstanceDetail
            ? 'engine-instance-cell is-clickable table-group-instance'
            : 'engine-instance-cell table-group-instance';
        if (hasInstanceDetail) {
            instanceCell.dataset.action = 'instance';
            instanceCell.dataset.engine = name;
            instanceCell.dataset.instance = instanceId;
            instanceCell.tabIndex = 0;
            instanceCell.setAttribute('role', 'button');
        }
        instanceCell.textContent = instanceId;
        row.appendChild(instanceCell);

        const timeCell = doc.createElement('td');
        timeCell.className = 'engine-time-cell table-group-time';
        timeCell.dataset.action = 'time';
        timeCell.dataset.engine = name;
        timeCell.dataset.spec = rawSpec || '';
        timeCell.tabIndex = 0;
        timeCell.setAttribute('role', 'button');
        timeCell.textContent = displaySpec;
        row.appendChild(timeCell);

        row.dataset.rowSig = buildRowSignature(name, displaySpec, instanceId, hasInstanceDetail, rawSpec);

        return { row, nameCell, instanceCell, timeCell };
    }

    function updateEngineRow(
        refs: EngineRowDomRefs,
        name: string,
        displaySpec: string,
        instanceId: string,
        hasInstanceDetail: boolean,
        rawSpec: string,
        isExpanded: boolean,
    ): boolean {
        const newSig = buildRowSignature(name, displaySpec, instanceId, hasInstanceDetail, rawSpec);
        const sigChanged = refs.row.dataset.rowSig !== newSig;

        if (sigChanged) {
            refs.row.dataset.rowSig = newSig;

            refs.nameCell.textContent = name;

            refs.instanceCell.className = hasInstanceDetail
                ? 'engine-instance-cell is-clickable table-group-instance'
                : 'engine-instance-cell table-group-instance';
            if (hasInstanceDetail) {
                refs.instanceCell.dataset.action = 'instance';
                refs.instanceCell.dataset.engine = name;
                refs.instanceCell.dataset.instance = instanceId;
                refs.instanceCell.tabIndex = 0;
                refs.instanceCell.setAttribute('role', 'button');
            } else {
                delete refs.instanceCell.dataset.action;
                delete refs.instanceCell.dataset.instance;
                refs.instanceCell.removeAttribute('tabindex');
                refs.instanceCell.removeAttribute('role');
            }
            refs.instanceCell.textContent = instanceId;

            refs.timeCell.dataset.spec = rawSpec || '';
            refs.timeCell.textContent = displaySpec;
        }

        const wasExpanded = refs.row.classList.contains('selected');
        if (wasExpanded !== isExpanded) {
            refs.row.classList.toggle('selected', isExpanded);
            refs.row.setAttribute('aria-expanded', isExpanded ? 'true' : 'false');
        }

        return sigChanged;
    }

    function reportRecoverable(scope: string, error: unknown, userMessage?: string): void {
        reportDashboardRecoverableFailure(error, { scope, userMessage });
    }

    function subscribeToSummary(): void {
        if (localState.summaryUnsubscribe) {
            return;
        }
        // Subscribe directly to the unified store instead of summary:update events.
        // This ensures we receive updates AFTER the store has been updated,
        // avoiding race conditions with Tournament/SPSA event handlers.
        const unifiedStore = getUnifiedStore();
        const subscription = unifiedStore.subscribe('all', handleStoreUpdate);
        setSummaryUnsubscribe(localState, subscription.unsubscribe);
    }

    function cacheElements(): void {
        cacheDomRefs(dom, arenaWindow);
    }

    /**
     * Get fresh summary maps through the store.
     * Always retrieves current state to avoid stale data issues.
     */
    function summaryMaps() {
        return summaryStore.getMaps();
    }

    /**
     * Get sorted list of engine names through the store.
     */
    function collectEngineNamesFromSummary(): string[] {
        return summaryStore.getEngineNames();
    }

    function renderEngineList() {
        if (!dom.tableBody) {
            throw new Error('Engines table body is not available');
        }
        const doc = arenaWindow.document;
        const maps = summaryMaps();
        const { tcMap, instanceMap, defaultTime } = maps;
        const names = collectEngineNamesFromSummary();

        if (names.length === 0) {
            engineRowRegistry.forEach((refs) => {
                refs.row.remove();
            });
            engineRowRegistry.clear();
            detailRowRegistry.forEach((row) => {
                row.remove();
            });
            detailRowRegistry.clear();
            if (!dom.tableBody.querySelector('tr[data-empty]')) {
                const emptyRow = doc.createElement('tr');
                emptyRow.dataset.empty = '1';
                const emptyCell = doc.createElement('td');
                emptyCell.colSpan = 3;
                emptyCell.className = 'muted';
                emptyCell.textContent = 'No engines detected.';
                emptyRow.appendChild(emptyCell);
                dom.tableBody.replaceChildren(emptyRow);
            }
            if (dom.summaryLabel) {
                dom.summaryLabel.textContent = '';
                dom.summaryLabel.hidden = true;
            }
            return;
        }

        const emptyRow = dom.tableBody.querySelector('tr[data-empty]');
        if (emptyRow) emptyRow.remove();

        if (dom.summaryLabel) {
            dom.summaryLabel.textContent = '';
            dom.summaryLabel.hidden = true;
        }

        if (localState.expanded && !names.includes(localState.expanded.engine)) {
            localState.expanded = null;
        }

        const formatTimeShort = formatTimeControlShortLocal;
        const validNames = new Set(names);

        engineRowRegistry.forEach((refs, name) => {
            if (!validNames.has(name)) {
                refs.row.remove();
                engineRowRegistry.delete(name);
                const detailRow = detailRowRegistry.get(name);
                if (detailRow) {
                    detailRow.remove();
                    detailRowRegistry.delete(name);
                }
            }
        });

        const fragment = doc.createDocumentFragment();
        let needsReorder = false;

        for (const name of names) {
            const rawSpec = tcMap.get(name) || defaultTime || '';
            const displaySpec = rawSpec?.trim() ? formatTimeShort(rawSpec) : '-';
            const instanceId = normalizeInstanceLabel(instanceMap.get(name));
            const rowExpanded = Boolean(localState.expanded && localState.expanded.engine === name);
            const context = rowExpanded ? (localState.expanded as EngineDetailContext) : null;
            const rowMode = context?.mode ?? null;
            const hasInstanceDetail = Boolean(instanceId && instanceId !== 'local');

            let refs = engineRowRegistry.get(name);
            if (!refs) {
                refs = createEngineRow(doc, name, displaySpec, instanceId, hasInstanceDetail, rawSpec);
                engineRowRegistry.set(name, refs);
                needsReorder = true;
            } else {
                updateEngineRow(refs, name, displaySpec, instanceId, hasInstanceDetail, rawSpec, rowExpanded);
            }

            refs.row.classList.toggle('selected', rowExpanded);
            refs.row.setAttribute('aria-expanded', rowExpanded ? 'true' : 'false');

            let detailRow = detailRowRegistry.get(name);
            if (rowExpanded && rowMode && context) {
                const effectiveSpec =
                    context.tcSpec ?? (rawSpec?.trim() ? rawSpec : (tcMap.get(name) ?? defaultTime ?? undefined));

                // Include options data presence in signature for 'options' mode
                // This ensures re-render when merged_options availability changes
                const optionsDataKey =
                    rowMode === 'options' ? (summaryStore.hasValidOptionsData(name) ? '1' : '0') : '';
                const detailSig = `${name}|${rowMode}|${context.instanceId ?? instanceId}|${effectiveSpec ?? ''}|${optionsDataKey}`;
                const needsDetailUpdate = !detailRow || detailRow.dataset.detailSig !== detailSig;

                if (needsDetailUpdate) {
                    const detailHtml = renderDetailContent({
                        engine: name,
                        mode: rowMode,
                        instanceId: context.instanceId ?? instanceMap.get(name) ?? null,
                        tcSpec: effectiveSpec,
                    });
                    if (detailHtml) {
                        if (!detailRow) {
                            detailRow = doc.createElement('tr');
                            detailRow.className = 'engines-detail-row';
                            detailRowRegistry.set(name, detailRow);
                            needsReorder = true;
                        }
                        detailRow.dataset.for = name;
                        detailRow.dataset.mode = rowMode;
                        detailRow.dataset.detailSig = detailSig;
                        if (rowMode === 'instance') {
                            detailRow.dataset.instance = context.instanceId ?? instanceId;
                        }
                        if (rowMode === 'time') {
                            detailRow.dataset.spec = context.tcSpec ?? (rawSpec?.trim() ? rawSpec : '');
                        }
                        const cell = doc.createElement('td');
                        cell.colSpan = 3;
                        cell.innerHTML = detailHtml;
                        detailRow.replaceChildren(cell);
                    } else {
                        localState.expanded = null;
                        if (detailRow) {
                            detailRow.remove();
                            detailRowRegistry.delete(name);
                        }
                    }
                }
            } else if (detailRow) {
                detailRow.remove();
                detailRowRegistry.delete(name);
            }
        }

        if (needsReorder) {
            for (const name of names) {
                const refs = engineRowRegistry.get(name);
                if (refs) {
                    fragment.appendChild(refs.row);
                    const detailRow = detailRowRegistry.get(name);
                    if (detailRow) {
                        fragment.appendChild(detailRow);
                    }
                }
            }
            dom.tableBody.replaceChildren(fragment);
        } else {
            const currentOrder: string[] = [];
            dom.tableBody.querySelectorAll<HTMLTableRowElement>('tr.engines-row[data-engine]').forEach((row) => {
                if (row.dataset.engine) currentOrder.push(row.dataset.engine);
            });
            const orderMatches = currentOrder.length === names.length && currentOrder.every((n, i) => n === names[i]);
            if (!orderMatches) {
                for (const name of names) {
                    const refs = engineRowRegistry.get(name);
                    if (refs) {
                        dom.tableBody.appendChild(refs.row);
                        const detailRow = detailRowRegistry.get(name);
                        if (detailRow) {
                            dom.tableBody.appendChild(detailRow);
                        }
                    }
                }
            }
        }

        if (localState.expanded) {
            const detailRow = detailRowRegistry.get(localState.expanded.engine);
            if (detailRow) {
                afterRenderDetail(detailRow, localState.expanded);
            } else {
                localState.expanded = null;
            }
        }
    }

    /**
     * Render detail content for an expanded engine row.
     *
     * IMPORTANT: For 'options' mode, we always fetch fresh meta data through the store
     * to avoid stale closure issues where merged_options might be missing or outdated.
     * This ensures USI options are always displayed correctly, even after summary updates.
     */
    function renderDetailContent(context: EngineDetailContext): string {
        const optionsDeps: OptionsRenderDeps = {};
        const timeDeps: TimeControlRenderDeps = { warnSoftFailure };

        switch (context.mode) {
            case 'options': {
                // Always get fresh meta through the store to avoid stale data issues.
                // This fixes the bug where "No USI options configured" was shown incorrectly
                // after summary updates, while "Show full USI options" worked correctly.
                const meta = summaryStore.getEngineMeta(context.engine);
                return renderOptionsDetail(context.engine, meta, optionsDeps);
            }
            case 'instance': {
                // Get fresh instance data for consistency
                const instanceKey = context.instanceId ?? summaryStore.getInstance(context.engine) ?? null;
                const labeled = normalizeInstanceLabel(instanceKey ?? '');
                if (!instanceKey || labeled === 'local') {
                    return '';
                }
                return renderInstanceDetail(context.engine, labeled);
            }
            case 'time': {
                // Get fresh time control data for consistency
                const spec = context.tcSpec ?? summaryStore.getTimeControl(context.engine);
                return renderTimeControlDetail(context.engine, spec, timeDeps);
            }
            default:
                return '';
        }
    }

    function afterRenderDetail(detailRow: Element | null, expanded: EngineDetailContext | null): void {
        if (!detailRow || !expanded) return;
        const rowElement = detailRow as HTMLElement;
        const { engine, mode } = expanded;
        if (mode === 'options') {
            setupOptionsDetail(rowElement, engine);
        } else if (mode === 'instance') {
            const instanceId = rowElement.dataset.instance || expanded.instanceId;
            if (instanceId) {
                void setupInstanceDetail(rowElement, instanceId);
            }
        }
    }

    function setupOptionsDetail(detailRow: Element, engineName: string): void {
        const container = (detailRow as HTMLElement).querySelector<HTMLElement>('.engine-options-container');
        if (!container) return;
        const button = container.querySelector<HTMLButtonElement>('.js-engines-full-options');
        const fullArea = container.querySelector<HTMLElement>('.engine-options-full');
        if (!button || !fullArea) return;

        const renderFullOptions = (element: Element, payload: unknown, meta: EngineMetaDetail | undefined): void => {
            const normalizedMeta = meta as NormalizedTournamentEngineMeta | undefined;
            element.innerHTML = renderFullOptionsTableMarkup({
                payload,
                meta: normalizedMeta,
                escapeHtml,
                formatOptionValue,
            });
        };

        /**
         * Load full options with unified caching.
         * Results are cached regardless of the data source (local API or DashboardTournament).
         */
        const loadFullOptionsWithCache = async (engine: string): Promise<unknown> => {
            if (fullOptionsCache.has(engine)) {
                return fullOptionsCache.get(engine);
            }

            const tournament = arenaWindow.DashboardTournament;
            const loader = tournament && typeof tournament.loadFullOptions === 'function' ? tournament : null;

            let data: unknown;
            if (loader) {
                // Use DashboardTournament loader but cache the result locally
                data = await loader.loadFullOptions(engine);
            } else {
                // Use local API
                const apiBase = getApiBase();
                try {
                    data = await requestJson<JsonObject>(`${apiBase}/api/engine_options/${encodeURIComponent(engine)}`);
                } catch (error) {
                    const requestError = error as RequestError | undefined;
                    if (requestError?.status === 404) {
                        throw new Error('Full USI options not available yet for this engine.');
                    }
                    const statusInfo = requestError?.status ? ` (${requestError.status})` : '';
                    throw new Error(`Failed to load full USI options${statusInfo}`, { cause: error });
                }
            }

            fullOptionsCache.set(engine, data);
            return data;
        };

        // Track if DOM has been rendered to avoid unnecessary re-renders
        let isRendered = false;

        button.addEventListener('click', async () => {
            const expanded = container.getAttribute('data-expanded') === '1';
            if (expanded) {
                // Hide: just toggle visibility, preserve DOM for instant re-show
                container.setAttribute('data-expanded', '0');
                button.textContent = 'Show full USI options';
                fullArea.classList.add('hidden');
                return;
            }

            // Show: check if we can reuse existing DOM
            container.setAttribute('data-expanded', '1');

            // If already rendered and cached, just show immediately
            if (isRendered && fullOptionsCache.has(engineName)) {
                fullArea.classList.remove('hidden');
                button.textContent = 'Hide full USI options';
                return;
            }

            // Need to load/render - update UI immediately via requestAnimationFrame
            button.disabled = true;
            await new Promise<void>((resolve) => {
                requestAnimationFrame(() => {
                    button.textContent = 'Loading…';
                    resolve();
                });
            });

            try {
                const payload = await loadFullOptionsWithCache(engineName);
                // Use store for consistent, fresh data access
                const meta = summaryStore.getEngineMeta(engineName) || {};
                const tournament = arenaWindow.DashboardTournament;
                if (tournament && typeof tournament.renderFullOptionsTable === 'function') {
                    tournament.renderFullOptionsTable(fullArea, payload, meta);
                } else {
                    renderFullOptions(fullArea, payload, meta);
                }
                isRendered = true;
                fullArea.classList.remove('hidden');
                button.textContent = 'Hide full USI options';
            } catch (error) {
                container.setAttribute('data-expanded', '0');
                fullArea.classList.add('hidden');
                fullArea.innerHTML = '';
                isRendered = false;
                button.textContent = 'Show full USI options';
                reportRecoverable('Engines.FullOptions.Fetch', error, 'Failed to load full USI options');
                if (typeof showNotice === 'function') {
                    showNotice(error.message || 'Failed to load full USI options', 'error');
                }
            } finally {
                button.disabled = false;
            }
        });
    }

    async function setupInstanceDetail(detailRow: Element, instanceId: string): Promise<void> {
        const body = detailRow.querySelector<HTMLElement>('.engine-instance-body');
        if (!body) return;
        try {
            const data = await fetchInstanceDetail(instanceId);
            body.setAttribute('data-state', 'loaded');
            body.innerHTML = renderInstanceInfo(data);
        } catch (error) {
            body.setAttribute('data-state', 'error');
            const message = error instanceof Error ? error.message : 'Failed to load instance details';
            body.innerHTML = `<div class="subtle">${escapeHtml(message)}</div>`;
            reportRecoverable('Engines.Instance.Detail', error, message);
        }
    }

    async function fetchInstanceDetail(instanceId: string): Promise<EngineInstanceDetail> {
        if (instanceCache.has(instanceId)) {
            return instanceCache.get(instanceId) as EngineInstanceDetail;
        }
        const apiBase = getApiBase();
        try {
            const data = await requestJson<EngineInstanceDetail>(
                `${apiBase}/api/instances/${encodeURIComponent(instanceId)}`,
            );
            instanceCache.set(instanceId, data);
            return data;
        } catch (error) {
            const requestError = error as RequestError | undefined;
            if (requestError?.status === 404) {
                throw new Error('Instance not found. It may have been removed.');
            }
            const statusInfo = requestError?.status ? ` (${requestError.status})` : '';
            throw new Error(`Failed to fetch instance${statusInfo}`, { cause: error });
        }
    }

    function setExpanded(
        engine: string | null,
        mode: EngineDetailMode | null,
        context: Partial<EngineDetailContext> = {},
    ): void {
        toggleExpanded(localState, engine, mode, context);
        renderEngineList();
    }

    function toggleOptions(engine: string | null): void {
        if (!engine) return;
        setExpanded(engine, 'options');
    }

    function toggleInstance(engine: string | null, instanceId: string | null): void {
        if (!engine || !instanceId || instanceId === 'local') return;
        setExpanded(engine, 'instance', { instanceId });
    }

    function toggleTimeControl(engine: string | null, spec: string | null): void {
        if (!engine) return;
        const normalizedSpec = spec?.trim() ? spec : undefined;
        setExpanded(engine, 'time', { tcSpec: normalizedSpec });
    }

    function handleTableInteraction(event: MouseEvent): void {
        const target = event.target as HTMLElement | null;
        const cell = target?.closest<HTMLTableCellElement>('td[data-action]');
        if (!cell) return;
        const engineName = cell.getAttribute('data-engine');
        const action = cell.getAttribute('data-action');
        if (!engineName || !action) return;
        event.preventDefault();
        if (action === 'options') {
            toggleOptions(engineName);
        } else if (action === 'instance') {
            const instanceId = cell.getAttribute('data-instance');
            toggleInstance(engineName, instanceId);
        } else if (action === 'time') {
            const spec = cell.getAttribute('data-spec') || '';
            toggleTimeControl(engineName, spec);
        }
    }

    function handleTableKeydown(event: KeyboardEvent): void {
        if (event.key !== 'Enter' && event.key !== ' ') return;
        const target = event.target as HTMLElement | null;
        const cell = target?.closest<HTMLTableCellElement>('td[data-action]');
        if (!cell) return;
        event.preventDefault();
        const engineName = cell.getAttribute('data-engine');
        const action = cell.getAttribute('data-action');
        if (!engineName || !action) return;
        if (action === 'options') {
            toggleOptions(engineName);
        } else if (action === 'instance') {
            const instanceId = cell.getAttribute('data-instance');
            toggleInstance(engineName, instanceId);
        } else if (action === 'time') {
            const spec = cell.getAttribute('data-spec') || '';
            toggleTimeControl(engineName, spec);
        }
    }

    function renderAll(): void {
        renderEngineList();
    }

    /**
     * Handle store update events from the unified SummaryStore.
     * This is called AFTER the store has been updated, ensuring we always
     * render with fresh data.
     */
    function handleStoreUpdate(event: StoreEvent): void {
        // Skip if module is not initialized or not active
        if (!isInitialized(localState) || !localState.active) return;

        try {
            // Clear caches based on event type to avoid unnecessary API re-fetches.
            // - 'engines' and 'all': engine list/meta changed, clear both caches
            // - 'options': only runtime USI options changed (stored in unified store),
            //   no need to clear API response caches
            if (event.type === 'engines' || event.type === 'all') {
                instanceCache.clear();
                fullOptionsCache.clear();
                renderAll();
                return;
            }

            // For 'options' events (runtime USI options), only re-render if one of
            // the changed engines has an expanded detail panel showing options.
            // This avoids unnecessary full re-renders when options update in background.
            if (event.type === 'options') {
                const expandedEngine = localState.expanded?.engine;
                const expandedMode = localState.expanded?.mode;
                // Only re-render if the expanded engine is in changedEngines AND showing options
                if (expandedEngine && expandedMode === 'options' && event.changedEngines.includes(expandedEngine)) {
                    renderAll();
                }
                return;
            }

            // For any other event types, re-render
            renderAll();
        } catch (error) {
            reportRecoverable('Engines.Summary.Refresh', error, 'Failed to refresh Engines tab after store update');
        }
    }

    function ensureInitialized(): boolean {
        if (isInitialized(localState)) {
            if (!localState.summaryUnsubscribe) {
                subscribeToSummary();
            }
            return true;
        }
        try {
            cacheElements();
        } catch (error) {
            warnSoftFailure('Failed to initialize Engines tab: missing DOM nodes', error);
        }
        const table = dom.table;
        if (table) {
            table.addEventListener('click', handleTableInteraction);
            table.addEventListener('keydown', handleTableKeydown);
        }
        subscribeToSummary();
        markInitialized(localState);
        renderAll();
        return true;
    }

    function focusEngineDetail(
        engineName: string,
        detail: EngineDetailMode,
        { scroll = true }: { scroll?: boolean } = {},
    ): boolean {
        const name = engineName.trim();
        if (!name) return false;
        ensureInitialized();

        const alreadyExpanded = Boolean(localState.expanded?.engine === name && localState.expanded?.mode === detail);
        if (!alreadyExpanded) {
            setExpanded(name, detail);
        } else {
            renderEngineList();
        }

        if (!dom.tableBody) {
            throw new Error('Engines table body is not available');
        }
        if (typeof CSS === 'undefined' || typeof CSS.escape !== 'function') {
            throw new Error('CSS.escape is not available in this environment');
        }

        const selector = `tr.engines-row[data-engine="${CSS.escape(name)}"]`;
        const row = dom.tableBody.querySelector<HTMLTableRowElement>(selector);
        if (!row) {
            return false;
        }

        if (scroll) {
            row.scrollIntoView({ behavior: 'smooth', block: 'center' });
        }
        return true;
    }

    Dashboard.setActive = function setActive(isActive: boolean): void {
        const active = Boolean(isActive);
        const wasActive = localState.active;
        setStateActive(localState, active);
        if (active) {
            if (!ensureInitialized()) {
                return;
            }
            // Always reset expanded state when tab becomes active for consistent UX.
            // This ensures users always see collapsed rows when returning to the tab.
            if (!wasActive) {
                resetExpanded(localState);
            }
            subscribeToSummary();
            if (!wasActive) {
                renderAll();
            }
            return;
        }
        if (wasActive) {
            clearSummarySubscription(localState);
            resetExpanded(localState);
            instanceCache.clear();
            clearRowRegistry();
        }
    };

    Dashboard.refresh = function refresh(): void {
        if (!ensureInitialized()) return;
        renderAll();
    };

    Dashboard.focusEngine = function focusEngine(engineName, options) {
        const detail = (options?.detail ?? 'options') as EngineDetailMode;
        return focusEngineDetail(engineName, detail, { scroll: options?.scroll });
    };

    ensureInitialized();

    installedEnginesModule = true;
    return Dashboard;
}
