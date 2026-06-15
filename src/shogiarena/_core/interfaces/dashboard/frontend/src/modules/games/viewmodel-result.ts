import { evaluateOutcome, RESULT_LABELS } from '@/modules/games/utils';
import { gameResultAbbreviation, gameResultDetailKey } from '@/modules/shared/utils/game-result';
import type { GameOutcomeInfo, NormalizedGameRow } from '@/modules/games/types';
import { RESULT_DETAIL_SUFFIXES, RESULT_DETAIL_SYMBOLS } from './viewmodel-constants';
import { formatDetailLabel, formatTitleCase } from './viewmodel-format';

export type ResultBadgeVariant = 'win' | 'draw' | 'paused' | 'running' | 'pending' | 'cancelled' | 'error';

export interface GameResultViewModel {
    readonly variant: ResultBadgeVariant;
    readonly shape: 'disc' | 'triangle' | null;
    readonly symbol: string;
    readonly display: string;
    readonly tooltip: string;
    readonly detailKey: string;
    readonly symbolEmpty: boolean;
}

function resolveBadgeVariant(outcome: GameOutcomeInfo, statusLower: string): ResultBadgeVariant {
    if (outcome.outcome === 'running') return 'running';
    if (statusLower === 'pending' || outcome.outcome === 'pending') return 'pending';
    if (outcome.outcome === 'cancelled') return 'cancelled';
    if (outcome.outcome === 'error') return 'error';
    if (outcome.outcome === 'paused') return 'paused';
    if (outcome.outcome === 'draw') return 'draw';
    return 'win';
}

function resolveBadgeDisplay(variant: ResultBadgeVariant, abbrRaw: string): string {
    const abbr = abbrRaw.trim().toUpperCase();
    switch (variant) {
        case 'running':
            return '';
        case 'pending':
            return abbr || '';
        case 'cancelled':
            return '';
        case 'error':
            return abbr || 'ERR';
        case 'paused':
            return abbr || 'P';
        case 'draw':
            return abbr || 'DR';
        default:
            return abbr || '—';
    }
}

function resolveOutcomeSymbol(outcome: GameOutcomeInfo['outcome'], detailKey: string, abbrRaw: string): string {
    const abbr = abbrRaw.trim().toUpperCase();
    if (outcome === 'black-win' || outcome === 'white-win') {
        return RESULT_DETAIL_SYMBOLS[detailKey as keyof typeof RESULT_DETAIL_SYMBOLS] ?? '';
    }
    if (outcome === 'draw') {
        if (abbr.length >= 1) {
            return abbr.length === 1 ? abbr : abbr.charAt(0);
        }
        if (detailKey === 'repetition') return 'R';
        if (detailKey === 'max plies') return 'M';
        if (detailKey === 'impasse') return 'I';
        if (detailKey === 'timeout') return 'T';
    }
    if (outcome === 'paused') {
        if (abbr.length >= 1) {
            return abbr.length === 1 ? abbr : abbr.charAt(0);
        }
        if (detailKey === 'paused') return 'P';
        return 'P';
    }
    return abbr.length >= 1 ? (abbr.length === 1 ? abbr : abbr.charAt(0)) : '';
}

export function buildResultViewModel(normalized: NormalizedGameRow, statusLower: string): GameResultViewModel {
    const outcome = evaluateOutcome(normalized.status, normalized.game_result);
    const detailRaw = typeof normalized.result_detail === 'string' ? normalized.result_detail : '';
    const detailTrim = detailRaw.trim() || gameResultDetailKey(normalized.game_result) || '';
    const detailKey = detailTrim.toLowerCase();
    const abbrRaw = typeof normalized.result_abbr === 'string' ? normalized.result_abbr : '';
    const resolvedAbbr = abbrRaw.trim() || gameResultAbbreviation(normalized.game_result) || '';
    const labelRaw = typeof normalized.result_label === 'string' ? normalized.result_label : '';
    const labelTrim = labelRaw.trim();
    const fallbackLabel = RESULT_LABELS[outcome.outcome] ?? formatTitleCase(statusLower) ?? '';

    const detailLabel = detailTrim ? formatDetailLabel(detailTrim) : '';
    const detailSuffix = detailKey ? (RESULT_DETAIL_SUFFIXES[detailKey] ?? '') : '';

    const baseFallback = fallbackLabel || formatTitleCase(statusLower) || '';
    let tooltipLabel = labelTrim;
    if (!tooltipLabel) {
        let composed = baseFallback;
        if (composed) {
            if (detailSuffix && !composed.toLowerCase().includes(detailSuffix.replace(/^by\s+/i, '').toLowerCase())) {
                composed = `${composed} ${detailSuffix}`;
            } else if (
                detailLabel &&
                detailLabel !== composed &&
                !composed.toLowerCase().includes(detailLabel.toLowerCase()) &&
                statusLower !== 'pending' &&
                outcome.outcome !== 'running'
            ) {
                composed = `${composed} (${detailLabel})`;
            }
        }
        tooltipLabel = composed;
    }

    tooltipLabel = tooltipLabel.trim();
    if (!tooltipLabel) {
        tooltipLabel = '—';
    }

    const variant = resolveBadgeVariant(outcome, statusLower);
    const detailSymbol = resolveOutcomeSymbol(outcome.outcome, detailKey, resolvedAbbr);
    const badgeDisplay = resolveBadgeDisplay(variant, resolvedAbbr);

    if (variant === 'win') {
        return {
            variant,
            shape: 'disc',
            symbol: detailSymbol,
            display: detailSymbol,
            tooltip: tooltipLabel,
            detailKey,
            symbolEmpty: !detailSymbol,
        } satisfies GameResultViewModel;
    }

    if (variant === 'draw' || variant === 'paused') {
        const display = detailSymbol || badgeDisplay || '—';
        return {
            variant,
            shape: 'triangle',
            symbol: detailSymbol,
            display,
            tooltip: tooltipLabel,
            detailKey,
            symbolEmpty: false,
        } satisfies GameResultViewModel;
    }

    return {
        variant,
        shape: null,
        symbol: badgeDisplay,
        display: badgeDisplay,
        tooltip: tooltipLabel,
        detailKey,
        symbolEmpty: false,
    } satisfies GameResultViewModel;
}
