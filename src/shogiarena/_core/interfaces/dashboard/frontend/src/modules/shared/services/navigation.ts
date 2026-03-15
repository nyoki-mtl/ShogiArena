import type { DashboardEnginesApi } from '@/modules/engines/types';
import type { DashboardGamesApi } from '@/modules/games/types';
import type {
    DashboardNavigationApi,
    DashboardTabId,
    DashboardTabsApi,
    FocusEngineOptions,
    FocusInstanceOptions,
    FocusTournamentOptions,
    LiveGameNavigationOptions,
    SpsaNavigationOptions,
} from '@/types/globals';
import type { DashboardInstancesApi } from '@/modules/instances/types';
import type { LiveCardsApi } from '@/types/live';
import { requireLiveApi, type LiveNamespaceOwner } from '@/modules/live/utils/liveNamespace';
import type { DashboardSpsaApi, DashboardSpsaPublicApi, SpsaTabId } from '@/modules/spsa/types';
import type { TournamentDashboardAPI } from '@/modules/tournament/types';

interface NavigationWindow extends LiveNamespaceOwner {
    DashboardTabs?: DashboardTabsApi;
    DashboardGames?: DashboardGamesApi & {
        setInstanceFilter?: (instanceId: string | null, options?: { activateTab?: boolean }) => void;
        clearInstanceFilter?: () => void;
    };
    DashboardEngines?: DashboardEnginesApi;
    DashboardInstances?: DashboardInstancesApi;
    DashboardTournament?: TournamentDashboardAPI;
    DashboardSpsa?: DashboardSpsaApi | DashboardSpsaPublicApi;
    DashboardNavigation?: DashboardNavigationApi;
}

const defaultWindow = window as NavigationWindow;

let installedNavigation: DashboardNavigationApi | null = null;

function attachNavigation(owner: NavigationWindow, api: DashboardNavigationApi): void {
    owner.DashboardNavigation = api;
}

function requireTabs(owner: NavigationWindow): DashboardTabsApi {
    const tabs = owner.DashboardTabs;
    if (!tabs || typeof tabs.setActive !== 'function') {
        throw new Error('DashboardTabs.setActive is unavailable');
    }
    return tabs;
}

function openTabInternal(owner: NavigationWindow, tabId: DashboardTabId): void {
    const tabs = requireTabs(owner);
    tabs.setActive(tabId);
}

function requireLiveCards(owner: NavigationWindow): LiveCardsApi {
    const cards = requireLiveApi(owner, 'cards', 'DashboardLiveCards is unavailable');
    if (!cards) {
        throw new Error('DashboardLiveCards is unavailable');
    }
    if (typeof cards.focusGameOnLiveTab !== 'function') {
        throw new Error('Live cards focusGameOnLiveTab is unavailable');
    }
    return cards;
}

async function openGameInternal(
    owner: NavigationWindow,
    gameId: string | number,
    options: LiveGameNavigationOptions,
): Promise<void> {
    const cardsApi = requireLiveCards(owner);
    await cardsApi.focusGameOnLiveTab(gameId, options);
}

function focusInstanceInternal(
    owner: NavigationWindow,
    instanceId: string,
    options: FocusInstanceOptions | undefined,
): void {
    const id = String(instanceId ?? '').trim();
    if (!id) return;
    if (options?.activateTab !== false) {
        openTabInternal(owner, 'instances');
    }
    const instancesApi = owner.DashboardInstances;
    if (!instancesApi || typeof instancesApi.focusInstance !== 'function') {
        throw new Error('DashboardInstances.focusInstance is unavailable');
    }
    instancesApi.focusInstance(id);
}

function focusInstancesInternal(
    owner: NavigationWindow,
    instanceIds: readonly string[],
    options: FocusInstanceOptions | undefined,
): void {
    const ids = instanceIds
        .map((value) => String(value ?? '').trim())
        .filter((value): value is string => value.length > 0);
    if (!ids.length) return;

    if (options?.activateTab !== false) {
        openTabInternal(owner, 'instances');
    }

    const instancesApi = owner.DashboardInstances;
    if (!instancesApi || typeof instancesApi.focusInstances !== 'function') {
        throw new Error('DashboardInstances.focusInstances is unavailable');
    }
    instancesApi.focusInstances(ids);
}

function showInstanceGamesInternal(
    owner: NavigationWindow,
    instanceId: string,
    options: FocusInstanceOptions | undefined,
): void {
    const id = String(instanceId ?? '').trim();
    if (options?.activateTab !== false) {
        openTabInternal(owner, 'games');
    }
    if (!id) return;
    const gamesApi = owner.DashboardGames;
    if (!gamesApi || typeof gamesApi.setInstanceFilter !== 'function') {
        throw new Error('DashboardGames.setInstanceFilter is unavailable');
    }
    gamesApi.setInstanceFilter(id, { activateTab: options?.activateTab !== false });
}

function focusTournamentInternal(
    owner: NavigationWindow,
    engineName: string,
    options: FocusTournamentOptions | undefined,
): void {
    const name = String(engineName ?? '').trim();
    if (!name) return;
    const tournamentApi = owner.DashboardTournament;
    if (!tournamentApi || typeof tournamentApi.focusEngineMatchups !== 'function') {
        throw new Error('TournamentDashboardAPI.focusEngineMatchups is unavailable');
    }

    const detail = options?.detail ?? 'matchup';
    const focusOptions = {
        scroll: options?.scroll !== false,
        detail,
        preferredOpponent: detail === 'matchup' ? options?.opponent : undefined,
    } as const;

    tournamentApi.focusEngineMatchups(name, focusOptions);
}

function openSpsaInternal(owner: NavigationWindow, options: SpsaNavigationOptions | undefined): void {
    openTabInternal(owner, 'spsa');
    const targetTab: SpsaTabId = options?.tab ?? (typeof options?.updateIdx === 'number' ? 'updates' : 'overview');
    const spsaApi = owner.DashboardSpsa;

    if (!spsaApi || typeof spsaApi.setActive !== 'function' || typeof spsaApi.switchTab !== 'function') {
        throw new Error('DashboardSpsa API is unavailable');
    }

    spsaApi.setActive(true);
    spsaApi.switchTab(targetTab);

    const updateIdx = options?.updateIdx;
    if (typeof updateIdx === 'number' && Number.isFinite(updateIdx)) {
        if (targetTab !== 'updates') {
            spsaApi.switchTab('updates');
        }
        if (typeof spsaApi.focusUpdate !== 'function') {
            throw new Error('DashboardSpsa.focusUpdate is unavailable');
        }
        spsaApi.focusUpdate(updateIdx, { scroll: true });
    }

    const selector = options?.scrollIntoViewSelector;
    if (selector) {
        const element = owner.document?.querySelector(selector);
        if (element && 'scrollIntoView' in element) {
            (element as HTMLElement).scrollIntoView({ behavior: 'smooth', block: 'center' });
        }
    }
}

function focusEngineInternal(
    owner: NavigationWindow,
    engineName: string,
    options: FocusEngineOptions | undefined,
): void {
    const name = engineName.trim();
    if (!name) return;
    const targetTab: DashboardTabId = options?.tab ?? 'tournament';
    openTabInternal(owner, targetTab);
    if (targetTab === 'engines') {
        const enginesApi = owner.DashboardEngines;
        if (enginesApi && typeof enginesApi.focusEngine === 'function') {
            const engineDetail =
                options?.detail === 'options' || options?.detail === 'time' || options?.detail === 'instance'
                    ? options.detail
                    : undefined;
            const handled = enginesApi.focusEngine(name, {
                detail: engineDetail,
                scroll: options?.scroll,
            });
            if (handled) {
                return;
            }
        }
    }

    const tournamentDetail: FocusTournamentOptions = {
        detail: options?.detail === 'options' || options?.detail === 'time' ? 'options' : 'matchup',
        opponent: options?.opponent,
        scroll: options?.scroll,
    };

    focusTournamentInternal(owner, name, tournamentDetail);
}

export function installDashboardNavigation(owner: NavigationWindow = defaultWindow): DashboardNavigationApi {
    const current = owner.DashboardNavigation;
    if (current && current !== installedNavigation) {
        throw new Error('DashboardNavigation is already installed with a different implementation');
    }

    if (installedNavigation) {
        attachNavigation(owner, installedNavigation);
        return installedNavigation;
    }

    const navigation: DashboardNavigationApi = {
        openTab(tabId, options) {
            openTabInternal(owner, tabId);
            const selector = options?.scrollIntoViewSelector;
            if (selector) {
                const element = owner.document?.querySelector(selector);
                if (element && 'scrollIntoView' in element) {
                    (element as HTMLElement).scrollIntoView({ behavior: 'smooth', block: 'center' });
                }
            }
        },
        async openGame(gameId, options = {}) {
            await openGameInternal(owner, gameId, options);
        },
        focusInstance(instanceId, options) {
            focusInstanceInternal(owner, instanceId, options);
        },
        focusInstances(instanceIds, options) {
            focusInstancesInternal(owner, instanceIds, options);
        },
        showInstanceGames(instanceId, options) {
            showInstanceGamesInternal(owner, instanceId, options);
        },
        focusEngine(engineName, options) {
            focusEngineInternal(owner, engineName, options);
        },
        focusTournamentEngine(engineName, options) {
            focusTournamentInternal(owner, engineName, options);
        },
        openSpsa(options) {
            openSpsaInternal(owner, options);
        },
    };

    installedNavigation = navigation;
    attachNavigation(owner, navigation);
    return navigation;
}

export type { DashboardNavigationApi };
