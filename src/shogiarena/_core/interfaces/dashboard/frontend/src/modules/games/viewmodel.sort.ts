import { evaluateOutcome, STATUS_PRIORITY } from '@/modules/games/utils';
import { gameResultAbbreviation, gameResultDetailKey } from '@/modules/shared/utils/gameResult';
import type { GamesSortDirection, GamesSortKey, NormalizedGameRow } from '@/modules/games/types';
import {
    RESULT_DETAIL_INDEX,
    RESULT_DETAIL_ORDER,
    RESULT_OUTCOME_INDEX,
    RESULT_OUTCOME_ORDER,
} from './viewmodel.constants';

export type StatusPriority = (typeof STATUS_PRIORITY)[number];

function resolveStatusPriority(value: unknown): StatusPriority | null {
    const normalized = String(value ?? '')
        .trim()
        .toLowerCase();
    switch (normalized) {
        case 'running':
        case 'pending':
        case 'completed':
        case 'cancelled':
            return normalized as StatusPriority;
        default:
            return null;
    }
}

function resolveResultSortKey(row: NormalizedGameRow): string {
    const outcomeInfo = evaluateOutcome(row.status, row.game_result);
    const outcomeIdx = RESULT_OUTCOME_INDEX.get(outcomeInfo.outcome) ?? RESULT_OUTCOME_ORDER.length;
    const detailRaw = typeof row.result_detail === 'string' ? row.result_detail : '';
    const detailKey = (detailRaw.trim() || gameResultDetailKey(row.game_result) || '').toLowerCase();
    const detailIdx = RESULT_DETAIL_INDEX.get(detailKey) ?? RESULT_DETAIL_ORDER.length;
    const label = typeof row.result_label === 'string' ? row.result_label.trim().toLowerCase() : '';
    const abbrRaw = typeof row.result_abbr === 'string' ? row.result_abbr.trim() : '';
    const abbr = (abbrRaw || gameResultAbbreviation(row.game_result) || '').toLowerCase();
    const gameResult = (row.game_result ?? '').toLowerCase();
    return `${outcomeIdx.toString().padStart(2, '0')}|${detailIdx.toString().padStart(2, '0')}|${label}|${abbr}|${detailKey}|${gameResult}`;
}

function compareStrings(a: unknown, b: unknown): number {
    return String(a).localeCompare(String(b), undefined, { numeric: true, sensitivity: 'base' });
}

export function sortRows(rows: NormalizedGameRow[], sortKey: GamesSortKey, sortDirection: GamesSortDirection): void {
    const direction = sortDirection === 'desc' ? -1 : 1;
    const timeSortValue = (value: string | null): number => {
        if (typeof value === 'string' && value) {
            const parsed = Date.parse(value);
            if (!Number.isNaN(parsed)) {
                return parsed;
            }
        }
        return direction === -1 ? Number.MIN_SAFE_INTEGER : Number.MAX_SAFE_INTEGER;
    };
    rows.sort((a, b) => {
        const av = (() => {
            switch (sortKey) {
                case 'order':
                    return typeof a.display_order === 'number' ? a.display_order : Number.MAX_SAFE_INTEGER;
                case 'game':
                    return a.game_id ?? '';
                case 'black':
                    return a.black ?? '';
                case 'white':
                    return a.white ?? '';
                case 'result':
                    return resolveResultSortKey(a);
                case 'status': {
                    const status = resolveStatusPriority(a.status);
                    const idx = status ? STATUS_PRIORITY.indexOf(status) : -1;
                    return idx === -1 ? STATUS_PRIORITY.length : idx;
                }
                case 'moves': {
                    const fallback = direction === -1 ? Number.MIN_SAFE_INTEGER : Number.MAX_SAFE_INTEGER;
                    return typeof a.total_plies === 'number' && Number.isFinite(a.total_plies)
                        ? a.total_plies
                        : fallback;
                }
                case 'started':
                    return timeSortValue(a.started_at);
                case 'ended':
                    return timeSortValue(a.ended_at);
                default:
                    return 0;
            }
        })();
        const bv = (() => {
            switch (sortKey) {
                case 'order':
                    return typeof b.display_order === 'number' ? b.display_order : Number.MAX_SAFE_INTEGER;
                case 'game':
                    return b.game_id ?? '';
                case 'black':
                    return b.black ?? '';
                case 'white':
                    return b.white ?? '';
                case 'result':
                    return resolveResultSortKey(b);
                case 'status': {
                    const status = resolveStatusPriority(b.status);
                    const idx = status ? STATUS_PRIORITY.indexOf(status) : -1;
                    return idx === -1 ? STATUS_PRIORITY.length : idx;
                }
                case 'moves': {
                    const fallback = direction === -1 ? Number.MIN_SAFE_INTEGER : Number.MAX_SAFE_INTEGER;
                    return typeof b.total_plies === 'number' && Number.isFinite(b.total_plies)
                        ? b.total_plies
                        : fallback;
                }
                case 'started':
                    return timeSortValue(b.started_at);
                case 'ended':
                    return timeSortValue(b.ended_at);
                default:
                    return 0;
            }
        })();

        if (typeof av === 'number' && typeof bv === 'number') {
            return (av - bv) * direction;
        }
        return compareStrings(av, bv) * direction;
    });
}
