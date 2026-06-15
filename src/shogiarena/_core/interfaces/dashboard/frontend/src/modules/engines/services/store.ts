/**
 * EngineSummaryStore - Centralized data access for engine summary information.
 *
 * This store provides a consistent, always-fresh data access pattern to avoid
 * stale closure issues that can occur when passing pre-computed data snapshots.
 *
 * Key design principles:
 * 1. Data is always accessed through getters, never passed as snapshots
 * 2. Each access retrieves the current state from the unified store
 * 3. The store handles intelligent merging of partial updates
 *
 * Architecture:
 * - This is a facade over the unified SummaryStore (/src/store/summaryStore.ts)
 * - The unified store is the single source of truth for all summary data
 */

import type { EngineMetaDetail, EngineSummaryMaps, EnginesSummary, EnginesWindow } from '../types/internal';
import { summaryStore } from '@/store';
import type { JsonObject } from '@/types/shared';

export interface EngineSummaryStore {
    /** Get summary maps with fresh data. Always retrieves current state. */
    getMaps(): EngineSummaryMaps;

    /** Get engine meta for a specific engine. Always retrieves current state. */
    getEngineMeta(engineName: string): EngineMetaDetail | undefined;

    /** Get time control spec for a specific engine. */
    getTimeControl(engineName: string): string | undefined;

    /** Get instance ID for a specific engine. */
    getInstance(engineName: string): string | null | undefined;

    /** Get default time control. */
    getDefaultTimeControl(): string | null;

    /** Get sorted list of engine names. */
    getEngineNames(): string[];

    /** Check if an engine has valid merged options data. */
    hasValidOptionsData(engineName: string): boolean;

    /** Get the raw summary object. */
    getRawSummary(): EnginesSummary;
}

/**
 * Convert NormalizedEngineMeta from unified store to EngineMetaDetail for engines module.
 */
function toEngineMetaDetail(
    _name: string,
    storeMeta: ReturnType<typeof summaryStore.getEngineMeta>,
): EngineMetaDetail | undefined {
    if (!storeMeta) return undefined;

    return {
        name: storeMeta.name,
        engine_path: storeMeta.enginePath ?? undefined,
        merged_options: storeMeta.merged_options,
        resolved_options: storeMeta.resolved_options,
        option_sources: storeMeta.option_sources,
        option_sources_details: storeMeta.option_sources_details,
        runtime_usi_options: storeMeta.runtime_usi_options,
        runtime_engine_info: storeMeta.runtime_engine_info ?? undefined,
    } as EngineMetaDetail;
}

/**
 * Build EngineSummaryMaps from the unified store's view model.
 */
function buildMapsFromStore(): EngineSummaryMaps {
    const viewModel = summaryStore.getViewModel();
    const metaMap = new Map<string, EngineMetaDetail>();
    const tcMap = new Map<string, string>();
    const instanceMap = new Map<string, string | null | undefined>();

    for (const [name, engineView] of viewModel.engineMap) {
        const meta = toEngineMetaDetail(name, summaryStore.getEngineMeta(name));
        if (meta) {
            metaMap.set(name, meta);
        }
        if (engineView.timeControl) {
            tcMap.set(name, engineView.timeControl);
        }
        instanceMap.set(name, engineView.instanceId);
    }

    return {
        metaMap,
        tcMap,
        instanceMap,
        defaultTime: viewModel.defaultTimeControl,
    };
}

/**
 * Creates an EngineSummaryStore bound to the given window context.
 *
 * This store delegates to the unified SummaryStore for all data access,
 * ensuring consistent data across all modules.
 */
export function createEngineSummaryStore(arenaWindow: EnginesWindow): EngineSummaryStore {
    /**
     * Initialize the store with existing data from the window context.
     * This handles the case where data was loaded before the store was created.
     */
    function initializeFromWindow(): void {
        // Load existing data into the unified store.
        // NOTE: We do NOT call setActiveSource here - that should only be called
        // when the tab becomes active (via shared/services/tabs.ts).
        // The store will use appropriate fallback behavior when no source is active.

        // Try to get data from DashboardTournament first
        const tournament = arenaWindow.DashboardTournament;
        if (tournament && typeof tournament.getNormalizedSummary === 'function') {
            const normalized = tournament.getNormalizedSummary();
            if (normalized) {
                const raw = normalized.raw as JsonObject;
                summaryStore.applyTournamentSummary({
                    ...raw,
                    engines: normalized.engines,
                    engine_meta: normalized.engineMeta as unknown as JsonObject,
                    engine_time_controls: normalized.engineTimeControls,
                    engine_instances: normalized.engineInstances,
                    default_time_control: normalized.defaultTimeControl,
                });
                return;
            }
        }

        // Try DashboardSpsa next
        const spsa = arenaWindow.DashboardSpsa;
        if (spsa && typeof spsa.getNormalizedSummary === 'function') {
            const normalized = spsa.getNormalizedSummary();
            if (normalized) {
                const raw = normalized.raw as JsonObject;
                summaryStore.applySpsaSummary({
                    ...raw,
                    engines: normalized.engines,
                    engine_meta: normalized.engineMeta as unknown as JsonObject,
                    engine_time_controls: normalized.engineTimeControls,
                    engine_instances: normalized.engineInstances,
                    default_time_control: normalized.defaultTimeControl,
                });
                return;
            }
        }
    }

    // Initialize on creation
    initializeFromWindow();

    /**
     * Get fresh summary maps. Each call retrieves current state from the unified store.
     */
    function getMaps(): EngineSummaryMaps {
        return buildMapsFromStore();
    }

    /**
     * Get engine meta for a specific engine with fresh data.
     */
    function getEngineMeta(engineName: string): EngineMetaDetail | undefined {
        return toEngineMetaDetail(engineName, summaryStore.getEngineMeta(engineName));
    }

    /**
     * Get time control spec for a specific engine.
     */
    function getTimeControl(engineName: string): string | undefined {
        return summaryStore.getTimeControl(engineName);
    }

    /**
     * Get instance ID for a specific engine.
     */
    function getInstance(engineName: string): string | null | undefined {
        return summaryStore.getInstance(engineName);
    }

    /**
     * Get default time control.
     */
    function getDefaultTimeControl(): string | null {
        return summaryStore.getDefaultTimeControl();
    }

    /**
     * Get sorted list of engine names.
     */
    function getEngineNames(): string[] {
        return summaryStore.getEngineNames();
    }

    /**
     * Check if an engine has valid merged options data.
     * Used to determine if USI options should be displayed.
     */
    function hasValidOptionsData(engineName: string): boolean {
        return summaryStore.hasValidOptionsData(engineName);
    }

    /**
     * Get the raw summary object.
     * Returns a reconstructed summary from the unified store.
     */
    function getRawSummary(): EnginesSummary {
        const viewModel = summaryStore.getViewModel();
        const engines = viewModel.engines;
        const enginesMeta: EngineMetaDetail[] = [];

        for (const name of engines) {
            const meta = toEngineMetaDetail(name, summaryStore.getEngineMeta(name));
            if (meta) {
                enginesMeta.push(meta);
            }
        }

        const engineTimeControls: Record<string, string> = {};
        const engineInstances: Record<string, string | null | undefined> = {};

        for (const [name, engineView] of viewModel.engineMap) {
            if (engineView.timeControl) {
                engineTimeControls[name] = engineView.timeControl;
            }
            engineInstances[name] = engineView.instanceId;
        }

        return {
            engines,
            enginesMeta,
            engineTimeControls,
            engineInstances,
            defaultTimeControl: viewModel.defaultTimeControl ?? undefined,
        };
    }

    return {
        getMaps,
        getEngineMeta,
        getTimeControl,
        getInstance,
        getDefaultTimeControl,
        getEngineNames,
        hasValidOptionsData,
        getRawSummary,
    };
}

// ============================================================================
// Utility exports for direct store access
// ============================================================================

/**
 * Get the unified store instance.
 * Use this for subscribing to store events or accessing store directly.
 */
export function getUnifiedStore(): typeof summaryStore {
    return summaryStore;
}
