/**
 * Store Module
 *
 * Exports the unified summary store for managing all engine summary data
 * across Tournament, SPSA, and Runtime sources.
 */

export { summaryStore } from './summaryStore';
export type {
    // Source types
    SummarySource,
    // Meta types
    BaseEngineMeta,
    EngineOptionMeta,
    RuntimeEngineMeta,
    RuntimeUsiOptionEntry,
    NormalizedEngineMeta,
    // Raw data types
    RawTournamentData,
    RawSpsaData,
    RawRuntimeData,
    // State types
    SummaryStoreState,
    // View types
    EngineViewModel,
    SummaryViewModel,
    // Subscription types
    StoreEventType,
    StoreEventCallback,
    StoreEvent,
    Subscription,
    // Store interface
    SummaryStore,
} from './types';
