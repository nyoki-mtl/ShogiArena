import { evaluateOutcome } from '@/modules/games/utils';
import type { GameOutcomeInfo, NormalizedGameRow } from '@/modules/games/types';
import { MOVES_PLACEHOLDER, TIME_PLACEHOLDER } from './viewmodel.constants';
import { formatTimestamp } from './viewmodel.format';
import { buildResultViewModel, type GameResultViewModel } from './viewmodel.result';

export { sortRows } from './viewmodel.sort';
export type { GameResultViewModel, ResultBadgeVariant } from './viewmodel.result';

export type StatusIndicatorVariant = 'pending' | 'running' | 'completed' | 'cancelled';

export interface GameRowViewModel {
    readonly statusLower: string;
    readonly outcome: GameOutcomeInfo;
    readonly indicatorVariant: StatusIndicatorVariant | null;
    readonly indicatorLabel: string | null;
    readonly result: GameResultViewModel;
    readonly movesLabel: string;
    readonly started: { label: string; title: string | null; isEmpty: boolean };
    readonly ended: { label: string; title: string | null; isEmpty: boolean };
}

export function buildGameRowViewModel(normalized: NormalizedGameRow): GameRowViewModel {
    const statusLower = String(normalized.status ?? '')
        .trim()
        .toLowerCase();
    const outcome = evaluateOutcome(normalized.status, normalized.game_result);

    const indicatorVariant: StatusIndicatorVariant | null = (() => {
        if (outcome.outcome === 'running' || statusLower === 'running') return 'running';
        if (statusLower === 'pending' || outcome.outcome === 'pending') return 'pending';
        if (statusLower === 'cancelled' || outcome.outcome === 'cancelled') return 'cancelled';
        if (!['pending', 'running'].includes(outcome.outcome)) return 'completed';
        return null;
    })();

    const indicatorLabel = (() => {
        switch (indicatorVariant) {
            case 'running':
                return 'Running';
            case 'pending':
                return 'Pending';
            case 'completed':
                return 'Completed';
            case 'cancelled':
                return 'Cancelled';
            default:
                return null;
        }
    })();

    const result = buildResultViewModel(normalized, statusLower);
    const started = formatTimestamp(normalized.started_at);
    const ended = formatTimestamp(normalized.ended_at);

    const movesLabel =
        Number.isFinite(normalized.total_plies) && Number(normalized.total_plies) >= 0
            ? String(normalized.total_plies)
            : MOVES_PLACEHOLDER;

    return {
        statusLower,
        outcome,
        indicatorVariant,
        indicatorLabel,
        result,
        movesLabel,
        started: {
            label: started.label ?? TIME_PLACEHOLDER,
            title: started.title,
            isEmpty: !started.label,
        },
        ended: {
            label: ended.label ?? TIME_PLACEHOLDER,
            title: ended.title,
            isEmpty: !ended.label,
        },
    } satisfies GameRowViewModel;
}
