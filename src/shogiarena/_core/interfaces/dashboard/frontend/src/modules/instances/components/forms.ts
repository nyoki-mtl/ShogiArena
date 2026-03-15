import type { InstanceFormElements, InstanceRecord } from '@/modules/instances/types';
import { apiBase, cancelPendingGames, postInstanceAction, requestJson } from '@/modules/instances/services/api';
import { handleApiError, showNotice } from '@/modules/instances/utils/helpers';
import type { InstancesDashboardState } from '@/modules/instances/state';
import type { MutableJsonObject } from '@/types/shared';

interface FormSubmitPayload extends MutableJsonObject {
    readonly name: string;
    readonly type: string;
    readonly slots: number;
    readonly tags: readonly string[];
    readonly host?: string;
    readonly user?: string;
    readonly port?: number;
    readonly identity_file?: string;
    readonly project_root?: string;
    readonly is_strict_host_key_checking?: boolean;
}

export interface FormHandlers {
    readonly openForm: (mode: 'create' | 'edit', instance?: InstanceRecord) => void;
    readonly promptProvision: (instance: InstanceRecord) => Promise<void>;
    readonly showDrainConfirmation: (instance: InstanceRecord) => void;
}

function updateFormVisibility(state: InstancesDashboardState): void {
    const elements = requireFormElements(state);
    const type = elements.type.value;
    if (elements.sshFields) {
        if (type === 'ssh') {
            elements.sshFields.classList.remove('hidden');
        } else {
            elements.sshFields.classList.add('hidden');
        }
    }
}

function resetInstanceForm(state: InstancesDashboardState): void {
    const elements = requireFormElements(state);
    elements.form.reset();
    if (elements.title) {
        elements.title.textContent = 'Add Instance';
    }
    elements.name.disabled = false;
    elements.type.disabled = false;
    elements.type.value = 'local';
    elements.slots.value = '1';
    elements.port.value = '22';
    if (elements.strict) {
        elements.strict.checked = false;
    }
    elements.tags.value = '';
    updateFormVisibility(state);
}

function populateFormForEdit(state: InstancesDashboardState, inst: InstanceRecord): void {
    const elements = requireFormElements(state);
    const cfg = inst.config || {};
    if (elements.title) {
        elements.title.textContent = `Edit Instance: ${inst.id}`;
    }
    elements.name.value = inst.id || cfg.name || '';
    elements.name.disabled = true;
    if (inst.type) {
        elements.type.value = inst.type;
    }
    if (cfg.slots != null) {
        elements.slots.value = String(cfg.slots);
    }
    elements.host.value = cfg.host || '';
    elements.user.value = cfg.user || '';
    elements.port.value = cfg.port != null ? String(cfg.port) : '22';
    elements.identity.value = cfg.identity_file || '';
    elements.projectRoot.value = cfg.project_root || '';
    if (elements.strict) {
        elements.strict.checked = Boolean(cfg.is_strict_host_key_checking);
    }
    if (Array.isArray(cfg.tags) && cfg.tags.length) {
        elements.tags.value = cfg.tags.join(', ');
    } else {
        elements.tags.value = '';
    }
    updateFormVisibility(state);
}

function convertFormPayload(state: InstancesDashboardState): FormSubmitPayload | null {
    const elements = requireFormElements(state);
    const name = elements.name.value.trim();
    const type = elements.type.value;
    const slotsValue = elements.slots.value.trim();
    if (!name) {
        handleApiError(new Error('name is required'));
        return null;
    }
    if (!type) {
        handleApiError(new Error('type is required'));
        return null;
    }
    if (!slotsValue) {
        handleApiError(new Error('slots is required'));
        return null;
    }
    const slots = Number(slotsValue);
    if (!Number.isFinite(slots) || slots < 0) {
        handleApiError(new Error('slots must be >= 0 (0 means auto)'));
        return null;
    }
    const basePayload: FormSubmitPayload = {
        name,
        type,
        slots,
        tags: elements.tags.value
            .split(',')
            .map((tag) => tag.trim())
            .filter((tag) => tag.length > 0),
    };

    if (type !== 'ssh') {
        return basePayload;
    }

    const host = elements.host.value.trim();
    const user = elements.user.value.trim();
    const port = Number(elements.port.value || 22);
    if (!host) {
        handleApiError(new Error('host is required for SSH instances'));
        return null;
    }
    if (!user) {
        handleApiError(new Error('user is required for SSH instances'));
        return null;
    }
    if (!Number.isFinite(port) || port <= 0) {
        handleApiError(new Error('port must be a positive number'));
        return null;
    }

    const identity = elements.identity.value.trim();
    const projectRoot = elements.projectRoot.value.trim();
    const strictOption = elements.strict ? { is_strict_host_key_checking: Boolean(elements.strict.checked) } : {};
    const identityOption = identity ? { identity_file: identity } : {};

    const sshPayload: FormSubmitPayload = {
        ...basePayload,
        host,
        user,
        port,
        project_root: projectRoot,
        ...identityOption,
        ...strictOption,
    };

    return sshPayload;
}

async function submitForm(state: InstancesDashboardState, refresh: (force: boolean) => Promise<void>): Promise<void> {
    const elements = requireFormElements(state);
    const payload = convertFormPayload(state);
    if (!payload) return;
    const isCreate = state.formMode === 'create';
    const targetId = isCreate ? null : state.editingId;
    const url = isCreate
        ? `${apiBase()}/api/instances`
        : `${apiBase()}/api/instances/${encodeURIComponent(targetId ?? '')}`;
    const method = isCreate ? 'POST' : 'PATCH';
    const submitButton = elements.submit ?? null;
    try {
        if (submitButton) submitButton.disabled = true;
        await requestJson(url, { method, body: payload });
        showNotice(isCreate ? 'Instance created' : 'Instance updated', 'info');
        closeForm(state);
        await refresh(true);
    } catch (error) {
        handleApiError(error, isCreate ? 'Failed to create instance' : 'Failed to update instance');
    } finally {
        if (submitButton) submitButton.disabled = false;
    }
}

function closeForm(state: InstancesDashboardState): void {
    const dialog = state.formDialog;
    if (!dialog) {
        throw new Error('Instances dashboard form dialog is not initialized');
    }
    dialog.close();
}

function openForm(state: InstancesDashboardState, mode: 'create' | 'edit', instance?: InstanceRecord): void {
    const dialog = state.formDialog;
    if (!dialog) {
        throw new Error('Instances dashboard form dialog is not initialized');
    }
    const elements = requireFormElements(state);
    state.formMode = mode;
    state.editingId = mode === 'edit' && instance ? instance.id : null;
    resetInstanceForm(state);
    if (mode === 'edit' && instance) {
        populateFormForEdit(state, instance);
    }
    dialog.showModal();
    if (mode === 'create') {
        elements.name.focus();
    } else {
        elements.slots.focus();
    }
}

async function promptProvision(state: InstancesDashboardState, instance: InstanceRecord): Promise<void> {
    const localInput = window.prompt(`Local path to sync to instance ${instance.id} (project-root relative)`, '');
    if (!localInput) return;
    const localPath = localInput.trim();
    if (!localPath) return;
    const cfg = instance.config || {};
    const suggestionRoot = cfg.engine_dir ? '{engine_dir}' : '{project_root}/data/engines';
    const tail = localPath.split(/[/\\]/).filter(Boolean).pop() || '';
    const remoteSuggestion = `${suggestionRoot}${tail ? `/${tail}` : ''}`;
    const remoteInput = window.prompt(
        'Remote directory (supports {engine_dir} / {project_root} placeholders)',
        remoteSuggestion,
    );
    if (!remoteInput) return;
    const remotePath = remoteInput.trim();
    if (!remotePath) return;
    try {
        const result = await postInstanceAction(state, instance.id, 'provision', {
            local_path: localPath,
            remote_path: remotePath,
            mode: 'dir',
        });
        if (result?.success) {
            showNotice(`Provision started (${instance.id})`, 'info');
        } else if (result?.error) {
            handleApiError(new Error(result.error));
        }
    } catch (error) {
        handleApiError(error, 'Provision failed');
    }
}

function showDrainConfirmation(
    state: InstancesDashboardState,
    instance: InstanceRecord,
    refresh: (force: boolean) => Promise<void>,
): void {
    const dialog = state.drainDialog;
    if (!dialog) {
        throw new Error('Instances dashboard drain confirmation dialog is not initialized');
    }
    const message = document.getElementById('drainConfirmMessage');
    if (!message) {
        throw new Error('Instances dashboard drain confirmation message element is missing');
    }
    const name = instance.id || instance.config?.name || 'this instance';
    message.textContent = `Instance ${name} is draining. Cancel pending games to free slots now; active games finish and the runner keeps waiting for a new schedule.`;
    state.drainInstanceId = instance.id;
    dialog.showModal();
    const cancelBtn = document.getElementById('drainConfirmCancel');
    const keepBtn = document.getElementById('drainConfirmKeep');
    if (!cancelBtn) {
        throw new Error('Instances dashboard drain confirmation cancel button is missing');
    }
    if (!keepBtn) {
        throw new Error('Instances dashboard drain confirmation keep button is missing');
    }
    cancelBtn.onclick = async () => {
        try {
            const result = await cancelPendingGames();
            const cancelled = result.cancelled_count ?? 0;
            const running = Array.isArray(result.running_games) ? result.running_games.length : 0;
            const scope = running > 0 ? ` (${running} in progress)` : '';
            const total = result.total_games ?? '-';
            showNotice(
                `Cancelled pending games (${cancelled})${scope}. Runner remains online waiting for a new schedule (total ${total}).`,
                'info',
            );
            await refresh(true);
        } catch (error) {
            handleApiError(error, 'Failed to cancel pending games');
        } finally {
            dialog.close();
            state.drainInstanceId = null;
        }
    };
    keepBtn.onclick = () => {
        dialog.close();
        state.drainInstanceId = null;
    };
}

export function initializeForms(
    state: InstancesDashboardState,
    refresh: (force: boolean) => Promise<void>,
): FormHandlers {
    const formDialog = document.getElementById('instanceFormDialog') as HTMLDialogElement | null;
    const form = document.getElementById('instanceForm') as HTMLFormElement | null;
    if (!formDialog) {
        throw new Error('Instances dashboard form dialog element is missing');
    }
    if (!form) {
        throw new Error('Instances dashboard form element is missing');
    }

    state.formDialog = formDialog;

    const type = form.querySelector('[name="instanceType"]') as HTMLSelectElement | null;
    const name = form.querySelector('[name="instanceName"]') as HTMLInputElement | null;
    const slots = form.querySelector('[name="instanceSlots"]') as HTMLInputElement | null;
    const host = form.querySelector('[name="instanceHost"]') as HTMLInputElement | null;
    const user = form.querySelector('[name="instanceUser"]') as HTMLInputElement | null;
    const port = form.querySelector('[name="instancePort"]') as HTMLInputElement | null;
    const identity = form.querySelector('[name="instanceIdentityFile"]') as HTMLInputElement | null;
    const projectRoot = form.querySelector('[name="instanceProjectRoot"]') as HTMLInputElement | null;
    const tags = form.querySelector('[name="instanceTags"]') as HTMLInputElement | null;

    if (!type || !name || !slots || !host || !user || !port || !identity || !projectRoot || !tags) {
        throw new Error('Instances dashboard form inputs are missing required fields');
    }

    state.formElements = {
        form,
        title: document.getElementById('instanceFormTitle'),
        type,
        name,
        slots,
        host,
        user,
        port,
        identity,
        projectRoot,
        tags,
        strict: form.querySelector('[name="instanceStrictHostKeyChecking"]') as HTMLInputElement,
        sshFields: form.querySelector('[data-role="ssh-fields"]') as HTMLElement,
        submit: document.getElementById('instanceFormSubmit') as HTMLButtonElement,
    } satisfies InstanceFormElements;

    state.formElements.type.addEventListener('change', () => updateFormVisibility(state));
    form.addEventListener('submit', (event) => {
        event.preventDefault();
        void submitForm(state, refresh);
    });

    const cancelBtn = form.querySelector('[data-action="cancel"]') as HTMLButtonElement | null;
    if (!cancelBtn) {
        throw new Error('Instances dashboard form cancel button is missing');
    }
    cancelBtn.addEventListener('click', () => {
        resetInstanceForm(state);
        closeForm(state);
    });
    updateFormVisibility(state);

    const drainDialog = document.getElementById('drainConfirmDialog') as HTMLDialogElement | null;
    if (!drainDialog) {
        throw new Error('Instances dashboard drain confirmation dialog element is missing');
    }
    state.drainDialog = drainDialog;

    return {
        openForm: (mode, instance) => openForm(state, mode, instance),
        promptProvision: (instance) => promptProvision(state, instance),
        showDrainConfirmation: (instance) => showDrainConfirmation(state, instance, refresh),
    };
}

function requireFormElements(state: InstancesDashboardState): InstanceFormElements {
    const elements = state.formElements;
    if (!elements) {
        throw new Error('Instances dashboard form elements are not initialized');
    }
    return elements;
}
