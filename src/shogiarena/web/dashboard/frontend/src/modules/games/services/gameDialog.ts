import type { DashboardNavigationApi } from '@/types/globals';
import type { GamesServiceContext } from './context';
import type { ScheduleController } from './schedule';
import { byId } from '../utils/dom';

interface InstancesFocusOptions {
    readonly activateTab?: boolean;
}

export interface GameDialogController {
    openGameDialog: (gameId: string) => void;
    closeGameDialog: () => void;
    openInstancesForSelectedGame: () => void;
    openLiveViewForSelectedGame: () => void;
    activateInstancePill: (element: HTMLElement) => void;
    focusInstances: (instances: readonly string[]) => void;
}

export interface GameDialogControllerDependencies {
    readonly context: GamesServiceContext;
    readonly schedule: ScheduleController;
}

export function createGameDialogController({
    context,
    schedule,
}: GameDialogControllerDependencies): GameDialogController {
    const { state, utils, owner } = context;

    function focusInstances(instances: readonly string[], options: InstancesFocusOptions = {}): void {
        const ids = instances
            .map((value) => String(value ?? '').trim())
            .filter((value): value is string => value.length > 0);
        if (!ids.length) return;
        const navigation = owner.DashboardNavigation as DashboardNavigationApi | undefined;
        if (options.activateTab !== false) {
            navigation?.openTab?.('instances');
        }
        const instancesApi = owner.DashboardInstances;
        if (instancesApi?.focusInstances) {
            instancesApi.focusInstances(ids);
            return;
        }
        if (instancesApi?.focusInstance) {
            ids.forEach((id, index) => {
                owner.setTimeout(() => instancesApi.focusInstance(id), index * 150);
            });
        }
    }

    function fillModalSide(
        el: HTMLElement | null,
        name: string | null,
        instanceName: string | null,
        kind: string | null,
    ): void {
        if (!el) return;
        el.innerHTML = '';
        const nameEl = document.createElement('div');
        nameEl.className = 'games-modal__player';
        nameEl.textContent = name || '-';
        el.appendChild(nameEl);
        if (instanceName) {
            const metaEl = document.createElement('div');
            metaEl.className = 'games-modal__player-instance';
            const parts = [instanceName];
            if (kind) {
                parts.push(kind);
            }
            metaEl.textContent = parts.join(' · ');
            el.appendChild(metaEl);
        }
    }

    function openGameDialog(gameId: string): void {
        if (!gameId) return;
        const row = state.rowsByGame.get(gameId);
        if (!row) return;

        const dialog = byId<HTMLDialogElement>('gamesDialog');
        if (!dialog) return;

        state.selectedGameId = gameId;

        const titleEl = byId<HTMLElement>('gamesDialogTitle');
        if (titleEl) {
            titleEl.textContent = row.game_id || 'Game details';
        }

        const metaEl = byId<HTMLElement>('gamesDialogMeta');
        if (metaEl) {
            const metaParts = [] as string[];
            if (typeof row.round_index === 'number') {
                metaParts.push(`Round ${row.round_index + 1}`);
            }
            if (typeof row.order_index === 'number') {
                metaParts.push(`Position ${row.order_index + 1}`);
            }
            metaParts.push(`Status: ${row.status}`);
            metaEl.textContent = metaParts.join(' · ');
        }

        const statusEl = byId<HTMLElement>('gamesDialogStatus');
        if (statusEl) statusEl.textContent = row.status || '-';

        fillModalSide(byId<HTMLElement>('gamesDialogBlack'), row.black, row.black_instance, row.black_instance_kind);
        fillModalSide(byId<HTMLElement>('gamesDialogWhite'), row.white, row.white_instance, row.white_instance_kind);

        const sfenEl = byId<HTMLElement>('gamesDialogSfen');
        if (sfenEl) sfenEl.textContent = row.initial_sfen || '-';

        const liveBtn = byId<HTMLButtonElement>('gamesDialogLiveBtn');
        if (liveBtn) {
            const disabled = !utils.canOpenLiveView(row.status);
            liveBtn.disabled = disabled;
            liveBtn.setAttribute('aria-disabled', disabled ? 'true' : 'false');
        }

        if (!dialog.open) {
            dialog.showModal();
        } else {
            dialog.focus();
        }
    }

    function closeGameDialog(): void {
        const dialog = byId<HTMLDialogElement>('gamesDialog');
        if (dialog?.open) {
            dialog.close();
        }
        state.selectedGameId = null;
    }

    function openInstancesForSelectedGame(): void {
        const gameId = state.selectedGameId;
        if (!gameId) return;
        const row = state.rowsByGame.get(gameId);
        if (!row) return;
        const targets = row.instances.filter(Boolean);
        if (!targets.length) return;
        closeGameDialog();
        focusInstances(targets);
    }

    function openLiveViewForSelectedGame(): void {
        const gameId = state.selectedGameId;
        if (!gameId) {
            closeGameDialog();
            return;
        }
        schedule.openGameInLiveView(gameId);
        closeGameDialog();
    }

    function activateInstancePill(element: HTMLElement): void {
        const instanceId = element.dataset.instanceId;
        if (instanceId) {
            schedule.applyInstanceFilter(instanceId, { activateTab: false });
        }
    }

    return {
        openGameDialog,
        closeGameDialog,
        openInstancesForSelectedGame,
        openLiveViewForSelectedGame,
        activateInstancePill,
        focusInstances,
    } satisfies GameDialogController;
}
