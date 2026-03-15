import type { AssignmentMode } from '@/modules/games/types';
import type { MutableJsonObject } from '@/types/shared';
import type { GamesServiceContext } from './context';
import type { ScheduleController } from './schedule';
import { byId } from '../utils/dom';

interface AssignmentDialogState {
    readonly mode: AssignmentMode;
    readonly shared: string | null;
    readonly black: string | null;
    readonly white: string | null;
    readonly requireInstall: boolean;
}

export interface AssignmentController {
    initializeDom: () => void;
    openDialog: (gameId: string, state?: Partial<AssignmentDialogState>) => void;
    closeDialog: () => void;
    submit: () => Promise<void>;
    cancelGame: (gameId: string, button?: HTMLButtonElement | null) => Promise<void>;
    restoreGame: (gameId: string, button?: HTMLButtonElement | null) => Promise<void>;
}

export interface AssignmentControllerDependencies {
    readonly context: GamesServiceContext;
    readonly schedule: ScheduleController;
}

function normalizeInstance(value: string | null | undefined): string | null {
    if (value == null) return null;
    const trimmed = value.trim();
    return trimmed.length > 0 ? trimmed : null;
}

function resolveDialogMode(value: AssignmentMode | undefined): AssignmentMode {
    return value === 'shared' || value === 'per_color' ? value : 'auto';
}

export function createAssignmentController({
    context,
    schedule,
}: AssignmentControllerDependencies): AssignmentController {
    const { state, utils } = context;

    let assignDialog: HTMLDialogElement | null = null;
    let perColorToggle: HTMLInputElement | null = null;
    let assignSharedSelect: HTMLSelectElement | null = null;
    let assignBlackSelect: HTMLSelectElement | null = null;
    let assignWhiteSelect: HTMLSelectElement | null = null;
    let assignInstallCheckbox: HTMLInputElement | null = null;
    let assignSubmitButton: HTMLButtonElement | null = null;
    let sharedGroup: HTMLElement | null = null;
    let perColorGroup: HTMLElement | null = null;

    function requireAssignDialog(): HTMLDialogElement {
        if (!assignDialog) {
            throw new Error('assignInstanceDialog element is unavailable');
        }
        return assignDialog;
    }

    function perColorEnabled(): boolean {
        return Boolean(perColorToggle?.checked);
    }

    function setPerColorEnabled(enabled: boolean): void {
        if (perColorToggle) {
            perColorToggle.checked = enabled;
        }
        updatePerColorVisibility();
    }

    function updatePerColorVisibility(): void {
        const enabled = perColorEnabled();
        if (perColorGroup) perColorGroup.hidden = !enabled;
        if (assignSharedSelect) assignSharedSelect.disabled = enabled;
        if (assignBlackSelect) {
            assignBlackSelect.disabled = !enabled;
            if (!enabled) assignBlackSelect.value = '';
        }
        if (assignWhiteSelect) {
            assignWhiteSelect.disabled = !enabled;
            if (!enabled) assignWhiteSelect.value = '';
        }
        if (sharedGroup) sharedGroup.hidden = false;
    }

    function populateSelect(select: HTMLSelectElement | null): void {
        if (!select) return;
        select.innerHTML = '';
        const autoOption = document.createElement('option');
        autoOption.value = '';
        autoOption.textContent = 'Auto (default)';
        select.appendChild(autoOption);

        state.instanceOptions
            .slice()
            .sort((a, b) => a.localeCompare(b))
            .forEach((value) => {
                const option = document.createElement('option');
                option.value = value;
                option.textContent = value;
                select.appendChild(option);
            });
    }

    function closeDialog(): void {
        if (assignDialog && typeof assignDialog.close === 'function') {
            assignDialog.close();
        }
        state.assignmentGameId = null;
    }

    function openDialog(gameId: string, dialogState?: Partial<AssignmentDialogState>): void {
        const dialog = requireAssignDialog();
        state.assignmentGameId = gameId;
        if (typeof dialog.showModal !== 'function') {
            throw new Error('assignInstanceDialog does not support showModal');
        }

        populateSelect(assignSharedSelect);
        populateSelect(assignBlackSelect);
        populateSelect(assignWhiteSelect);

        const initialMode = resolveDialogMode(dialogState?.mode);
        const usePerColor = initialMode === 'per_color';
        setPerColorEnabled(usePerColor);

        if (assignSharedSelect) {
            assignSharedSelect.value = dialogState?.shared ?? '';
        }
        if (usePerColor) {
            if (assignBlackSelect) assignBlackSelect.value = dialogState?.black ?? '';
            if (assignWhiteSelect) assignWhiteSelect.value = dialogState?.white ?? '';
        }
        if (assignInstallCheckbox) {
            assignInstallCheckbox.checked = Boolean(dialogState?.requireInstall);
        }

        dialog.showModal();
        const focusTarget = usePerColor ? (assignBlackSelect ?? assignSharedSelect) : assignSharedSelect;
        focusTarget?.focus();
    }

    async function submit(): Promise<void> {
        if (!state.assignmentGameId) {
            throw new Error('No game is selected for instance assignment');
        }
        const mode: AssignmentMode = perColorEnabled() ? 'per_color' : 'shared';
        const gameId = state.assignmentGameId;
        const payload: MutableJsonObject = { mode };

        if (mode === 'shared') {
            const sharedValue = normalizeInstance(assignSharedSelect?.value ?? null);
            payload.shared_instance = sharedValue;
            payload.instance_id = sharedValue;
        } else {
            payload.black_instance_id = normalizeInstance(assignBlackSelect?.value ?? null);
            payload.white_instance_id = normalizeInstance(assignWhiteSelect?.value ?? null);
            const sharedValue = normalizeInstance(assignSharedSelect?.value ?? null);
            if (sharedValue !== null) {
                payload.shared_instance = sharedValue;
            }
        }

        payload.should_require_install = assignInstallCheckbox?.checked ?? false;

        try {
            if (assignSubmitButton) assignSubmitButton.disabled = true;
            await utils.requestJson(`${utils.apiBase()}/api/schedule/${encodeURIComponent(gameId)}/assign`, {
                method: 'POST',
                body: payload,
            });

            if (mode === 'per_color') {
                const blackLabel = assignBlackSelect?.value || 'auto';
                const whiteLabel = assignWhiteSelect?.value || 'auto';
                utils.showNotice(`Assigned game ${gameId} (B:${blackLabel} / W:${whiteLabel})`, 'info');
            } else {
                const label = assignSharedSelect?.value;
                utils.showNotice(
                    label && label.trim().length > 0
                        ? `Assigned game ${gameId} to instance ${label}`
                        : `Cleared instance assignment for game ${gameId}`,
                    'info',
                );
            }

            closeDialog();
            await schedule.fetchGames(true);
        } catch (err) {
            utils.showNotice((err as Error)?.message || `Failed to assign instance for game ${gameId}`, 'error');
        } finally {
            if (assignSubmitButton) assignSubmitButton.disabled = false;
        }
    }

    async function cancelGame(gameId: string, button?: HTMLButtonElement | null): Promise<void> {
        if (!gameId) return;
        const targetButton = button || null;
        try {
            if (targetButton) targetButton.disabled = true;
            await utils.requestJson(`${utils.apiBase()}/api/schedule/${encodeURIComponent(gameId)}/cancel`, {
                method: 'POST',
            });
            utils.showNotice(`Cancelled game ${gameId}`, 'info');
            await schedule.fetchGames(true);
        } catch (err) {
            utils.showNotice((err as Error)?.message || `Failed to cancel game ${gameId}`, 'error');
        } finally {
            if (targetButton) targetButton.disabled = false;
        }
    }

    async function restoreGame(gameId: string, button?: HTMLButtonElement | null): Promise<void> {
        if (!gameId) return;
        const targetButton = button || null;
        try {
            if (targetButton) targetButton.disabled = true;
            await utils.requestJson(`${utils.apiBase()}/api/schedule/${encodeURIComponent(gameId)}/restore`, {
                method: 'POST',
            });
            utils.showNotice(`Restored game ${gameId}`, 'info');
            await schedule.fetchGames(true);
        } catch (err) {
            utils.showNotice((err as Error)?.message || `Failed to restore game ${gameId}`, 'error');
        } finally {
            if (targetButton) targetButton.disabled = false;
        }
    }

    function initializeDom(): void {
        assignDialog = byId<HTMLDialogElement>('assignInstanceDialog');
        perColorToggle = byId<HTMLInputElement>('assignInstancePerColorToggle');
        assignSharedSelect = byId<HTMLSelectElement>('assignInstanceSelect');
        assignBlackSelect = byId<HTMLSelectElement>('assignInstanceBlackSelect');
        assignWhiteSelect = byId<HTMLSelectElement>('assignInstanceWhiteSelect');
        assignInstallCheckbox = byId<HTMLInputElement>('assignInstanceInstall');
        assignSubmitButton = byId<HTMLButtonElement>('assignInstanceSubmit');
        sharedGroup = assignDialog?.querySelector<HTMLElement>('[data-assignment="shared"]') ?? null;
        perColorGroup = assignDialog?.querySelector<HTMLElement>('[data-assignment="per_color"]') ?? null;

        const assignCancelBtn = byId<HTMLButtonElement>('assignInstanceCancel');
        const assignForm = byId<HTMLFormElement>('assignInstanceForm');

        if (assignForm) {
            assignForm.addEventListener('submit', (event) => {
                event.preventDefault();
                void submit();
            });
        }
        if (assignCancelBtn) {
            assignCancelBtn.addEventListener('click', () => {
                closeDialog();
            });
        }
        if (assignDialog) {
            assignDialog.addEventListener('cancel', () => {
                state.assignmentGameId = null;
            });
            assignDialog.addEventListener('close', () => {
                state.assignmentGameId = null;
            });
        }
        if (perColorToggle) {
            perColorToggle.addEventListener('change', () => {
                updatePerColorVisibility();
            });
        }
        updatePerColorVisibility();
    }

    return {
        initializeDom,
        openDialog,
        closeDialog,
        submit,
        cancelGame,
        restoreGame,
    } satisfies AssignmentController;
}
