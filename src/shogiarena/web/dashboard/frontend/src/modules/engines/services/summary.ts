/**
 * Engine Summary Utilities
 *
 * Note: Most summary-related functions have been moved to the unified store
 * (/src/store/summaryStore.ts). This file only contains utility functions
 * that don't belong in the store.
 */

/**
 * Normalize an instance label to a human-readable string.
 * Returns 'local' for empty/null/undefined values.
 */
export function normalizeInstanceLabel(value: string | null | undefined): string {
    if (typeof value === 'string' && value.trim()) {
        return value.trim();
    }
    return 'local';
}
