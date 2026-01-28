import { escapeHtml } from '@/modules/shared/utils/html';

const ERROR_CONTAINER_ID = 'errorContainer';

function getContainer(): HTMLElement | null {
    return document.getElementById(ERROR_CONTAINER_ID);
}

export function showError(message: string): void {
    const container = getContainer();
    if (!container) return;
    const safe = escapeHtml(message);
    container.innerHTML = `<p class="error">${safe}</p>`;
}

export function clearError(): void {
    const container = getContainer();
    if (!container) return;
    container.innerHTML = '';
}
