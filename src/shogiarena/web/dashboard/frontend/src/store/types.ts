/**
 * Unified SummaryStore Types
 *
 * This module defines types for the centralized store that manages all summary data
 * from multiple sources (Tournament, SPSA, Runtime USI options).
 *
 * Key concepts:
 * - Raw data: Original data as received from each source
 * - Normalized data: Processed data with consistent structure
 * - Merged view: Final composed view with priority-based merging
 */

import type { JsonObject, MutableJsonObject } from '@/types/shared';

// ============================================================================
// Source Types
// ============================================================================

/** Data source identifiers */
export type SummarySource = 'tournament' | 'spsa' | 'runtime';

// ============================================================================
// Base Meta Types (engine name, path, basic info)
// ============================================================================

export interface BaseEngineMeta {
    name: string;
    enginePath: string | null;
}

// ============================================================================
// Option Meta Types (merged_options, resolved_options, option_sources)
// ============================================================================

export interface EngineOptionMeta {
    merged_options: MutableJsonObject;
    resolved_options: MutableJsonObject;
    option_sources: Record<string, string>;
    option_sources_details: Record<string, string>;
}

// ============================================================================
// Runtime Meta Types (runtime_usi_options, runtime_engine_info)
// ============================================================================

export interface RuntimeUsiOptionEntry {
    current?: unknown;
    default?: unknown;
}

export interface RuntimeEngineMeta {
    runtime_usi_options: Record<string, RuntimeUsiOptionEntry>;
    runtime_engine_info: MutableJsonObject | null;
}

// ============================================================================
// Normalized Engine Meta (complete merged view)
// ============================================================================

export interface NormalizedEngineMeta extends BaseEngineMeta, EngineOptionMeta, RuntimeEngineMeta {
    /** Source that provided base meta */
    baseSource: SummarySource | null;
    /** Source that provided option meta */
    optionSource: SummarySource | null;
    /** Timestamp of last update */
    lastUpdated: number;
}

// ============================================================================
// Raw Data Containers (stored per source)
// ============================================================================

export interface RawTournamentData {
    engines: string[];
    engineMeta: Record<string, JsonObject>;
    engineTimeControls: Record<string, string>;
    engineInstances: Record<string, string | null>;
    engineStats: Record<string, JsonObject>;
    defaultTimeControl: string | null;
    raw: JsonObject;
}

export interface RawSpsaData {
    engines: string[];
    engineMeta: Record<string, JsonObject>;
    engineTimeControls: Record<string, string>;
    engineInstances: Record<string, string | null>;
    engineStats: Record<string, JsonObject>;
    defaultTimeControl: string | null;
    raw: JsonObject;
}

export interface RawRuntimeData {
    /** Runtime USI options by engine name */
    usiOptions: Record<string, Record<string, RuntimeUsiOptionEntry>>;
    /** Runtime engine info by engine name */
    engineInfo: Record<string, MutableJsonObject | null>;
}

// ============================================================================
// Store State
// ============================================================================

export interface SummaryStoreState {
    /** Current active source (determines which data takes precedence for non-runtime data) */
    activeSource: SummarySource | null;

    /** Raw data from tournament source */
    tournament: RawTournamentData | null;

    /** Raw data from SPSA source */
    spsa: RawSpsaData | null;

    /** Raw data from runtime (USI options collected during matches) */
    runtime: RawRuntimeData;

    /** Timestamp of last state change */
    lastUpdated: number;
}

// ============================================================================
// View Types (UI-ready computed views)
// ============================================================================

export interface EngineViewModel {
    name: string;
    enginePath: string | null;
    timeControl: string | null;
    instanceId: string | null;
    merged_options: MutableJsonObject;
    resolved_options: MutableJsonObject;
    option_sources: Record<string, string>;
    option_sources_details: Record<string, string>;
    runtime_usi_options: Record<string, RuntimeUsiOptionEntry>;
    runtime_engine_info: MutableJsonObject | null;
    /** Has valid merged_options data */
    hasOptionsData: boolean;
}

export interface SummaryViewModel {
    engines: string[];
    engineMap: Map<string, EngineViewModel>;
    defaultTimeControl: string | null;
    /** The source requested by the user (via tab switch) */
    activeSource: SummarySource | null;
    /** The actual source providing data (may differ from activeSource during fallback) */
    actualSource: 'tournament' | 'spsa' | null;
}

// ============================================================================
// Subscription Types
// ============================================================================

export type StoreEventType = 'engines' | 'timeControls' | 'instances' | 'options' | 'all';

export type StoreEventCallback = (event: StoreEvent) => void;

export interface StoreEvent {
    type: StoreEventType;
    source: SummarySource;
    changedEngines: string[];
}

export interface Subscription {
    unsubscribe: () => void;
}

// ============================================================================
// Store API
// ============================================================================

export interface SummaryStore {
    // ---- Apply Updates ----
    /** Apply tournament summary data */
    applyTournamentSummary(raw: JsonObject): boolean;

    /** Apply SPSA summary data */
    applySpsaSummary(raw: JsonObject): boolean;

    /** Apply runtime USI options for an engine */
    applyRuntimeOptions(engineName: string, options: Record<string, RuntimeUsiOptionEntry>): boolean;

    /** Apply runtime engine info for an engine */
    applyRuntimeEngineInfo(engineName: string, info: MutableJsonObject | null): boolean;

    /** Set the active source (tournament or spsa) */
    setActiveSource(source: 'tournament' | 'spsa'): void;

    // ---- Getters ----
    /** Get merged engine meta for a specific engine */
    getEngineMeta(engineName: string): NormalizedEngineMeta | undefined;

    /** Get time control for a specific engine */
    getTimeControl(engineName: string): string | undefined;

    /** Get instance ID for a specific engine */
    getInstance(engineName: string): string | null | undefined;

    /** Get default time control */
    getDefaultTimeControl(): string | null;

    /** Get sorted list of engine names */
    getEngineNames(): string[];

    /** Get complete view model for UI */
    getViewModel(): SummaryViewModel;

    /** Check if an engine has valid merged options data */
    hasValidOptionsData(engineName: string): boolean;

    /** Get the current active source */
    getActiveSource(): SummarySource | null;

    // ---- Subscriptions ----
    /** Subscribe to store changes */
    subscribe(eventType: StoreEventType, callback: StoreEventCallback): Subscription;

    // ---- Debug ----
    /** Get raw store state (for debugging) */
    getState(): SummaryStoreState;
}
