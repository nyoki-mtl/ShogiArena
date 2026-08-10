import { installEnginesModule } from '@/modules/engines';
import type { DashboardEnginesApi } from '@/modules/engines/types';
import { installGamesModule } from '@/modules/games';
import { installGamesRender } from '@/modules/games/components/render';
import { installGamesUtils } from '@/modules/games/utils';
import { installCsaModule } from '@/modules/csa';
import { installGenerateModule } from '@/modules/generate';
import { installInstancesModule } from '@/modules/instances';
import {
    initializeLiveMain,
    installLiveCardsModule,
    installLiveSummaryModule,
    installLiveTimeModule,
    installLiveUpdatesModule,
} from '@/modules/live';
import { installBookModule } from '@/modules/book';
import { installMatchModule } from '@/modules/match';
import { installRulesModule } from '@/modules/rules';
import {
    applyTabConfiguration,
    getModeConfig,
    installDashboardApi,
    installDashboardCore,
    installDashboardDom,
    installDashboardNavigation,
    installDashboardNotices,
    installDashboardTabs,
    installDashboardTheme,
    installHeaderProgress,
    installShogiBoardGlobals,
    resolveRuntimeModeFromSummary,
    setDashboardMode,
} from '@/modules/shared';
import { installSprtModule } from '@/modules/sprt';
import { installSpsaModule } from '@/modules/spsa';
import type { DashboardSpsaPublicApi } from '@/modules/spsa/types';
import {
    installTournamentBootstrap,
    installTournamentData,
    installTournamentMatchups,
    installTournamentOpenings,
    installTournamentStandings,
    installTournamentState,
    type TournamentDashboardAPI,
    type TournamentSummary,
} from '@/modules/tournament';
import { summaryStore } from '@/store';
import type { DashboardCore, DashboardRuntimeMode } from '@/types/dashboard';
import type { ArenaDashboardWindow, DashboardTabsApi } from '@/types/globals';
import type { JsonObject } from '@/types/shared';

interface TournamentFeatureFlags {
    state: boolean;
    data: boolean;
    matchups: boolean;
    openings: boolean;
    standings: boolean;
    bootstrap: boolean;
}

interface DashboardFeatureFlags {
    shared: boolean;
    live: boolean;
    instances: boolean;
    games: boolean;
    rules: boolean;
    engines: boolean;
    spsa: boolean;
    match: boolean;
    sprt: boolean;
    book: boolean;
    generate: boolean;
    csa: boolean;
    tournament: TournamentFeatureFlags;
}

type TournamentFeatureOverrides = Partial<TournamentFeatureFlags>;

export interface DashboardFeatureOverrides {
    shared?: boolean;
    live?: boolean;
    instances?: boolean;
    games?: boolean;
    rules?: boolean;
    engines?: boolean;
    spsa?: boolean;
    match?: boolean;
    sprt?: boolean;
    book?: boolean;
    generate?: boolean;
    csa?: boolean;
    tournament?: TournamentFeatureOverrides;
}

export interface DashboardInitialData {
    summary?: TournamentSummary;
    apiPort?: number;
}

export interface DashboardBootstrapOptions {
    owner?: ArenaDashboardWindow;
    features?: DashboardFeatureOverrides;
    initialData?: DashboardInitialData;
    runtimeMode?: DashboardRuntimeMode;
}

export interface DashboardInitializationResult {
    owner: ArenaDashboardWindow;
    features: DashboardFeatureFlags;
    tournament?: TournamentDashboardAPI;
    engines?: DashboardEnginesApi;
    spsa?: DashboardSpsaPublicApi;
    match?: ReturnType<typeof installMatchModule>;
    sprt?: ReturnType<typeof installSprtModule>;
    book?: ReturnType<typeof installBookModule>;
    generate?: ReturnType<typeof installGenerateModule>;
    csa?: ReturnType<typeof installCsaModule>;
}

const DEFAULT_FEATURES: DashboardFeatureFlags = {
    shared: true,
    live: true,
    instances: true,
    games: true,
    rules: true,
    engines: true,
    spsa: true,
    match: true,
    sprt: true,
    book: true,
    generate: false,
    csa: false,
    tournament: {
        state: true,
        data: true,
        matchups: true,
        openings: true,
        standings: true,
        bootstrap: true,
    },
};

function resolveFeatures(overrides?: DashboardFeatureOverrides): DashboardFeatureFlags {
    const flags: DashboardFeatureFlags = {
        shared: DEFAULT_FEATURES.shared,
        live: DEFAULT_FEATURES.live,
        instances: DEFAULT_FEATURES.instances,
        games: DEFAULT_FEATURES.games,
        rules: DEFAULT_FEATURES.rules,
        engines: DEFAULT_FEATURES.engines,
        spsa: DEFAULT_FEATURES.spsa,
        match: DEFAULT_FEATURES.match,
        sprt: DEFAULT_FEATURES.sprt,
        book: DEFAULT_FEATURES.book,
        generate: DEFAULT_FEATURES.generate,
        csa: DEFAULT_FEATURES.csa,
        tournament: { ...DEFAULT_FEATURES.tournament },
    };

    if (!overrides) {
        return flags;
    }

    if (typeof overrides.shared === 'boolean') flags.shared = overrides.shared;
    if (typeof overrides.live === 'boolean') flags.live = overrides.live;
    if (typeof overrides.instances === 'boolean') flags.instances = overrides.instances;
    if (typeof overrides.games === 'boolean') flags.games = overrides.games;
    if (typeof overrides.rules === 'boolean') flags.rules = overrides.rules;
    if (typeof overrides.engines === 'boolean') flags.engines = overrides.engines;
    if (typeof overrides.spsa === 'boolean') flags.spsa = overrides.spsa;
    if (typeof overrides.match === 'boolean') flags.match = overrides.match;
    if (typeof overrides.sprt === 'boolean') flags.sprt = overrides.sprt;
    if (typeof overrides.book === 'boolean') flags.book = overrides.book;
    if (typeof overrides.generate === 'boolean') flags.generate = overrides.generate;
    if (typeof overrides.csa === 'boolean') flags.csa = overrides.csa;
    if (overrides.tournament) {
        const tournamentOverrides = overrides.tournament;
        if (typeof tournamentOverrides.state === 'boolean') flags.tournament.state = tournamentOverrides.state;
        if (typeof tournamentOverrides.data === 'boolean') flags.tournament.data = tournamentOverrides.data;
        if (typeof tournamentOverrides.matchups === 'boolean') flags.tournament.matchups = tournamentOverrides.matchups;
        if (typeof tournamentOverrides.openings === 'boolean') flags.tournament.openings = tournamentOverrides.openings;
        if (typeof tournamentOverrides.standings === 'boolean')
            flags.tournament.standings = tournamentOverrides.standings;
        if (typeof tournamentOverrides.bootstrap === 'boolean')
            flags.tournament.bootstrap = tournamentOverrides.bootstrap;
    }

    return flags;
}

function applyInitialData(owner: ArenaDashboardWindow, initialData?: DashboardInitialData): void {
    if (!initialData) {
        return;
    }

    if (typeof initialData.apiPort === 'number' && Number.isFinite(initialData.apiPort) && initialData.apiPort > 0) {
        owner.ARENA_API_PORT = initialData.apiPort;
    }
}

export function initializeDashboard(options: DashboardBootstrapOptions = {}): DashboardInitializationResult {
    const owner: ArenaDashboardWindow = options.owner ?? (window as ArenaDashboardWindow);
    applyInitialData(owner, options.initialData);
    const features = resolveFeatures(options.features);
    const initialSummary =
        options.initialData?.summary && typeof options.initialData.summary === 'object'
            ? (options.initialData.summary as { tournament_type?: unknown })
            : undefined;
    const declaredMode =
        typeof owner.ARENA_RUNTIME_MODE === 'string' ? (owner.ARENA_RUNTIME_MODE as DashboardRuntimeMode) : null;
    const resolvedMode = resolveRuntimeModeFromSummary(initialSummary);
    const initialMode = options.runtimeMode ?? declaredMode ?? resolvedMode;
    if (options.initialData?.summary && typeof options.initialData.summary === 'object' && initialMode !== 'generate') {
        const summary = options.initialData.summary as unknown as JsonObject;
        if (initialMode === 'spsa') {
            summaryStore.applySpsaSummary(summary);
        } else {
            summaryStore.applyTournamentSummary(summary);
        }
    }
    const modeConfig = getModeConfig(initialMode);

    if (modeConfig.disableTournamentTabs) {
        features.tournament.state = false;
        features.tournament.data = false;
        features.tournament.matchups = false;
        features.tournament.openings = false;
        features.tournament.standings = false;
        features.tournament.bootstrap = false;
    }

    if (modeConfig.disableGamesTab) {
        features.games = false;
    }

    if (modeConfig.disableSpsaModule) {
        features.spsa = false;
    }

    if (modeConfig.mode === 'match') {
        features.sprt = false;
        features.match = true;
    } else if (modeConfig.mode === 'sprt') {
        features.match = false;
        features.sprt = true;
    } else if (modeConfig.mode === 'generate') {
        features.match = false;
        features.sprt = false;
        features.generate = true;
    } else if (modeConfig.mode === 'csa') {
        features.match = false;
        features.sprt = false;
        features.generate = false;
        features.csa = true;
    }

    owner.ARENA_DISABLE_SPSA = !features.spsa;
    owner.ARENA_RUNTIME_MODE = initialMode;

    if (features.shared === false) {
        throw new Error('Dashboard shared features cannot be disabled');
    }

    let core: DashboardCore | undefined;
    let tabsApi: DashboardTabsApi | undefined;

    installDashboardTheme(owner);
    installDashboardApi(owner);
    installDashboardDom(owner);
    installDashboardNotices(owner);
    core = installDashboardCore(owner);
    tabsApi = installDashboardTabs(owner);
    installHeaderProgress(owner);
    installDashboardNavigation(owner);
    installShogiBoardGlobals(owner);

    if (core) {
        setDashboardMode(core, initialMode);
    }

    if (tabsApi) {
        applyTabConfiguration(tabsApi, initialMode);
    }

    let tournamentApi: TournamentDashboardAPI | undefined;
    const { tournament: tournamentFlags } = features;
    const shouldInstallTournamentState =
        tournamentFlags.state ||
        tournamentFlags.data ||
        tournamentFlags.matchups ||
        tournamentFlags.openings ||
        tournamentFlags.standings ||
        tournamentFlags.bootstrap;

    if (shouldInstallTournamentState) {
        tournamentApi = installTournamentState(owner);
    }

    if (tournamentFlags.data) {
        tournamentApi = installTournamentData(owner);
    }

    if (tournamentFlags.matchups) {
        tournamentApi = installTournamentMatchups(owner);
    }

    if (tournamentFlags.openings) {
        tournamentApi = installTournamentOpenings(owner);
    }

    if (tournamentFlags.standings) {
        tournamentApi = installTournamentStandings(owner);
    }

    if (tournamentFlags.bootstrap) {
        installTournamentBootstrap(owner);
    }

    if (features.live) {
        installLiveTimeModule(owner);
        installLiveCardsModule(owner);
        installLiveSummaryModule(owner);
        installLiveUpdatesModule(owner);
        initializeLiveMain(owner);
    }

    if (features.instances) {
        installInstancesModule(owner);
    }

    if (features.games) {
        installGamesUtils(owner);
        installGamesRender(owner);
        installGamesModule(owner);
    }

    if (features.rules) {
        installRulesModule(owner);
    }

    let enginesApi: DashboardEnginesApi | undefined;
    if (features.engines) {
        enginesApi = installEnginesModule(owner);
    }

    let spsaApi: DashboardSpsaPublicApi | undefined;
    if (features.spsa) {
        spsaApi = installSpsaModule(owner);
    }

    let matchApi: ReturnType<typeof installMatchModule> | undefined;
    if (features.match) {
        matchApi = installMatchModule(owner);
    }

    let sprtApi: ReturnType<typeof installSprtModule> | undefined;
    if (features.sprt) {
        sprtApi = installSprtModule(owner);
    }

    let bookApi: ReturnType<typeof installBookModule> | undefined;
    if (features.book) {
        bookApi = installBookModule(owner);
    }

    let generateApi: ReturnType<typeof installGenerateModule> | undefined;
    if (features.generate) {
        generateApi = installGenerateModule(owner);
    }

    let csaApi: ReturnType<typeof installCsaModule> | undefined;
    if (features.csa) {
        csaApi = installCsaModule(owner);
        owner.DashboardCsa = csaApi;
    }

    return {
        owner,
        features,
        tournament: tournamentApi,
        engines: enginesApi,
        spsa: spsaApi,
        match: matchApi,
        sprt: sprtApi,
        book: bookApi,
        generate: generateApi,
        csa: csaApi,
    };
}
