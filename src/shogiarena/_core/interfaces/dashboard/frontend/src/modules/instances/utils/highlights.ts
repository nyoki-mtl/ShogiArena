import type { InstancesDashboardState } from '@/modules/instances/state';

function findInstanceCard(root: HTMLElement | null, id: string): HTMLElement | null {
    if (!root || !id) return null;
    const escapeId = typeof CSS !== 'undefined' && typeof CSS.escape === 'function' ? CSS.escape(id) : id;
    return root.querySelector(`.inst-card[data-inst-id="${escapeId}"]`);
}

export function applyActiveHighlights(state: InstancesDashboardState): void {
    if (!state.activeHighlights.size) return;
    const grid = document.getElementById('instancesGrid');
    if (!grid) {
        throw new Error('Instances dashboard requires #instancesGrid element for highlights');
    }
    for (const id of state.activeHighlights) {
        const card = findInstanceCard(grid, id);
        if (card) {
            card.classList.add('inst-card--highlight');
        }
    }
}

export function processHighlightQueue(state: InstancesDashboardState): void {
    if (!state.highlightQueue.size) return;
    const grid = document.getElementById('instancesGrid');
    if (!grid) {
        throw new Error('Instances dashboard requires #instancesGrid element for highlight queue');
    }
    const remaining = new Set<string>();
    let scrolled = false;
    for (const id of state.highlightQueue) {
        const card = findInstanceCard(grid, id);
        if (!card) {
            remaining.add(id);
            continue;
        }
        card.classList.add('inst-card--highlight');
        if (!state.activeHighlights.has(id)) {
            state.activeHighlights.add(id);
        }
        if (!scrolled) {
            try {
                card.scrollIntoView({ behavior: 'smooth', block: 'center' });
            } catch (_error) {
                card.scrollIntoView();
            }
            scrolled = true;
        }
        if (state.highlightTimers.has(id)) {
            clearTimeout(state.highlightTimers.get(id));
        }
        const timer = setTimeout(() => {
            const gridEl = document.getElementById('instancesGrid');
            const cardEl = findInstanceCard(gridEl, id);
            if (cardEl) {
                cardEl.classList.remove('inst-card--highlight');
            }
            state.highlightTimers.delete(id);
            state.activeHighlights.delete(id);
        }, 4000);
        state.highlightTimers.set(id, timer);
    }
    state.highlightQueue = remaining;
}

/** 保留中の highlight 解除タイマーをすべて取り消し、ハイライト状態をリセットする。 */
export function clearHighlightTimers(state: InstancesDashboardState): void {
    for (const timer of state.highlightTimers.values()) {
        clearTimeout(timer);
    }
    state.highlightTimers.clear();
    state.activeHighlights.clear();
    state.highlightQueue.clear();
}

export function queueInstanceHighlights(state: InstancesDashboardState, ids: readonly string[] | string): void {
    const list = Array.isArray(ids) ? ids : [ids];
    const cleaned = list.filter((value): value is string => typeof value === 'string' && value.length > 0);
    if (!cleaned.length) return;
    for (const id of cleaned) {
        state.highlightQueue.add(id);
    }
}
