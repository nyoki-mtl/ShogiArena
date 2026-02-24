import type { DashboardCore, DashboardRuntimeMode } from '@/types/dashboard';
import type { DashboardTabId, DashboardTabsApi } from '@/types/globals';

export const DASHBOARD_MODE_CHANGED_EVENT = 'runtime:mode-changed';

export interface DashboardModeConfig {
    readonly mode: DashboardRuntimeMode;
    readonly visibleTabs: readonly DashboardTabId[];
    readonly disableTournamentTabs: boolean;
    readonly disableGamesTab: boolean;
    readonly disableSpsaModule: boolean;
}

const MODE_CONFIG_MAP: Record<DashboardRuntimeMode, DashboardModeConfig> = {
    tournament: {
        mode: 'tournament',
        visibleTabs: ['live', 'tournament', 'openings', 'rules', 'engines', 'instances', 'games'],
        disableTournamentTabs: false,
        disableGamesTab: false,
        disableSpsaModule: true,
    },
    match: {
        mode: 'match',
        visibleTabs: ['live', 'match', 'rules', 'engines', 'instances', 'games'],
        disableTournamentTabs: true,
        disableGamesTab: false,
        disableSpsaModule: true,
    },
    sprt: {
        mode: 'sprt',
        visibleTabs: ['live', 'sprt', 'rules', 'engines', 'instances', 'games'],
        disableTournamentTabs: true,
        disableGamesTab: false,
        disableSpsaModule: true,
    },
    spsa: {
        mode: 'spsa',
        visibleTabs: ['live', 'spsa', 'rules', 'engines', 'instances'],
        disableTournamentTabs: true,
        disableGamesTab: true,
        disableSpsaModule: false,
    },
    unknown: {
        mode: 'unknown',
        visibleTabs: ['live', 'rules', 'engines', 'instances', 'games', 'openings'],
        disableTournamentTabs: false,
        disableGamesTab: false,
        disableSpsaModule: false,
    },
    generate: {
        mode: 'generate',
        visibleTabs: ['live', 'generate', 'rules', 'engines', 'instances'],
        disableTournamentTabs: true,
        disableGamesTab: true,
        disableSpsaModule: true,
    },
};

function normalizeRuntimeMode(value: unknown): DashboardRuntimeMode {
    if (typeof value === 'string') {
        const trimmed = value.trim().toLowerCase();
        if (trimmed === 'spsa') return 'spsa';
        if (trimmed === 'match') return 'match';
        if (trimmed === 'sprt') return 'sprt';
        if (trimmed === 'generate') return 'generate';
        if (trimmed === 'tournament' || trimmed === 'gauntlet' || trimmed === 'roundrobin') {
            return 'tournament';
        }
    }
    return 'unknown';
}

export function resolveRuntimeModeFromSummary(summary: unknown): DashboardRuntimeMode {
    if (!summary || typeof summary !== 'object' || Array.isArray(summary)) {
        return 'unknown';
    }
    const tournamentType = (summary as { tournamentType?: unknown }).tournamentType;
    return normalizeRuntimeMode(tournamentType);
}

export function getDashboardMode(core: DashboardCore | null | undefined): DashboardRuntimeMode {
    if (!core) return 'unknown';
    return core.state.runtimeMode ?? 'unknown';
}

export function setDashboardMode(core: DashboardCore, mode: DashboardRuntimeMode): void {
    const current = getDashboardMode(core);
    if (current === mode) {
        return;
    }
    core.mutateState('runtimeMode', mode);
    core.events.emit<DashboardRuntimeMode>(DASHBOARD_MODE_CHANGED_EVENT, mode);
    if (mode === 'spsa') {
        core.mutateState('spsaMode', true);
    } else if (mode === 'tournament' || mode === 'match' || mode === 'sprt') {
        core.mutateState('spsaMode', false);
    }
}

export function getModeConfig(mode: DashboardRuntimeMode): DashboardModeConfig {
    return MODE_CONFIG_MAP[mode] ?? MODE_CONFIG_MAP.unknown;
}

export function applyTabConfiguration(tabs: DashboardTabsApi | undefined, mode: DashboardRuntimeMode): void {
    if (!tabs) {
        return;
    }
    const config = getModeConfig(mode);
    const visible = new Set(config.visibleTabs);
    const allTabs: DashboardTabId[] = [
        'live',
        'match',
        'generate',
        'sprt',
        'tournament',
        'openings',
        'rules',
        'spsa',
        'engines',
        'instances',
        'games',
    ];
    for (const tabId of allTabs) {
        tabs.setVisibility(tabId, visible.has(tabId));
    }
    tabs.setOrder(config.visibleTabs);
}
