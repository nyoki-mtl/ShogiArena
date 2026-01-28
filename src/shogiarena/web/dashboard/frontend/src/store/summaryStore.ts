/**
 * Unified Summary Store Implementation
 *
 * This store provides a single source of truth for all engine summary data,
 * eliminating the issues caused by ARENA_SUMMARY being overwritten by different tabs.
 *
 * Data flow:
 * 1. Tournament/SPSA tabs apply their raw data via applyTournamentSummary/applySpsaSummary
 * 2. Runtime USI options are applied separately via applyRuntimeOptions
 * 3. The store normalizes and merges data with priority: Runtime > Active Source > Fallback
 * 4. UI components subscribe to changes and receive merged views
 *
 * Merge priority for engine meta:
 * - Runtime meta (runtime_usi_options, runtime_engine_info): Always from runtime data
 * - Option meta (merged_options, resolved_options, etc.): From active source, with runtime as supplement
 * - Base meta (name, path): From active source
 */

import type { JsonObject, MutableJsonObject } from '@/types/shared';
import type {
    EngineViewModel,
    NormalizedEngineMeta,
    RawSpsaData,
    RawTournamentData,
    RuntimeUsiOptionEntry,
    StoreEvent,
    StoreEventCallback,
    StoreEventType,
    Subscription,
    SummarySource,
    SummaryStore,
    SummaryStoreState,
    SummaryViewModel,
} from './types';

// ============================================================================
// Internal State
// ============================================================================

let storeState: SummaryStoreState = {
    activeSource: null,
    tournament: null,
    spsa: null,
    runtime: {
        usiOptions: {},
        engineInfo: {},
    },
    lastUpdated: 0,
};

/** Subscriptions by event type */
const subscriptions = new Map<StoreEventType, Set<StoreEventCallback>>();

// ============================================================================
// Normalization Helpers
// ============================================================================

function coerceString(value: unknown): string | null {
    if (typeof value === 'string') {
        const trimmed = value.trim();
        return trimmed.length > 0 ? trimmed : null;
    }
    return null;
}

function coerceObject(value: unknown): MutableJsonObject {
    if (value && typeof value === 'object' && !Array.isArray(value)) {
        return value as MutableJsonObject;
    }
    return {};
}

function normalizeStringRecord(value: unknown): Record<string, string> {
    if (!value || typeof value !== 'object' || Array.isArray(value)) {
        return {};
    }
    const record: Record<string, string> = {};
    for (const [key, raw] of Object.entries(value as JsonObject)) {
        if (!key) continue;
        const normalized = raw == null ? null : String(raw);
        if (normalized != null) {
            record[String(key)] = normalized;
        }
    }
    return record;
}

function normalizeInstanceRecord(value: unknown): Record<string, string | null> {
    if (!value || typeof value !== 'object' || Array.isArray(value)) {
        return {};
    }
    const record: Record<string, string | null> = {};
    for (const [key, raw] of Object.entries(value as JsonObject)) {
        if (!key) continue;
        if (raw == null) {
            record[String(key)] = null;
        } else {
            record[String(key)] = String(raw);
        }
    }
    return record;
}

function normalizeEngineMetaRecord(value: unknown): Record<string, JsonObject> {
    const result: Record<string, JsonObject> = {};

    // Handle object format (engineMeta: { engineA: {...}, engineB: {...} })
    if (value && typeof value === 'object' && !Array.isArray(value)) {
        for (const [key, entry] of Object.entries(value as JsonObject)) {
            if (!key || !entry || typeof entry !== 'object') continue;
            result[String(key)] = entry as JsonObject;
        }
        return result;
    }

    return result;
}

function normalizeEnginesMetaArray(value: unknown): Record<string, JsonObject> {
    const result: Record<string, JsonObject> = {};

    // Handle array format (enginesMeta: [{ name: 'engineA', ... }, ...])
    if (Array.isArray(value)) {
        for (const entry of value) {
            if (!entry || typeof entry !== 'object') continue;
            const entryObj = entry as JsonObject;
            const name = coerceString(entryObj.name);
            if (name) {
                result[name] = entryObj;
            }
        }
    }

    return result;
}

function normalizeRuntimeUsiOptions(value: unknown): Record<string, RuntimeUsiOptionEntry> {
    if (!value || typeof value !== 'object' || Array.isArray(value)) {
        return {};
    }
    const result: Record<string, RuntimeUsiOptionEntry> = {};
    for (const [key, entry] of Object.entries(value as JsonObject)) {
        if (!key || !entry || typeof entry !== 'object' || Array.isArray(entry)) continue;
        const entryObj = entry as MutableJsonObject;
        const normalized: RuntimeUsiOptionEntry = {};
        if (Object.hasOwn(entryObj, 'current')) {
            normalized.current = entryObj.current;
        }
        if (Object.hasOwn(entryObj, 'default')) {
            normalized.default = (entryObj as { default?: unknown }).default;
        }
        result[key] = normalized;
    }
    return result;
}

// ============================================================================
// Data Extraction from Raw Summary
// ============================================================================

function extractEngines(raw: JsonObject): string[] {
    const names = new Set<string>();

    // From engines array
    if (Array.isArray(raw.engines)) {
        for (const name of raw.engines) {
            const normalized = coerceString(name);
            if (normalized) names.add(normalized);
        }
    }

    // From engineStats keys
    if (raw.engineStats && typeof raw.engineStats === 'object') {
        for (const name of Object.keys(raw.engineStats as JsonObject)) {
            const normalized = coerceString(name);
            if (normalized) names.add(normalized);
        }
    }

    // From enginesMeta array
    if (Array.isArray(raw.enginesMeta)) {
        for (const entry of raw.enginesMeta) {
            if (entry && typeof entry === 'object') {
                const name = coerceString((entry as JsonObject).name);
                if (name) names.add(name);
            }
        }
    }

    // From engineMeta object keys
    if (raw.engineMeta && typeof raw.engineMeta === 'object' && !Array.isArray(raw.engineMeta)) {
        for (const name of Object.keys(raw.engineMeta as JsonObject)) {
            const normalized = coerceString(name);
            if (normalized) names.add(normalized);
        }
    }

    return Array.from(names).sort((a, b) => a.localeCompare(b));
}

function extractEngineMeta(raw: JsonObject): Record<string, JsonObject> {
    // Merge both engineMeta (object) and enginesMeta (array) formats
    const fromObject = normalizeEngineMetaRecord(raw.engineMeta);
    const fromArray = normalizeEnginesMetaArray(raw.enginesMeta);

    // Array format takes precedence (more recent format)
    return { ...fromObject, ...fromArray };
}

// ============================================================================
// Apply Updates
// ============================================================================

function parseTournamentSummary(raw: JsonObject): RawTournamentData {
    return {
        engines: extractEngines(raw),
        engineMeta: extractEngineMeta(raw),
        engineTimeControls: normalizeStringRecord(raw.engineTimeControls),
        engineInstances: normalizeInstanceRecord(raw.engineInstances),
        engineStats: coerceObject(raw.engineStats) as Record<string, JsonObject>,
        defaultTimeControl: coerceString(raw.defaultTimeControl),
        raw,
    };
}

function parseSpsaSummary(raw: JsonObject): RawSpsaData {
    return {
        engines: extractEngines(raw),
        engineMeta: extractEngineMeta(raw),
        engineTimeControls: normalizeStringRecord(raw.engineTimeControls),
        engineInstances: normalizeInstanceRecord(raw.engineInstances),
        engineStats: coerceObject(raw.engineStats) as Record<string, JsonObject>,
        defaultTimeControl: coerceString(raw.defaultTimeControl),
        raw,
    };
}

function emitEvent(event: StoreEvent): void {
    const allCallbacks = subscriptions.get('all');
    const typeCallbacks = subscriptions.get(event.type);

    if (allCallbacks) {
        for (const cb of allCallbacks) {
            try {
                cb(event);
            } catch (error) {
                console.error('[SummaryStore] Error in subscription callback:', error);
            }
        }
    }

    if (typeCallbacks && event.type !== 'all') {
        for (const cb of typeCallbacks) {
            try {
                cb(event);
            } catch (error) {
                console.error('[SummaryStore] Error in subscription callback:', error);
            }
        }
    }
}

function detectChangedEngines(
    oldData: RawTournamentData | RawSpsaData | null,
    newData: RawTournamentData | RawSpsaData,
): string[] {
    if (!oldData) {
        return newData.engines;
    }

    const changed = new Set<string>();

    // New engines
    for (const name of newData.engines) {
        if (!oldData.engines.includes(name)) {
            changed.add(name);
        }
    }

    // Removed engines
    for (const name of oldData.engines) {
        if (!newData.engines.includes(name)) {
            changed.add(name);
        }
    }

    // Changed meta
    for (const name of newData.engines) {
        const oldMeta = oldData.engineMeta[name];
        const newMeta = newData.engineMeta[name];
        if (JSON.stringify(oldMeta) !== JSON.stringify(newMeta)) {
            changed.add(name);
        }
    }

    // Changed time controls
    for (const name of newData.engines) {
        const oldTc = oldData.engineTimeControls[name];
        const newTc = newData.engineTimeControls[name];
        if (oldTc !== newTc) {
            changed.add(name);
        }
    }

    // Changed instances
    for (const name of newData.engines) {
        const oldInstance = oldData.engineInstances[name];
        const newInstance = newData.engineInstances[name];
        if (oldInstance !== newInstance) {
            changed.add(name);
        }
    }

    // Changed defaultTimeControl affects all engines that don't have explicit time controls
    if (oldData.defaultTimeControl !== newData.defaultTimeControl) {
        for (const name of newData.engines) {
            // Mark engine as changed if it relies on defaultTimeControl
            if (!newData.engineTimeControls[name]) {
                changed.add(name);
            }
        }
    }

    return Array.from(changed);
}

// ============================================================================
// Merge Logic
// ============================================================================

interface ActiveDataResult {
    data: RawTournamentData | RawSpsaData;
    /** The actual source of the data (may differ from activeSource during fallback) */
    actualSource: 'tournament' | 'spsa';
}

function getActiveData(): ActiveDataResult | null {
    // Try to return data from the active source first
    if (storeState.activeSource === 'tournament' && storeState.tournament) {
        return { data: storeState.tournament, actualSource: 'tournament' };
    }
    if (storeState.activeSource === 'spsa' && storeState.spsa) {
        return { data: storeState.spsa, actualSource: 'spsa' };
    }
    // Fallback: return whichever is available (handles case where active source
    // is set but data hasn't loaded yet, e.g., switching to SPSA tab before
    // SPSA data arrives)
    if (storeState.tournament) {
        return { data: storeState.tournament, actualSource: 'tournament' };
    }
    if (storeState.spsa) {
        return { data: storeState.spsa, actualSource: 'spsa' };
    }
    return null;
}

function mergeEngineMeta(engineName: string): NormalizedEngineMeta | undefined {
    const result = getActiveData();
    if (!result) {
        return undefined;
    }

    const { data: activeData, actualSource } = result;

    // Check if engine exists in active data
    if (!activeData.engines.includes(engineName)) {
        return undefined;
    }

    const now = Date.now();

    // Get base and option meta from actual source
    const sourceMeta = activeData.engineMeta[engineName] ?? {};

    // Get fallback source for option meta supplementation
    const fallbackSource: 'tournament' | 'spsa' = actualSource === 'tournament' ? 'spsa' : 'tournament';
    const fallbackData = actualSource === 'tournament' ? storeState.spsa : storeState.tournament;
    const fallbackMeta = fallbackData?.engineMeta[engineName] ?? {};

    // Get runtime data from store (live USI communication)
    const runtimeUsi = storeState.runtime.usiOptions[engineName] ?? {};
    const runtimeInfo = storeState.runtime.engineInfo[engineName] ?? null;

    // Extract base meta
    const name = coerceString(sourceMeta.name) ?? engineName;
    // Support both snake_case (raw API format) and camelCase (normalized format)
    const enginePath = coerceString(sourceMeta.engine_path) ?? coerceString(sourceMeta.enginePath);

    // Extract option meta from active source
    const sourceMergedOptions = coerceObject(sourceMeta.merged_options);
    const sourceResolvedOptions = coerceObject(sourceMeta.resolved_options);
    const sourceOptionSources = normalizeStringRecord(sourceMeta.option_sources);
    const sourceOptionSourcesDetails = normalizeStringRecord(sourceMeta.option_sources_details);
    const sourceRuntimeUsi = normalizeRuntimeUsiOptions(sourceMeta.runtime_usi_options);
    const sourceRuntimeInfo = sourceMeta.runtime_engine_info ? coerceObject(sourceMeta.runtime_engine_info) : null;

    // Extract option meta from fallback source (for supplementation)
    const fallbackRuntimeUsi = normalizeRuntimeUsiOptions(fallbackMeta.runtime_usi_options);

    // Option meta fallback: if active source has no option data, use fallback source
    // This ensures options don't disappear when switching tabs (e.g., Engines → SPSA → Engines)
    const hasActiveOptions = Object.keys(sourceMergedOptions).length > 0;

    let mergedOptions: MutableJsonObject;
    let resolvedOptions: MutableJsonObject;
    let optionSources: Record<string, string>;
    let optionSourcesDetails: Record<string, string>;
    let optionSource: 'tournament' | 'spsa';

    if (hasActiveOptions) {
        // Use active source's option meta
        mergedOptions = sourceMergedOptions;
        resolvedOptions = sourceResolvedOptions;
        optionSources = sourceOptionSources;
        optionSourcesDetails = sourceOptionSourcesDetails;
        optionSource = actualSource;
    } else {
        // Fallback to other source's option meta
        mergedOptions = coerceObject(fallbackMeta.merged_options);
        resolvedOptions = coerceObject(fallbackMeta.resolved_options);
        optionSources = normalizeStringRecord(fallbackMeta.option_sources);
        optionSourcesDetails = normalizeStringRecord(fallbackMeta.option_sources_details);
        optionSource = Object.keys(mergedOptions).length > 0 ? fallbackSource : actualSource;
    }

    // Runtime USI options fallback: if active source has no runtime USI data, use fallback source
    // This ensures Default column values don't disappear when SPSA is active
    const baseRuntimeUsi = Object.keys(sourceRuntimeUsi).length > 0 ? sourceRuntimeUsi : fallbackRuntimeUsi;

    // Merge runtime USI options: live runtime data takes precedence over meta data
    const mergedRuntimeUsi: Record<string, RuntimeUsiOptionEntry> = { ...baseRuntimeUsi };
    for (const [optName, optEntry] of Object.entries(runtimeUsi)) {
        const existing = mergedRuntimeUsi[optName] ?? {};
        mergedRuntimeUsi[optName] = { ...existing, ...optEntry };
    }

    // Merge runtime engine info: runtime data takes precedence
    const mergedRuntimeInfo = runtimeInfo ?? sourceRuntimeInfo;

    return {
        name,
        enginePath,
        merged_options: mergedOptions,
        resolved_options: resolvedOptions,
        option_sources: optionSources,
        option_sources_details: optionSourcesDetails,
        runtime_usi_options: mergedRuntimeUsi,
        runtime_engine_info: mergedRuntimeInfo,
        baseSource: actualSource,
        optionSource,
        lastUpdated: now,
    };
}

// ============================================================================
// Store Implementation
// ============================================================================

function applyTournamentSummary(raw: JsonObject): boolean {
    const oldData = storeState.tournament;
    const newData = parseTournamentSummary(raw);

    storeState.tournament = newData;
    storeState.lastUpdated = Date.now();

    // Note: activeSource is managed by tab switching only (via setActiveSource)
    // getActiveData() handles fallback when activeSource is null

    const changedEngines = detectChangedEngines(oldData, newData);
    if (changedEngines.length > 0) {
        emitEvent({ type: 'engines', source: 'tournament', changedEngines });
        return true;
    }

    return false;
}

function applySpsaSummary(raw: JsonObject): boolean {
    const oldData = storeState.spsa;
    const newData = parseSpsaSummary(raw);

    storeState.spsa = newData;
    storeState.lastUpdated = Date.now();

    // Note: activeSource is managed by tab switching only (via setActiveSource)
    // getActiveData() handles fallback when activeSource is null

    const changedEngines = detectChangedEngines(oldData, newData);
    if (changedEngines.length > 0) {
        emitEvent({ type: 'engines', source: 'spsa', changedEngines });
        return true;
    }

    return false;
}

function applyRuntimeOptions(engineName: string, options: Record<string, RuntimeUsiOptionEntry>): boolean {
    const existing = storeState.runtime.usiOptions[engineName] ?? {};
    const merged = { ...existing, ...options };

    // Check if anything changed
    if (JSON.stringify(existing) === JSON.stringify(merged)) {
        return false;
    }

    storeState.runtime.usiOptions[engineName] = merged;
    storeState.lastUpdated = Date.now();

    emitEvent({ type: 'options', source: 'runtime', changedEngines: [engineName] });
    return true;
}

function applyRuntimeEngineInfo(engineName: string, info: MutableJsonObject | null): boolean {
    const existing = storeState.runtime.engineInfo[engineName];

    // Check if anything changed
    if (JSON.stringify(existing) === JSON.stringify(info)) {
        return false;
    }

    storeState.runtime.engineInfo[engineName] = info;
    storeState.lastUpdated = Date.now();

    emitEvent({ type: 'options', source: 'runtime', changedEngines: [engineName] });
    return true;
}

function setActiveSource(source: 'tournament' | 'spsa'): void {
    if (storeState.activeSource === source) {
        return;
    }

    storeState.activeSource = source;
    storeState.lastUpdated = Date.now();

    const result = getActiveData();
    const changedEngines = result?.data.engines ?? [];

    emitEvent({ type: 'all', source: result?.actualSource ?? source, changedEngines });
}

function getEngineMeta(engineName: string): NormalizedEngineMeta | undefined {
    return mergeEngineMeta(engineName);
}

function getTimeControl(engineName: string): string | undefined {
    const result = getActiveData();
    if (!result) return undefined;

    const specific = result.data.engineTimeControls[engineName];
    if (specific) return specific;

    return result.data.defaultTimeControl ?? undefined;
}

function getInstance(engineName: string): string | null | undefined {
    const result = getActiveData();
    if (!result) return undefined;

    return result.data.engineInstances[engineName];
}

function getDefaultTimeControl(): string | null {
    const result = getActiveData();
    return result?.data.defaultTimeControl ?? null;
}

function getEngineNames(): string[] {
    const result = getActiveData();
    return result?.data.engines ?? [];
}

function hasValidOptionsData(engineName: string): boolean {
    const meta = mergeEngineMeta(engineName);
    if (!meta?.merged_options || typeof meta.merged_options !== 'object') {
        return false;
    }
    return Object.keys(meta.merged_options).length > 0;
}

function getActiveSourceValue(): SummarySource | null {
    return storeState.activeSource;
}

function getViewModel(): SummaryViewModel {
    const result = getActiveData();
    const engines = result?.data.engines ?? [];
    const engineMap = new Map<string, EngineViewModel>();

    for (const name of engines) {
        const meta = mergeEngineMeta(name);
        if (!meta) continue;

        const timeControl = getTimeControl(name) ?? null;
        const instanceId = getInstance(name) ?? null;

        engineMap.set(name, {
            name: meta.name,
            enginePath: meta.enginePath,
            timeControl,
            instanceId,
            merged_options: meta.merged_options,
            resolved_options: meta.resolved_options,
            option_sources: meta.option_sources,
            option_sources_details: meta.option_sources_details,
            runtime_usi_options: meta.runtime_usi_options,
            runtime_engine_info: meta.runtime_engine_info,
            hasOptionsData: hasValidOptionsData(name),
        });
    }

    return {
        engines,
        engineMap,
        defaultTimeControl: result?.data.defaultTimeControl ?? null,
        activeSource: storeState.activeSource,
        actualSource: result?.actualSource ?? null,
    };
}

function subscribe(eventType: StoreEventType, callback: StoreEventCallback): Subscription {
    let callbacks = subscriptions.get(eventType);
    if (!callbacks) {
        callbacks = new Set();
        subscriptions.set(eventType, callbacks);
    }
    callbacks.add(callback);

    return {
        unsubscribe: () => {
            callbacks.delete(callback);
        },
    };
}

function getState(): SummaryStoreState {
    return { ...storeState };
}

// ============================================================================
// Exported Store Instance
// ============================================================================

export const summaryStore: SummaryStore = {
    applyTournamentSummary,
    applySpsaSummary,
    applyRuntimeOptions,
    applyRuntimeEngineInfo,
    setActiveSource,
    getEngineMeta,
    getTimeControl,
    getInstance,
    getDefaultTimeControl,
    getEngineNames,
    getViewModel,
    hasValidOptionsData,
    getActiveSource: getActiveSourceValue,
    subscribe,
    getState,
};

// ============================================================================
// Reset (for testing)
// ============================================================================

export function resetStore(): void {
    storeState = {
        activeSource: null,
        tournament: null,
        spsa: null,
        runtime: {
            usiOptions: {},
            engineInfo: {},
        },
        lastUpdated: 0,
    };
    subscriptions.clear();
}
