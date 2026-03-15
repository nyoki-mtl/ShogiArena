import type { LiveCardState } from '@/modules/live/types';
import type { GameMeta } from './metadata';

interface HeaderDeps {
    getCards: () => LiveCardState[];
    findCardBySource: (source: string) => LiveCardState | undefined;
    getGameMetadata: (gameId: string) => GameMeta | null;
    formatVariantDisplay: (variantId: string | null | undefined, phase: string | null | undefined) => string;
    workerLatestLabel: (workerIdx: number) => string;
}

export function createHeaderController(deps: HeaderDeps) {
    const { getCards, findCardBySource, getGameMetadata, formatVariantDisplay, workerLatestLabel } = deps;

    function resolveSourceDisplay(source: string): string {
        if (!source) return 'Unknown';
        if (source.startsWith('worker-latest:')) {
            const workerIdx = Number(source.split(':')[1]);
            if (Number.isFinite(workerIdx)) {
                return workerLatestLabel(workerIdx);
            }
        }
        if (source.startsWith('db-game:')) {
            const gameId = source.split(':')[1] || '';
            const meta = getGameMetadata(gameId);
            const variantLabel = formatVariantDisplay(meta?.variantId ?? null, meta?.phase ?? null);
            if (variantLabel) {
                return `Saved Game ${variantLabel}`;
            }
            return gameId ? `Saved Game ${gameId}` : 'Saved Game';
        }
        return source || 'Custom Source';
    }

    function updateCardHeaderTitle(cardState: LiveCardState | null | undefined): void {
        if (!cardState) return;
        const select = document.getElementById(`source-${cardState.id}`) as HTMLSelectElement | null;
        if (select) {
            const display = resolveSourceDisplay(cardState.source);
            select.title = display;
            const option = Array.from(select.options).find((opt) => opt.value === cardState.source);
            if (option) {
                option.title = display;
            }
        }
    }

    function updateWorkerOptionLabels(workerIdx: number | null = null): void {
        const selector =
            workerIdx == null
                ? 'select.source-select option[value^="worker-latest:"]'
                : `select.source-select option[value="worker-latest:${workerIdx}"]`;
        const options = document.querySelectorAll<HTMLOptionElement>(selector);
        options.forEach((opt) => {
            const [, idxRaw] = opt.value.split(':');
            const idx = Number(idxRaw);
            if (Number.isNaN(idx)) return;
            const label = workerLatestLabel(idx);
            opt.textContent = label;
            opt.title = label;
        });
        if (workerIdx != null) {
            const card = findCardBySource(`worker-latest:${workerIdx}`);
            updateCardHeaderTitle(card);
        } else {
            for (const cardState of getCards()) {
                if (cardState?.source?.startsWith('worker-latest:')) {
                    updateCardHeaderTitle(cardState);
                }
            }
        }
    }

    return {
        resolveSourceDisplay,
        updateCardHeaderTitle,
        updateWorkerOptionLabels,
    };
}
