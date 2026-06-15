import type { DashboardGamesApi, GamesSortKey } from '@/modules/games/types';
import type { GamesServiceContext } from './context';
import type { AssignmentController } from './assignments';
import type { GameDialogController } from './game-dialog';
import type { ScheduleController } from './schedule';
import { byId } from '../utils/dom';

export interface DomBindingsController {
    initialize: (api: DashboardGamesApi) => void;
}

interface DomBindingsDependencies {
    readonly context: GamesServiceContext;
    readonly schedule: ScheduleController;
    readonly assignment: AssignmentController;
    readonly dialog: GameDialogController;
}

export function createDomBindings({
    context,
    schedule,
    assignment,
    dialog,
}: DomBindingsDependencies): DomBindingsController {
    const { owner, state } = context;
    let initialized = false;
    let actionsDialog: HTMLDialogElement | null = null;
    let actionsTitle: HTMLElement | null = null;
    let actionsMeta: HTMLElement | null = null;
    let actionsAssignBtn: HTMLButtonElement | null = null;
    let actionsCancelBtn: HTMLButtonElement | null = null;
    let actionsRestoreBtn: HTMLButtonElement | null = null;
    let actionsCloseBtn: HTMLButtonElement | null = null;
    let currentActionsGameId: string | null = null;
    let lastActionsTrigger: HTMLButtonElement | null = null;
    let pendingCloseReason: 'default' | 'assign' | 'cancel' | 'restore' = 'default';

    function bindControls(): void {
        const instanceFilterSelect = byId<HTMLSelectElement>('gamesInstanceFilter');
        if (instanceFilterSelect) {
            instanceFilterSelect.addEventListener('change', () => {
                const value = instanceFilterSelect.value;
                schedule.applyInstanceFilter(value || null, { activateTab: false });
            });
        }

        const clearFilterBtn = byId<HTMLButtonElement>('gamesClearInstanceFilter');
        if (clearFilterBtn) {
            clearFilterBtn.addEventListener('click', () => {
                schedule.clearInstanceFilter({ activateTab: false });
            });
        }

        const searchInput = byId<HTMLInputElement>('gamesFilterInput');
        if (searchInput) {
            searchInput.value = state.searchQuery;
            searchInput.addEventListener('input', () => {
                schedule.setSearchQuery(searchInput.value);
            });
            searchInput.addEventListener('keydown', (event: KeyboardEvent) => {
                if (event.key === 'Escape') {
                    searchInput.value = '';
                    schedule.setSearchQuery('');
                    event.preventDefault();
                }
            });
        }

        document.querySelectorAll<HTMLElement>('.games-sort').forEach((btn) => {
            btn.addEventListener('click', () => {
                const key = btn.dataset.sort as GamesSortKey | undefined;
                if (key) {
                    schedule.setSort(key);
                }
            });
        });

        assignment.initializeDom();
        schedule.updateSortHeaders();
    }

    function finalizeActionsDialogClose(): void {
        const shouldRestoreFocus = pendingCloseReason !== 'assign';
        if (shouldRestoreFocus && lastActionsTrigger) {
            lastActionsTrigger.focus();
        }
        pendingCloseReason = 'default';
        currentActionsGameId = null;
        lastActionsTrigger = null;
        if (actionsCancelBtn) {
            actionsCancelBtn.disabled = false;
        }
        if (actionsRestoreBtn) {
            actionsRestoreBtn.disabled = false;
        }
    }

    function closeActionsDialog(reason: 'default' | 'assign' | 'cancel' | 'restore' = 'default'): void {
        const dialogEl = actionsDialog;
        if (!dialogEl) return;
        pendingCloseReason = reason;
        if (dialogEl.open) {
            dialogEl.close();
            return;
        }
        finalizeActionsDialogClose();
    }

    function openActionsDialog(trigger: HTMLButtonElement): void {
        const dialogEl = actionsDialog;
        if (!dialogEl || typeof dialogEl.showModal !== 'function') return;
        const gameId = trigger.dataset.gameId || '';
        if (!gameId) return;

        currentActionsGameId = gameId;
        lastActionsTrigger = trigger;

        const rowsMap = schedule.state?.rowsByGame;
        const row = rowsMap instanceof Map ? (rowsMap.get(gameId) ?? null) : null;
        const displayOrder = trigger.dataset.displayOrder;
        const statusHint = (row?.status || trigger.dataset.status || '').toLowerCase();

        if (actionsTitle) {
            if (displayOrder) {
                actionsTitle.textContent = `Game #${displayOrder}`;
            } else {
                actionsTitle.textContent = `Game ${gameId}`;
            }
        }

        if (actionsMeta) {
            const showdown = [
                trigger.dataset.black || (row?.black ?? ''),
                trigger.dataset.white || (row?.white ?? ''),
            ].filter(Boolean);
            const labelParts: string[] = [];
            if (showdown.length === 2) {
                labelParts.push(`${showdown[0]} vs ${showdown[1]}`);
            } else if (showdown.length === 1) {
                labelParts.push(showdown[0]);
            }
            labelParts.push(gameId);
            actionsMeta.textContent = labelParts.join(' · ');
        }

        const allowCancel = statusHint === 'pending';
        const allowRestore = statusHint === 'cancelled';
        const allowAssign = statusHint === 'pending';

        const configureActionButton = (btn: HTMLButtonElement | null, allow: boolean) => {
            if (!btn) return;
            btn.disabled = !allow;
            btn.style.display = allow ? '' : 'none';
            btn.setAttribute('aria-hidden', allow ? 'false' : 'true');
            btn.tabIndex = allow ? 0 : -1;
        };

        configureActionButton(actionsCancelBtn, allowCancel);
        configureActionButton(actionsRestoreBtn, allowRestore);
        configureActionButton(actionsAssignBtn, allowAssign);

        dialogEl.showModal();
        const focusCandidates: HTMLButtonElement[] = [];
        if (actionsAssignBtn && actionsAssignBtn.style.display !== 'none') focusCandidates.push(actionsAssignBtn);
        if (actionsRestoreBtn && actionsRestoreBtn.style.display !== 'none') focusCandidates.push(actionsRestoreBtn);
        if (actionsCancelBtn && actionsCancelBtn.style.display !== 'none') focusCandidates.push(actionsCancelBtn);

        const focusTarget = focusCandidates[0] ?? actionsCloseBtn ?? null;
        focusTarget?.focus();
    }

    function handleTableClick(event: MouseEvent): void {
        const rawTarget = event.target as Element | null;
        if (!rawTarget) return;
        const interactiveTarget =
            (rawTarget.closest(
                '.games-settings-trigger, .games-game-link, .games-engine-link, .games-instance-pill',
            ) as HTMLElement | null) || (rawTarget as HTMLElement);

        const actionsTrigger = interactiveTarget.closest('.games-settings-trigger') as HTMLButtonElement | null;
        if (actionsTrigger) {
            event.preventDefault();
            event.stopPropagation();
            openActionsDialog(actionsTrigger);
            return;
        }

        const gameLink = interactiveTarget.closest('.games-game-link') as HTMLElement | null;
        if (gameLink) {
            const gameId = gameLink.dataset.gameId || gameLink.textContent || '';
            if (gameId) {
                const status = gameLink.dataset.status || '';
                schedule.openGameInLiveView(gameId, { source: 'games', status });
            }
            return;
        }

        const engineLink = interactiveTarget.closest('.games-engine-link') as HTMLElement | null;
        if (engineLink) {
            const engineName = engineLink.dataset.engineName || engineLink.textContent || '';
            schedule.navigateToEngineMatchups(engineName);
            return;
        }

        const instanceLink = interactiveTarget.closest('.games-instance-pill') as HTMLElement | null;
        if (instanceLink) {
            dialog.activateInstancePill(instanceLink);
        }
    }

    function handleAssignAction(): void {
        if (!currentActionsGameId) return;
        const rowsMap = schedule.state?.rowsByGame;
        const row = rowsMap instanceof Map ? (rowsMap.get(currentActionsGameId) ?? null) : null;
        const dialogState = row
            ? {
                  mode: row.assigned_mode,
                  shared:
                      row.assigned_mode === 'shared'
                          ? (row.assigned_override_black ?? row.assigned_override ?? null)
                          : null,
                  black: row.assigned_mode === 'per_color' ? (row.assigned_override_black ?? null) : null,
                  white: row.assigned_mode === 'per_color' ? (row.assigned_override_white ?? null) : null,
                  requireInstall: row.should_require_install,
              }
            : undefined;
        closeActionsDialog('assign');
        assignment.openDialog(currentActionsGameId, dialogState);
    }

    async function handleCancelAction(): Promise<void> {
        if (!currentActionsGameId || !actionsCancelBtn) return;
        await assignment.cancelGame(currentActionsGameId, actionsCancelBtn);
        closeActionsDialog('cancel');
    }

    async function handleRestoreAction(): Promise<void> {
        if (!currentActionsGameId || !actionsRestoreBtn) return;
        await assignment.restoreGame(currentActionsGameId, actionsRestoreBtn);
        closeActionsDialog('restore');
    }

    function handleTableKeydown(event: KeyboardEvent): void {
        const target = event.target as Element | null;
        if (!target) return;
        if (target.classList.contains('games-instance-pill')) {
            if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                dialog.activateInstancePill(target as HTMLElement);
            }
            return;
        }
    }

    function initialize(api: DashboardGamesApi): void {
        if (initialized) {
            return;
        }
        initialized = true;

        bindControls();
        const table = byId<HTMLElement>('gamesTable');
        if (table) {
            table.addEventListener('click', handleTableClick);
            table.addEventListener('keydown', handleTableKeydown);
        }

        actionsDialog = byId<HTMLDialogElement>('gamesActionsDialog');
        actionsTitle = byId<HTMLElement>('gamesActionsTitle');
        actionsMeta = byId<HTMLElement>('gamesActionsMeta');
        actionsAssignBtn = byId<HTMLButtonElement>('gamesActionsAssign');
        actionsCancelBtn = byId<HTMLButtonElement>('gamesActionsCancel');
        actionsRestoreBtn = byId<HTMLButtonElement>('gamesActionsRestore');
        actionsCloseBtn = byId<HTMLButtonElement>('gamesActionsClose');

        if (actionsAssignBtn) {
            actionsAssignBtn.addEventListener('click', () => {
                handleAssignAction();
            });
        }
        if (actionsCancelBtn) {
            actionsCancelBtn.addEventListener('click', () => {
                void handleCancelAction();
            });
        }
        if (actionsRestoreBtn) {
            actionsRestoreBtn.addEventListener('click', () => {
                void handleRestoreAction();
            });
        }
        if (actionsCloseBtn) {
            actionsCloseBtn.addEventListener('click', () => closeActionsDialog());
        }
        if (actionsDialog) {
            actionsDialog.addEventListener('cancel', (event) => {
                event.preventDefault();
                closeActionsDialog();
            });
            actionsDialog.addEventListener('close', () => {
                finalizeActionsDialogClose();
            });
        }

        const closeBtn = byId<HTMLButtonElement>('gamesDialogClose');
        if (closeBtn) {
            closeBtn.addEventListener('click', () => dialog.closeGameDialog());
        }

        const instancesBtn = byId<HTMLButtonElement>('gamesDialogInstancesBtn');
        if (instancesBtn) {
            instancesBtn.addEventListener('click', () => dialog.openInstancesForSelectedGame());
        }

        const liveBtn = byId<HTMLButtonElement>('gamesDialogLiveBtn');
        if (liveBtn) {
            liveBtn.addEventListener('click', () => dialog.openLiveViewForSelectedGame());
        }

        const gameDialog = byId<HTMLDialogElement>('gamesDialog');
        if (gameDialog) {
            gameDialog.addEventListener('cancel', () => {
                state.selectedGameId = null;
            });
            gameDialog.addEventListener('close', () => {
                state.selectedGameId = null;
            });
        }

        const tabsApi = owner?.DashboardTabs;
        if (tabsApi?.getActive?.() === 'games') {
            api.setActive(true);
        }
    }

    return {
        initialize,
    } satisfies DomBindingsController;
}
