import { escapeHtml } from '@/modules/shared/utils/html';
const CORRELATION_CONTAINER_ID = 'correlationContainer';
export const PARAMETER_DETAIL_CONTAINER_ID = 'parameterEvolutionChart';
export const PARAMETER_DETAIL_TITLE_ID = 'parameterDetailTitle';

export type CorrelationLayout = {
    tableHost: HTMLElement;
};

const correlationLayoutCache = new WeakMap<HTMLElement, CorrelationLayout>();

export function getCorrelationContainer(): HTMLElement | null {
    const node = document.getElementById(CORRELATION_CONTAINER_ID);
    return node instanceof HTMLElement ? node : null;
}

export function ensureCorrelationLayout(): CorrelationLayout | null {
    const container = getCorrelationContainer();
    if (!container) return null;
    const existing = correlationLayoutCache.get(container);
    if (existing?.tableHost?.isConnected) {
        return existing;
    }

    container.innerHTML = `
        <div class="parameter-table-wrapper">
            <div class="table-scroll" data-role="parameter-table">
                <p class="loading">Loading parameter analytics...</p>
            </div>
        </div>
    `;

    const tableHost = container.querySelector<HTMLElement>('[data-role="parameter-table"]');
    if (!tableHost) return null;

    const layout: CorrelationLayout = { tableHost };
    correlationLayoutCache.set(container, layout);
    return layout;
}

export function resetParameterDetail(message: string): void {
    const detailContainer = document.getElementById(PARAMETER_DETAIL_CONTAINER_ID);
    if (detailContainer) {
        detailContainer.innerHTML = `<p class="muted">${escapeHtml(message)}</p>`;
    }
    const title = document.getElementById(PARAMETER_DETAIL_TITLE_ID);
    if (title) {
        title.textContent = 'Parameter Detail';
    }
}

export function renderCorrelationLoadingLayout(): void {
    const layout = ensureCorrelationLayout();
    if (!layout) return;
    layout.tableHost.innerHTML = '<p class="loading">Loading parameter analytics...</p>';
    resetParameterDetail('Loading parameter analytics...');
}

export function renderCorrelationErrorLayout(message: string): void {
    const layout = ensureCorrelationLayout();
    if (!layout) return;
    layout.tableHost.innerHTML = `<p class="error">${escapeHtml(message)}</p>`;
    resetParameterDetail(message);
}
