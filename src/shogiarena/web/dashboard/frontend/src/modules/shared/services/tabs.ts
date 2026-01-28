import type { DashboardEnginesApi } from '@/modules/engines/types';
import type { DashboardGamesApi } from '@/modules/games/types';
import type { DashboardTabId, DashboardTabsApi } from '@/types/globals';
import type { DashboardInstancesApi } from '@/modules/instances/types';
import type { LiveCardsApi, LiveUpdatesApi } from '@/modules/live/types';
import type { DashboardRulesApi } from '@/modules/rules/types';
import type { DashboardSpsaApi, DashboardSpsaPublicApi } from '@/modules/spsa/types';
import type { DashboardMatchApi } from '@/modules/match/types';
import type { DashboardSprtApi } from '@/modules/sprt/types';
import type { TournamentDashboardAPI } from '@/modules/tournament/types';
import { summaryStore } from '@/store';

interface TabsWindow extends Window {
    DashboardTabs?: DashboardTabsApi;
    DashboardInstances?: DashboardInstancesApi;
    DashboardGames?: DashboardGamesApi;
    DashboardTournament?: TournamentDashboardAPI;
    DashboardEngines?: DashboardEnginesApi;
    DashboardRules?: DashboardRulesApi;
    DashboardSpsa?: DashboardSpsaApi | DashboardSpsaPublicApi;
    DashboardMatch?: DashboardMatchApi;
    DashboardSprt?: DashboardSprtApi;
    DashboardLiveCards?: LiveCardsApi;
    DashboardLiveUpdates?: LiveUpdatesApi;
}

type TabButton = HTMLButtonElement & {
    dataset: DOMStringMap & { dashboardTab?: DashboardTabId };
};

type TabContentElement = HTMLElement & {
    dataset: DOMStringMap & { tabContent?: string };
};

const defaultWindow = window as TabsWindow;
export const DASHBOARD_TAB_STORAGE_KEY = 'shogiarena.dashboard.activeTab';

function resolveStorage(owner: TabsWindow): Storage | null {
    try {
        const storage = owner.localStorage;
        if (!storage) {
            return null;
        }
        storage.getItem('__dashboard_tabs_probe__');
        return storage;
    } catch (error) {
        console.warn('[DashboardTabs] localStorage is not accessible', error);
        return null;
    }
}

function persistActiveTab(storage: Storage | null, tabId: DashboardTabId): void {
    if (!storage) {
        return;
    }
    try {
        storage.setItem(DASHBOARD_TAB_STORAGE_KEY, tabId);
    } catch (error) {
        console.warn('[DashboardTabs] Failed to persist tab state', error);
    }
}

function toDashboardTabId(value: unknown): DashboardTabId | null {
    if (typeof value !== 'string' || value.length === 0) {
        return null;
    }
    return value as DashboardTabId;
}

function parseContentTokens(element: TabContentElement): DashboardTabId[] {
    const raw = element.dataset.tabContent ?? '';
    return raw
        .split(/\s+/)
        .map((token) => token.trim())
        .filter((token): token is DashboardTabId => token.length > 0) as DashboardTabId[];
}

function notifyModules(owner: TabsWindow, tabId: DashboardTabId): void {
    owner.DashboardInstances?.setActive?.(tabId === 'instances');
    owner.DashboardGames?.setActive?.(tabId === 'games');
    owner.DashboardTournament?.setActive?.(tabId === 'tournament');
    owner.DashboardEngines?.setActive?.(tabId === 'engines');
    owner.DashboardRules?.setActive?.(tabId === 'rules');
    owner.DashboardSpsa?.setActive?.(tabId === 'spsa');
    owner.DashboardMatch?.setActive?.(tabId === 'match');
    owner.DashboardSprt?.setActive?.(tabId === 'sprt');
    owner.DashboardTournament?.setOpeningsActive?.(tabId === 'openings');
    owner.DashboardTournament?.notifyTabChange?.(tabId);

    // Update unified store's active source when switching to tournament/openings or SPSA tabs.
    // This ensures engine meta merge priority uses the currently active data source.
    if (tabId === 'tournament' || tabId === 'openings' || tabId === 'match' || tabId === 'sprt') {
        summaryStore.setActiveSource('tournament');
    } else if (tabId === 'spsa') {
        summaryStore.setActiveSource('spsa');
    }

    // Trigger resume when switching to the Live tab from another internal tab
    // Updates module fetches latest snapshots; Cards module triggers UI refresh burst
    if (tabId === 'live') {
        owner.DashboardLiveUpdates?.onTabActivate?.();
        owner.DashboardLiveCards?.onTabActivate?.();
    }
}

function createTabsApi(
    owner: TabsWindow,
    initialButtons: TabButton[],
    contents: TabContentElement[],
): DashboardTabsApi {
    let buttons = Array.from(initialButtons);
    const buttonMap = new Map<DashboardTabId, TabButton>();
    buttons.forEach((button) => {
        const tabId = toDashboardTabId(button.dataset.dashboardTab);
        if (tabId) {
            buttonMap.set(tabId, button);
        }
    });

    const buttonsParent = buttons[0]?.parentElement ?? null;
    const storage = resolveStorage(owner);

    const findFirstVisibleTab = (excluding?: DashboardTabId): DashboardTabId | null => {
        for (const button of buttons) {
            const tabId = toDashboardTabId(button.dataset.dashboardTab);
            if (!tabId || tabId === excluding) {
                continue;
            }
            if (!button.hidden) {
                return tabId;
            }
        }
        return null;
    };

    // Always start with 'live' tab active, ignoring previous persistence or server-rendered state.
    // This ensures a consistent "landing" experience on the LiveView.
    let preferredTab: DashboardTabId | null = null;
    let activeTab: DashboardTabId = 'live';

    function applyTabState(tabId: DashboardTabId, options: { persist?: boolean } = {}): void {
        const { persist = true } = options;
        const targetButton = buttonMap.get(tabId);
        if (!targetButton) {
            const fallback = findFirstVisibleTab(tabId);
            if (fallback && fallback !== tabId) {
                applyTabState(fallback, { persist });
                return;
            }
        } else if (targetButton.hidden) {
            const fallback = findFirstVisibleTab(tabId);
            if (fallback && fallback !== tabId) {
                applyTabState(fallback, { persist: preferredTab === null && persist });
                return;
            }
        }
        activeTab = tabId;
        if (preferredTab === tabId) {
            preferredTab = null;
        }

        buttons.forEach((button) => {
            const isActive = toDashboardTabId(button.dataset.dashboardTab) === tabId;
            button.classList.toggle('active', isActive);
            button.setAttribute('aria-selected', isActive ? 'true' : 'false');
        });

        contents.forEach((element) => {
            const tokens = parseContentTokens(element);
            element.classList.toggle('active', tokens.includes(tabId));
        });

        notifyModules(owner, tabId);
        if (persist) {
            persistActiveTab(storage, tabId);
        }
    }

    buttons.forEach((button) => {
        button.addEventListener('click', () => {
            const tabId = toDashboardTabId(button.dataset.dashboardTab);
            if (!tabId || tabId === activeTab) {
                return;
            }
            applyTabState(tabId);
        });
    });

    function reorderButtons(order: readonly DashboardTabId[]): void {
        if (!buttonsParent || order.length === 0) {
            return;
        }
        const desired = order
            .map((tabId) => {
                const button = buttonMap.get(tabId);
                return button ? { tabId, button } : null;
            })
            .filter((entry): entry is { tabId: DashboardTabId; button: TabButton } => entry !== null);

        if (desired.length === 0) {
            return;
        }

        const desiredSet = new Set(desired.map((entry) => entry.tabId));
        const remaining = buttons.filter((button) => {
            const tabId = toDashboardTabId(button.dataset.dashboardTab);
            return !tabId || !desiredSet.has(tabId);
        });

        const reordered = desired.map((entry) => entry.button).concat(remaining);
        reordered.forEach((button) => {
            if (button.parentElement === buttonsParent) {
                buttonsParent.append(button);
            }
        });
        buttons = reordered;
    }

    // Normalize the DOM and notify modules for the initial state.
    applyTabState(activeTab);

    return {
        setActive(tabId: DashboardTabId) {
            if (tabId === activeTab) {
                return;
            }
            applyTabState(tabId);
        },
        setVisibility(tabId: DashboardTabId, visible: boolean) {
            const button = buttonMap.get(tabId);
            if (!button) {
                return;
            }
            const shouldShow = Boolean(visible);
            const wasHidden = button.hidden;
            button.hidden = !shouldShow;

            if (!shouldShow && activeTab === tabId) {
                const fallback = findFirstVisibleTab(tabId) ?? 'live';
                const fallbackButton = buttonMap.get(fallback);
                if (fallbackButton && !fallbackButton.hidden) {
                    applyTabState(fallback, { persist: preferredTab === null });
                }
            }

            if (shouldShow && wasHidden && activeTab === tabId) {
                // normalise state in case the button reappears while active
                applyTabState(tabId);
            } else if (shouldShow && preferredTab === tabId) {
                applyTabState(tabId);
            }
        },
        setOrder(tabIds: readonly DashboardTabId[]) {
            reorderButtons(tabIds);
        },
        getActive() {
            return activeTab;
        },
    };
}

export function installDashboardTabs(owner: TabsWindow = defaultWindow): DashboardTabsApi {
    if (owner.DashboardTabs) {
        return owner.DashboardTabs;
    }

    const doc = owner.document;
    if (!doc) {
        const api: DashboardTabsApi = {
            setActive() {
                /* no-op without document */
            },
            setVisibility() {
                /* no-op without document */
            },
            setOrder() {
                /* no-op without document */
            },
            getActive() {
                return 'live';
            },
        };
        owner.DashboardTabs = api;
        return api;
    }

    const buttons = Array.from(doc.querySelectorAll<TabButton>('[data-dashboard-tab]'));
    const contents = Array.from(doc.querySelectorAll<TabContentElement>('[data-tab-content]'));

    const api = createTabsApi(owner, buttons, contents);
    owner.DashboardTabs = api;
    return api;
}

export type { DashboardTabsApi };
