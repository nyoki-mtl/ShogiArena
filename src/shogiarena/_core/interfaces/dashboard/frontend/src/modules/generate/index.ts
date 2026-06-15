import { requestJson, resolveApiBase } from '@/modules/shared/services/api';
import { summaryStore } from '@/store';
import type { ArenaDashboardWindow } from '@/types/globals';

type RecordsSummary = {
    total_games: number;
    total_positions: number;
    total_bytes: number;
    file_count: number;
};

type GenerateSummaryPayload = {
    records_summary?: RecordsSummary;
    total_games?: number;
    total_positions?: number;
    total_bytes?: number;
    file_count?: number;
};

const SUMMARY_SELECTORS = {
    total: '#generateTotalGames',
    positions: '#generateTotalPositions',
    files: '#generateTotalFiles',
    bytes: '#generateTotalBytes',
};

function buildSummaryUrl(): string {
    return `${resolveApiBase()}/api/summary?source=generate`;
}

function formatCount(value: number | undefined | null): string {
    if (typeof value !== 'number' || Number.isNaN(value)) {
        return '-';
    }
    return value.toString();
}

function formatBytes(value: number | undefined | null): string {
    if (typeof value !== 'number' || Number.isNaN(value)) {
        return '-';
    }
    if (value < 1024) {
        return `${value} B`;
    }
    const units = ['KB', 'MB', 'GB', 'TB', 'PB'];
    let normalized = value;
    let unitIndex = -1;
    while (normalized >= 1024 && unitIndex < units.length - 1) {
        normalized /= 1024;
        unitIndex += 1;
    }
    return `${normalized.toFixed(1)} ${units[unitIndex]}`;
}

function updateSummary(state: RecordsSummary | null): void {
    const totalEl = document.querySelector<HTMLElement>(SUMMARY_SELECTORS.total);
    const positionsEl = document.querySelector<HTMLElement>(SUMMARY_SELECTORS.positions);
    const filesEl = document.querySelector<HTMLElement>(SUMMARY_SELECTORS.files);
    const bytesEl = document.querySelector<HTMLElement>(SUMMARY_SELECTORS.bytes);

    if (!totalEl || !positionsEl || !filesEl || !bytesEl || !state) {
        return;
    }

    totalEl.textContent = formatCount(state.total_games);
    positionsEl.textContent = formatCount(state.total_positions);
    filesEl.textContent = formatCount(state.file_count);
    bytesEl.textContent = formatBytes(state.total_bytes);
}

function coerceFiniteNumber(value: unknown): number | null {
    if (typeof value !== 'number' || Number.isNaN(value)) {
        return null;
    }
    return value;
}

function coerceRecordsSummary(payload: GenerateSummaryPayload | null): RecordsSummary | null {
    if (!payload) {
        return null;
    }
    if (payload.records_summary) {
        return payload.records_summary;
    }
    const totalGames = coerceFiniteNumber(payload.total_games);
    const totalPositions = coerceFiniteNumber(payload.total_positions);
    const totalBytes = coerceFiniteNumber(payload.total_bytes);
    const fileCount = coerceFiniteNumber(payload.file_count);
    if (totalGames === null || totalPositions === null || totalBytes === null || fileCount === null) {
        return null;
    }
    return {
        total_games: totalGames,
        total_positions: totalPositions,
        total_bytes: totalBytes,
        file_count: fileCount,
    };
}

function extractSummaryPayload(value: unknown): GenerateSummaryPayload | null {
    if (!value || typeof value !== 'object' || Array.isArray(value)) {
        return null;
    }
    return value as GenerateSummaryPayload;
}

function extractRecordsSummary(value: unknown): RecordsSummary | null {
    const payload = extractSummaryPayload(value);
    return coerceRecordsSummary(payload);
}

async function fetchSummary(): Promise<RecordsSummary | null> {
    try {
        const response = await requestJson<unknown>(buildSummaryUrl(), { cache: 'no-cache' });
        return extractRecordsSummary(response);
    } catch (error) {
        console.error('[GenerateModule] Failed to load summary', error);
        return null;
    }
}

export function installGenerateModule(owner: ArenaDashboardWindow): { setActive?: (active: boolean) => void } {
    const doc = owner.document;
    if (!doc) {
        return {};
    }
    if (owner.DashboardGenerate) {
        // Re-installing would attach a duplicate 'summary:update' listener; reuse the live instance.
        return owner.DashboardGenerate;
    }

    const core = owner.DashboardCore;
    const events = core?.events;
    let isActive = false;
    let lastSummary: RecordsSummary | null = null;

    const refreshSummary = async (): Promise<void> => {
        const summary = await fetchSummary();
        if (summary) {
            lastSummary = summary;
            if (isActive) {
                updateSummary(summary);
            }
        }
    };

    const handleSummaryEvent = (event?: unknown): void => {
        const summaryCandidate =
            event && typeof event === 'object' && event !== null && 'summary' in event
                ? (event as { summary?: unknown }).summary
                : event;
        const summary = extractRecordsSummary(summaryCandidate);
        if (!summary) {
            return;
        }
        lastSummary = summary;
        if (isActive) {
            updateSummary(summary);
        }
    };

    if (events && typeof events.on === 'function') {
        events.on('summary:update', handleSummaryEvent);
    }
    const initialSummary = summaryStore.getRawSummary();
    if (initialSummary) {
        handleSummaryEvent({ summary: initialSummary });
    }

    const api = {
        refresh: () => {
            void refreshSummary();
        },
        setActive(nextActive: boolean) {
            isActive = Boolean(nextActive);
            if (isActive) {
                if (lastSummary) {
                    updateSummary(lastSummary);
                } else {
                    void refreshSummary();
                }
            }
        },
    };

    owner.DashboardGenerate = api;

    return api;
}
