import type { TournamentState } from '@/modules/tournament/types';
import type { DashboardCore } from '@/types/dashboard';
import type { TournamentDashboardAPI } from '@/modules/tournament/types';
import { escapeHtml } from '@/modules/shared/utils/html';
import { assertDashboardCoreGetApiBase } from '@/modules/shared/utils/dashboard-core';
import { abbreviateName, hashString } from '@/modules/shared/utils/format';
import {
    formatOptionValue,
    formatDuration,
    formatNodesCountDetail,
    formatNodesCountShort,
    parseTimeControlSpec,
} from '@/modules/tournament/utils/formatters';
import { createWorkerDomainState } from './domain/workers';
import { createStatsDomainState } from './domain/stats';
import { createLayoutUiState } from './ui/layout';

interface TournamentWindow extends Window {
    DashboardTournament?: TournamentDashboardAPI;
    DashboardCore?: DashboardCore;
    ARENA_DASHBOARD_STOPPED?: boolean;
    __ARENA_NUM_WORKERS__?: number;
}

const defaultWindow = window as TournamentWindow;

let installedTournamentDashboard: TournamentDashboardAPI | null = null;

function resolveWorkerCount(owner: TournamentWindow): number {
    const workerCountRaw =
        typeof owner.__ARENA_NUM_WORKERS__ === 'number' && Number.isFinite(owner.__ARENA_NUM_WORKERS__)
            ? Number(owner.__ARENA_NUM_WORKERS__)
            : 0;
    return Number.isFinite(workerCountRaw) && workerCountRaw >= 0 ? workerCountRaw : 0;
}

function createInitialTournamentState(owner: TournamentWindow): TournamentState {
    const workerCount = resolveWorkerCount(owner);
    const existing = owner.DashboardTournament?.state as TournamentState | undefined;
    if (existing) {
        return existing;
    }

    return {
        ...createWorkerDomainState(workerCount),
        ...createStatsDomainState(),
        ...createLayoutUiState(),
    } satisfies TournamentState;
}

function createTournamentDashboard(owner: TournamentWindow): TournamentDashboardAPI {
    const state = createInitialTournamentState(owner);

    const dashboard: TournamentDashboardAPI = owner.DashboardTournament ?? ({} as TournamentDashboardAPI);
    dashboard.state = state;
    dashboard.getNormalizedSummary = () => state.normalizedSummary;

    const getApiBase = (): string => {
        const resolver = assertDashboardCoreGetApiBase(owner);
        return resolver();
    };

    // formatters are provided by shared tournament utilities for reuse across profiles
    // (tournament / spsa) without requiring DashboardTournament to be installed.

    function formatOpeningLabel(value: unknown): string {
        if (value === null || value === undefined) return '-';
        const raw = String(value).trim();
        if (!raw) return '-';
        if (raw === 'startpos') {
            return 'Standard start position';
        }

        const parts = raw.split(/\s+/).filter(Boolean);
        if (!parts.length) {
            return '-';
        }

        const board = parts[0];
        const turnCode = parts.length > 1 ? parts[1] : '';
        const moveTokens = parts.slice(2);

        const turnLabel =
            turnCode === 'w' ? 'white to move' : turnCode === 'b' ? 'black to move' : turnCode ? `${turnCode}` : null;
        const movesLabel = moveTokens.length ? moveTokens.join(' ') : null;

        const segments = [`SFEN: ${board}`];
        if (turnLabel) segments.push(turnLabel);
        if (movesLabel) segments.push(`moves ${movesLabel}`);
        return segments.join(' | ');
    }

    function formatTimeControlShort(spec: string): string {
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
    }

    Object.assign(dashboard, {
        state,
        getApiBase,
        abbreviateName,
        hashString,
        escapeHtml,
        formatOptionValue,
        formatDuration,
        formatNodesCountShort,
        formatNodesCountDetail,
        formatOpeningLabel,
        parseTimeControlSpec,
        formatTimeControlShort,
    });
    return dashboard;
}

export function installTournamentState(owner: TournamentWindow = defaultWindow): TournamentDashboardAPI {
    if (installedTournamentDashboard) {
        owner.DashboardTournament = installedTournamentDashboard;
        return installedTournamentDashboard;
    }
    const dashboard = createTournamentDashboard(owner);
    installedTournamentDashboard = dashboard;
    owner.DashboardTournament = dashboard;
    return dashboard;
}
