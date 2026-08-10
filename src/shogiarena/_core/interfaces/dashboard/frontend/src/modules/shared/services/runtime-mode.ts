import { type DashboardRuntimeMode, isDashboardRuntimeMode } from '@/modules/shared/services/runtime-mode-catalog';
import type { DashboardCore } from '@/types/dashboard';
import type { DashboardTabId, DashboardTabsApi } from '@/types/globals';

export const DASHBOARD_MODE_CHANGED_EVENT = 'runtime:mode-changed';

export interface DashboardModeConfig {
    readonly mode: DashboardRuntimeMode;
    readonly visibleTabs: readonly DashboardTabId[];
    readonly disableTournamentTabs: boolean;
    readonly disableGamesTab: boolean;
    readonly disableSpsaModule: boolean;
    /**
     * Does a progress bar mean anything in this mode?
     *
     * A bar asserts progress against a finite plan. A CSA session has none: it
     * runs indefinitely and its denominator grows with every finished game, so
     * the bar reads "6/9 games into a 9-game plan" when the truth is "one game
     * running, six played". Kept as a config flag rather than a `mode === 'csa'`
     * test at the call site, so the next mode is forced to answer it too.
     */
    readonly showProgressBar: boolean;
}

const MODE_CONFIG_MAP: Record<DashboardRuntimeMode, DashboardModeConfig> = {
    tournament: {
        mode: 'tournament',
        visibleTabs: ['live', 'tournament', 'openings', 'rules', 'engines', 'instances', 'games', 'book'],
        disableTournamentTabs: false,
        disableGamesTab: false,
        disableSpsaModule: true,
        showProgressBar: true,
    },
    match: {
        mode: 'match',
        visibleTabs: ['live', 'match', 'rules', 'engines', 'instances', 'games', 'book'],
        disableTournamentTabs: true,
        disableGamesTab: false,
        disableSpsaModule: true,
        showProgressBar: true,
    },
    sprt: {
        mode: 'sprt',
        visibleTabs: ['live', 'sprt', 'rules', 'engines', 'instances', 'games', 'book'],
        disableTournamentTabs: true,
        disableGamesTab: false,
        disableSpsaModule: true,
        showProgressBar: true,
    },
    spsa: {
        mode: 'spsa',
        visibleTabs: ['live', 'spsa', 'rules', 'engines', 'instances'],
        disableTournamentTabs: true,
        disableGamesTab: true,
        disableSpsaModule: false,
        showProgressBar: true,
    },
    unknown: {
        mode: 'unknown',
        visibleTabs: ['live', 'rules', 'engines', 'instances', 'games', 'openings', 'book'],
        disableTournamentTabs: false,
        disableGamesTab: false,
        disableSpsaModule: false,
        showProgressBar: true,
    },
    generate: {
        mode: 'generate',
        visibleTabs: ['live', 'generate', 'rules', 'engines', 'instances'],
        disableTournamentTabs: true,
        disableGamesTab: true,
        disableSpsaModule: true,
        showProgressBar: true,
    },
    // CSA keeps the same game-history mental model as tournament modes while
    // retaining a run-centric operational surface for bridge health.
    csa: {
        mode: 'csa',
        visibleTabs: ['live', 'games', 'csa'],
        disableTournamentTabs: true,
        disableGamesTab: false,
        disableSpsaModule: true,
        showProgressBar: false,
    },
};

/** Tournament shapes the server may name instead of "tournament". */
const TOURNAMENT_ALIASES = new Set(['gauntlet', 'roundrobin']);

export function normalizeRuntimeMode(value: unknown): DashboardRuntimeMode {
    if (typeof value !== 'string') return 'unknown';
    const trimmed = value.trim().toLowerCase();
    if (TOURNAMENT_ALIASES.has(trimmed)) return 'tournament';
    // Every mode in the catalogue is accepted by construction, so a new one
    // cannot be dropped here by omission. `unknown` now means only what it says:
    // the input was not a mode we have.
    return isDashboardRuntimeMode(trimmed) ? trimmed : 'unknown';
}

export function resolveRuntimeModeFromSummary(summary: unknown): DashboardRuntimeMode {
    if (!summary || typeof summary !== 'object' || Array.isArray(summary)) {
        return 'unknown';
    }
    const tournamentType = (summary as { tournament_type?: unknown }).tournament_type;
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
    // SPSA mode is simply "is this SPSA". The allow-list this replaced left
    // `spsaMode` at its previous value for any mode missing from it.
    core.mutateState('spsaMode', mode === 'spsa');
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
        'book',
        // Every tab id must appear here. `setVisibility` is only called for the
        // ids in this array, so one left out is never hidden by anybody — it
        // would simply leak into whichever profile ships its markup.
        'csa',
    ];
    for (const tabId of allTabs) {
        tabs.setVisibility(tabId, visible.has(tabId));
    }
    tabs.setOrder(config.visibleTabs);
}
